"""Member inbox and authenticated scheduler entry point."""
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from api.app import security as sec
from api.app.contracts import Contract
from api.app.notifications import email_alerts
from api.app.notifications.scheduler import tick, tick_business
from api.app.notifications.service import pref
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api')
router.include_router(email_alerts.router)


def inbox_view(db, actor):
    rows = db.execute("""SELECT id,payload_json,sent_at,read_at FROM notification_jobs
                         WHERE business_id=? AND recipient_user_id=? AND channel='inapp' AND status='sent'
                         ORDER BY sent_at DESC,rowid DESC LIMIT 100""", (actor.business_id,actor.user_id))
    items = [dict(id=identity,kind=json.loads(payload)['kind'],page=json.loads(payload)['page'],
                  sent_at=sent,read=read is not None) for identity,payload,sent,read in rows]
    enabled, start, end = pref(db, actor.business_id, actor.user_id)
    return dict(items=items, enabled=enabled, quiet_start_min=start, quiet_end_min=end,
                time_zone='Asia/Dhaka', simulation_only=True)


@router.get('/notifications')
def inbox(actor=Depends(principal), db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        return inbox_view(db, sec.recheck(db, actor))


@router.post('/notifications/refresh')
def refresh(actor=Depends(principal), db=Depends(db_connection)):
    sec.budget(db, 'refresh-alerts:'+actor.user_id, 4)
    tick_business(db, actor.business_id, sec.now())
    return inbox_view(db, sec.recheck(db,actor))


@router.post('/notifications/{notification_id}/read')
def read(notification_id: str, actor=Depends(principal), db=Depends(db_connection)):
    with sec.transaction(db):
        user=sec.recheck(db, actor)
        matched=db.execute("""UPDATE notification_jobs SET read_at=COALESCE(read_at,?) WHERE id=? AND business_id=?
                              AND recipient_user_id=? AND channel='inapp' AND status='sent' RETURNING id""",
                           (sec.now(),notification_id,user.business_id,user.user_id)).fetchone()
        if not matched:
            sec.fail(404, 'NOTIFICATION_NOT_FOUND')
    return dict(simulation_only=True)


class Preferences(Contract):
    enabled: bool = Field(strict=True)
    quiet_start_min: int = Field(strict=True, ge=0, lt=1440)
    quiet_end_min: int = Field(strict=True, ge=0, lt=1440)


@router.post('/notifications/preferences')
def preferences(body: Preferences, actor=Depends(principal), db=Depends(db_connection)):
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        db.execute("""INSERT INTO notification_prefs
            (user_id,business_id,alert_kind,channels_json,quiet_start_min,quiet_end_min,updated_at)
            VALUES (?,?,'all',?,?,?,?) ON CONFLICT(user_id,business_id,alert_kind) DO UPDATE SET
            channels_json=excluded.channels_json,quiet_start_min=excluded.quiet_start_min,
            quiet_end_min=excluded.quiet_end_min,updated_at=excluded.updated_at""",
            (current.user_id,current.business_id,'["inapp"]' if body.enabled else '[]',
             body.quiet_start_min,body.quiet_end_min,sec.now()))
        if not body.enabled:
            db.execute("""UPDATE notification_jobs SET status='cancelled',claimed_until=NULL
                          WHERE business_id=? AND recipient_user_id=? AND channel='inapp' AND status IN ('queued','sending')""",
                       (current.business_id,current.user_id))
    return dict(simulation_only=True)


def signature(secret, ts):
    return hmac.new(secret.encode(), f'{ts}.POST./api/internal/tick'.encode(), hashlib.sha256).hexdigest()


@router.post('/internal/tick')
def scheduler_tick(request: Request, db=Depends(db_connection)):
    secret = os.getenv('CAPZORYX_TICK_SECRET', '')
    if len(secret)<32:
        sec.fail(503,'SCHEDULER_NOT_CONFIGURED')
    ts = request.headers.get('x-capzoryx-timestamp','')
    proof = request.headers.get('x-capzoryx-signature','')
    at = sec.now()
    if (not ts.isascii() or not ts.isdecimal() or len(ts)>12 or abs(at-int(ts))>300 or
            len(proof)!=64 or not proof.isascii() or not hmac.compare_digest(signature(secret,ts),proof)):
        sec.fail(401,'INVALID_SCHEDULER_SIGNATURE')
    return tick(db, at)
