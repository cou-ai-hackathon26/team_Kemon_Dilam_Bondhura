"""Verified-member bill reminders, one private digest per seat and Dhaka day."""
from datetime import datetime
import hmac
import json
import os
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import mailer, security as sec
from api.app.contracts import Contract, RecordId
from api.app.notifications.service import BD, deliver_after, pref
from api.app.routes import db_connection, principal
from api.app.services.finance import invoice_key

router = APIRouter(prefix='/notifications/bill-email')
BATCH = 2
QUEUE_BATCH = 8


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS bill_email_subscriptions(
        id TEXT PRIMARY KEY,business_id TEXT NOT NULL,user_id TEXT NOT NULL,address_tag TEXT NOT NULL,
        UNIQUE(business_id,user_id));
      CREATE INDEX IF NOT EXISTS bill_email_jobs ON notification_jobs(business_id,run_after)
        WHERE channel='bill_email' AND status='queued';
      CREATE TRIGGER IF NOT EXISTS bill_email_on_archive AFTER UPDATE OF archived_at ON workspace_meta
        WHEN NEW.archived_at IS NOT NULL BEGIN
          DELETE FROM bill_email_subscriptions WHERE business_id=NEW.business_id;
        END;
      CREATE TRIGGER IF NOT EXISTS bill_email_on_member_removal AFTER UPDATE OF active ON security_users
        WHEN NEW.active=0 BEGIN DELETE FROM bill_email_subscriptions WHERE user_id=NEW.user_id; END;
      CREATE TRIGGER IF NOT EXISTS bill_email_on_address_change AFTER UPDATE OF verified,email ON account_emails
        WHEN NEW.verified=0 OR NEW.email<>OLD.email BEGIN DELETE FROM bill_email_subscriptions
        WHERE user_id IN (SELECT user_id FROM account_members WHERE account_id=OLD.account_id); END;
      CREATE TRIGGER IF NOT EXISTS bill_email_on_address_delete AFTER DELETE ON account_emails BEGIN
        DELETE FROM bill_email_subscriptions WHERE user_id IN
        (SELECT user_id FROM account_members WHERE account_id=OLD.account_id); END;
      CREATE TRIGGER IF NOT EXISTS bill_email_on_revoke AFTER DELETE ON bill_email_subscriptions BEGIN
        UPDATE notification_jobs SET status='cancelled' WHERE business_id=OLD.business_id
        AND recipient_user_id=OLD.user_id AND channel='bill_email' AND status='queued'; END;
    """)


def website():
    url=os.getenv('SAFELEDGER_PUBLIC_URL','').strip().rstrip('/')
    try:
        parsed=urlsplit(url)
        if parsed.scheme=='https' and parsed.hostname and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            return url
    except ValueError:
        pass
    return None


def configured():
    return bool(website() and mailer.configured() and (mailer.provider()=='relay' or mailer.sender()[1]))


def verified_address(db,business,user):
    row=db.execute("""SELECT e.email FROM account_members m JOIN account_emails e ON e.account_id=m.account_id
        JOIN effective_security_users u ON u.user_id=m.user_id AND u.business_id=m.business_id
        JOIN workspace_meta w ON w.business_id=m.business_id WHERE m.business_id=? AND m.user_id=?
        AND e.verified=1 AND u.active=1 AND u.role IN ('owner','approver','bookkeeper','viewer')
        AND w.kind='personal' AND w.archived_at IS NULL""",(business,user)).fetchone()
    return row[0] if row else None


def recipient_tag(business,user,address):
    return sec.signature('bill-email-address',[business,user,address.strip().casefold()])


def status(db,current):
    business,user=current.business_id,current.user_id
    address=verified_address(db,business,user)
    row=db.execute('SELECT address_tag FROM bill_email_subscriptions WHERE business_id=? AND user_id=?',
                   (business,user)).fetchone()
    return dict(configured=configured(),verified=bool(address),enabled=bool(row and address and
                row[0]==recipient_tag(business,user,address)),simulation_only=True)


@router.get('')
def settings(actor=Depends(principal),db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        current=sec.recheck(db,actor)
        return dict(status(db,current),business_id=current.business_id,user_id=current.user_id)


class Preference(Contract):
    enabled: bool=Field(strict=True)
    expected_business_id: RecordId
    expected_user_id: RecordId


@router.post('')
def update(body:Preference,actor=Depends(principal),db=Depends(db_connection)):
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        business,user=current.business_id,current.user_id
        if (business,user)!=(body.expected_business_id,body.expected_user_id):
            sec.fail(409,'DRAFT_WORKSPACE_MISMATCH')
        if body.enabled:
            if not configured():sec.fail(503,'BILL_EMAIL_UNAVAILABLE')
            address=verified_address(db,business,user)
            if not address:sec.fail(403,'VERIFIED_EMAIL_REQUIRED')
            tag=recipient_tag(business,user,address)
            db.execute("""INSERT INTO bill_email_subscriptions VALUES(?,?,?,?)
                ON CONFLICT(business_id,user_id) DO UPDATE SET id=excluded.id,address_tag=excluded.address_tag
                WHERE bill_email_subscriptions.address_tag<>excluded.address_tag""",(uuid.uuid4().hex,business,user,tag))
        else:
            db.execute('DELETE FROM bill_email_subscriptions WHERE business_id=? AND user_id=?',(business,user))
        return dict(status(db,current),business_id=business,user_id=user)


class Unsubscribe(Contract):
    token:str=Field(pattern=r'^[a-f0-9]{32}\.[a-f0-9]{64}$')


@router.post('/unsubscribe')
def unsubscribe(body:Unsubscribe, db=Depends(db_connection)):
    identity,proof=body.token.split('.')
    if not hmac.compare_digest(proof,sec.signature('bill-email-unsubscribe',identity)):
        sec.fail(404,'REMINDER_LINK_INVALID')
    with sec.transaction(db):
        db.execute('DELETE FROM bill_email_subscriptions WHERE id=?',(identity,))
    return dict(simulation_only=True)


def due_count(dataset,day):
    return len({invoice_key(i) for i in dataset.invoices if i.status=='open' and (i.due_on-day).days in (0,1,3,7)})


def queue(db,business,dataset,at):
    day=datetime.fromtimestamp(at,BD).date()
    if not configured() or not due_count(dataset,day):
        return
    prefix='bill-email/'+business+'/'
    suffix='/'+str(day)
    # The unique dedupe index makes repeated ticks skip already-considered seats.
    seats=db.execute("""SELECT id,user_id FROM bill_email_subscriptions s WHERE business_id=? AND NOT EXISTS
        (SELECT 1 FROM notification_jobs j WHERE j.dedupe_key=(? || s.user_id || ?))
        ORDER BY user_id LIMIT ?""",(business,prefix,suffix,QUEUE_BATCH)).fetchall()
    for identity,user in seats:
        _,start,end=pref(db,business,user)
        key=prefix+user+suffix
        # Subscription IDs rotate when an address changes. The daily key remains stable across opt-outs.
        db.execute("""INSERT INTO notification_jobs(id,business_id,dedupe_key,recipient_user_id,channel,payload_json,run_after)
          VALUES (?,?,?,?,'bill_email',?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
          (uuid.uuid4().hex,business,key,user,sec.canonical(dict(day=str(day),subscription=identity)),deliver_after(at,start,end)))


def next_email(db,business,at):
    with sec.transaction(db):
        row=db.execute("""SELECT id,recipient_user_id,payload_json FROM notification_jobs
            WHERE business_id=? AND channel='bill_email' AND status='queued' AND run_after<=?
            ORDER BY run_after,id LIMIT 1""",(business,at)).fetchone()
        if not row:
            return None
        job,user,raw=row
        payload=json.loads(raw)
        identity=payload['subscription']
        sub=db.execute('SELECT address_tag FROM bill_email_subscriptions WHERE id=? AND business_id=? AND user_id=?',
                       (identity,business,user)).fetchone()
        target=verified_address(db,business,user)
        today=datetime.fromtimestamp(at,BD).date()
        if not sub or not target or sub[0]!=recipient_tag(business,user,target) or payload['day']!=str(today):
            db.execute("UPDATE notification_jobs SET status='cancelled' WHERE id=?",(job,))
            return ()
        _,start,end=pref(db,business,user)
        ready=deliver_after(at,start,end)
        if ready!=at:
            db.execute('UPDATE notification_jobs SET run_after=? WHERE id=?',(ready,job))
            return ()
        count=due_count(sec.runtime_dataset(db,business),today)
        if not count:
            db.execute("UPDATE notification_jobs SET status='cancelled' WHERE id=?",(job,))
            return ()
        if not mailer.reserve(target,bulk=True):
            db.execute('UPDATE notification_jobs SET run_after=? WHERE id=?',(at+3600,job))
            return ()
        # No provider idempotency guarantee. Commit the attempt before handing mail to the transport.
        # A crash or an ambiguous provider failure cannot send the same day's digest a second time.
        db.execute("UPDATE notification_jobs SET status='sending',attempts=1 WHERE id=?",(job,))
        return job,target,count,identity


def deliver(db,business,at):
    if not configured():
        return
    base=website()
    for _ in range(BATCH):
        pending=next_email(db,business,at)
        if pending is None:
            break
        if not pending:
            continue
        job,address,count,identity=pending
        token=identity+'.'+sec.signature('bill-email-unsubscribe',identity)
        unit='bill needs' if count==1 else 'bills need'
        message=(f'{count} {unit} your review. Sign in to see the details.\n\n'
                 f'{base}/?view=activity\n\nTurn off bill reminder emails for this workspace:\n{base}/bill-reminders/unsubscribe#{token}')
        accepted=mailer.send(address,'Your Capzoryx bill reminder',message)
        with sec.transaction(db):
            db.execute("UPDATE notification_jobs SET status=?,sent_at=?,last_error=? WHERE id=? AND status='sending'",
                       ('sent' if accepted else 'failed',at if accepted else None,None if accepted else 'MAIL_NOT_ACCEPTED',job))
