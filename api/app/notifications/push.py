"""Private workspace push subscriptions and capped, revalidated delivery jobs."""
from datetime import datetime
import json
import os
import re
from typing import Literal
import uuid

from fastapi import APIRouter, Depends
from pydantic import Field
import httpx

from api.app import security as sec
from api.app.contracts import Contract
from api.app.notifications.push_transport import enabled, push_endpoint, deliver_to
from api.app.notifications.service import BD, KINDS, claim, deliver_after, pref, relevant
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router=APIRouter(prefix='/api/notifications')
PUSH_KINDS={'new_device','approval_needed','cash_breach','bill_due','customer_overdue','morning_briefing','market_risk'}


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS push_subscriptions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,business_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('webpush','fcm')),endpoint_hash TEXT NOT NULL UNIQUE,subscription_json TEXT NOT NULL,
        key_tag TEXT NOT NULL,created_at INTEGER NOT NULL,revoked_at INTEGER);
      CREATE INDEX IF NOT EXISTS push_user ON push_subscriptions(business_id,user_id,revoked_at);
      CREATE TABLE IF NOT EXISTS push_delivery_days(business_id TEXT NOT NULL,user_id TEXT NOT NULL,day TEXT NOT NULL,
        signal TEXT NOT NULL,PRIMARY KEY(business_id,user_id,day,signal));
      CREATE TABLE IF NOT EXISTS push_daily_signals(business_id TEXT NOT NULL,user_id TEXT NOT NULL,day TEXT NOT NULL,
        dedupe TEXT NOT NULL,PRIMARY KEY(business_id,user_id,day,dedupe));
    """)


@router.get('/push-config')
def config(actor=Depends(principal)):
    return dict(webpush=enabled('webpush'),fcm=enabled('fcm'),public_key=os.getenv('SAFELEDGER_VAPID_PUBLIC') if enabled('webpush') else None,simulation_only=True)


class Subscription(Contract):
    kind:Literal['webpush','fcm']
    token:str|None=Field(default=None,max_length=4096)
    endpoint:str|None=Field(default=None,max_length=2048)


@router.post('/subscriptions',status_code=201)
def subscribe(body:Subscription,actor=Depends(principal),db=Depends(db_connection)):
    if not enabled(body.kind):
        sec.fail(503,'PUSH_NOT_CONFIGURED')
    value=body.token if body.kind=='fcm' else body.endpoint
    try:
        valid=(bool(value and re.fullmatch(r'[A-Za-z0-9_:.~-]{20,4096}',value)) and not body.endpoint if body.kind=='fcm'
               else bool(value and push_endpoint(value)) and not body.token)
    except ValueError:
        valid=False
    if not valid:
        sec.fail(422,'INVALID_PUSH_SUBSCRIPTION')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role not in ('owner','approver','viewer','bookkeeper'):
            sec.fail(403,'MEMBER_REQUIRED')
        business=current.business_id
        if not db.execute("SELECT 1 FROM workspace_meta WHERE business_id=? AND kind='personal' AND archived_at IS NULL",(business,)).fetchone():
            sec.fail(403,'WORKSPACE_CLEARED')
        digest=sec.signature('push-endpoint',[business,current.user_id,body.kind,value])
        existing=db.execute('SELECT id,revoked_at FROM push_subscriptions WHERE endpoint_hash=?',(digest,)).fetchone()
        if existing and existing[1] is None:
            return dict(id=existing[0],simulation_only=True)
        if db.execute('SELECT count(*) FROM push_subscriptions WHERE business_id=? AND user_id=? AND revoked_at IS NULL',
                      (business,current.user_id)).fetchone()[0]>=10:
            sec.fail(409,'PUSH_SUBSCRIPTION_LIMIT')
        identity=uuid.uuid4().hex
        key=customer_key(db,business,create=True)
        blob=seal(key,value.encode(),identity)
        db.execute("""INSERT INTO push_subscriptions VALUES (?,?,?,?,?,?,?,?,NULL) ON CONFLICT(endpoint_hash) DO UPDATE SET
                      id=excluded.id,revoked_at=NULL,subscription_json=excluded.subscription_json,key_tag=excluded.key_tag,created_at=excluded.created_at""",
                   (identity,current.user_id,business,body.kind,digest,blob,key_tag(key),sec.now()))
        sec.audit(db,current,identity,'PUSH_SUBSCRIBED',dict(channel=body.kind))
    return dict(id=identity,simulation_only=True)


@router.post('/subscriptions/{identity}/revoke')
def revoke(identity:str,actor=Depends(principal),db=Depends(db_connection)):
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        row=db.execute('SELECT revoked_at FROM push_subscriptions WHERE business_id=? AND user_id=? AND id=?',
                       (current.business_id,current.user_id,identity)).fetchone()
        if not row:
            sec.fail(404,'PUSH_SUBSCRIPTION_NOT_FOUND')
        if row[0] is None:
            db.execute("UPDATE push_subscriptions SET revoked_at=?,subscription_json='' WHERE id=?",(sec.now(),identity))
            sec.audit(db,current,identity,'PUSH_REVOKED',{})
    return dict(simulation_only=True)


def queue(db,business,kind,record,period,user,at,quiet):
    if kind not in PUSH_KINDS or not (enabled('webpush') or enabled('fcm')):
        return
    links=db.execute('SELECT id,kind FROM push_subscriptions WHERE business_id=? AND user_id=? AND revoked_at IS NULL',(business,user)).fetchall()
    if not links:
        return
    ready=at if kind=='new_device' else deliver_after(at,*quiet)
    day=str(datetime.fromtimestamp(ready,BD).date())
    issue=sec.signature('push-signal',[kind,record,period])
    distinct=db.execute('SELECT count(*) FROM push_daily_signals WHERE business_id=? AND user_id=? AND day=?',(business,user,day)).fetchone()[0]
    if distinct>=3:
        return
    fresh=db.execute('INSERT OR IGNORE INTO push_daily_signals VALUES(?,?,?,?) RETURNING dedupe',(business,user,day,issue)).fetchone()
    if not fresh:
        return
    for identity,channel in links:
        if not enabled(channel):
            continue
        key=sec.signature('notification-key',[business,kind,record,user,channel,identity,period])
        payload=sec.canonical(dict(kind=kind,record=record,subscription=identity,signal=issue,digest=distinct==2))
        db.execute("""INSERT INTO notification_jobs(id,business_id,dedupe_key,recipient_user_id,channel,payload_json,run_after)
                      VALUES (?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
                   (uuid.uuid4().hex,business,key,user,channel,payload,ready))


def next_delivery(db,business,at):
    with sec.transaction(db):
        row=db.execute("""SELECT id,channel,recipient_user_id,payload_json,attempts FROM notification_jobs WHERE business_id=?
                          AND channel IN ('fcm','webpush') AND ((status='queued' AND run_after<=?)
                          OR (status='sending' AND claimed_until<=?)) ORDER BY run_after,id LIMIT 1""",(business,at,at)).fetchone()
        if not row or not claim(db,row[0],at):
            return None
        job,channel,user,raw,attempts=row
        payload=json.loads(raw);ref=payload['subscription'];kind=payload['kind']
        sub=db.execute("""SELECT s.subscription_json,s.key_tag,u.role FROM push_subscriptions s JOIN security_users u
                          ON u.user_id=s.user_id AND u.business_id=s.business_id JOIN workspace_meta w ON w.business_id=s.business_id
                          WHERE s.id=? AND s.business_id=? AND s.user_id=? AND s.revoked_at IS NULL AND s.kind=?
                            AND u.active=1 AND w.kind='personal' AND w.archived_at IS NULL""",(ref,business,user,channel)).fetchone()
        key=customer_key(db,business)
        if (not enabled(channel) or not sub or not key or sub[1]!=key_tag(key) or sub[2] not in KINDS[kind][0] or
                attempts>=3 or not relevant(db,business,kind,payload['record'],at)):
            db.execute("UPDATE notification_jobs SET status='cancelled',claimed_until=NULL WHERE id=?",(job,))
            return ()
        _,start,end=pref(db,business,user)
        later=at if kind=='new_device' else deliver_after(at,start,end)
        if later!=at:
            db.execute("UPDATE notification_jobs SET status='queued',claimed_until=NULL,run_after=?,attempts=attempts-1 WHERE id=?",(later,job))
            return ()
        day=str(datetime.fromtimestamp(at,BD).date())
        signal=payload['signal']
        if not db.execute('SELECT 1 FROM push_delivery_days WHERE business_id=? AND user_id=? AND day=? AND signal=?',
                          (business,user,day,signal)).fetchone():
            count=db.execute('SELECT count(*) FROM push_delivery_days WHERE business_id=? AND user_id=? AND day=?',(business,user,day)).fetchone()[0]
            if count>=3:
                db.execute("UPDATE notification_jobs SET status='cancelled',claimed_until=NULL WHERE id=?",(job,))
                return ()
            db.execute('INSERT INTO push_delivery_days VALUES (?,?,?,?)',(business,user,day,signal))
        return job,ref,channel,unseal(key,sub[0],ref).decode(),attempts+1


def deliver(db,business,at):
    if not (enabled('webpush') or enabled('fcm')):
        return
    for _ in range(6):
        row=next_delivery(db,business,at)
        if row is None:
            break
        if not row:
            continue
        job,ref,channel,target,attempts=row
        try:
            status=deliver_to(channel,target)
        except (httpx.HTTPError,ValueError,KeyError,TypeError,AttributeError):
            status=503
        success=200<=status<300
        gone=status in (404,410)
        next_status='sent' if success else ('failed' if attempts>=3 or gone else 'queued')
        with sec.transaction(db):
            if gone:
                db.execute("UPDATE push_subscriptions SET revoked_at=?,subscription_json='' WHERE id=?",(at,ref))
            db.execute("""UPDATE notification_jobs SET status=?,run_after=?,sent_at=?,claimed_until=NULL,last_error=?
                          WHERE id=? AND status='sending' AND attempts=?""",
                       (next_status,at+60*2**attempts,at if success else None,None if success else 'PUSH_DELIVERY_FAILED',job,attempts))


def urgent_delivery(business):
    from contextlib import closing
    from api.app.database import connect
    with closing(connect()) as db:
        deliver(db,business,sec.now())
