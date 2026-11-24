"""Private in-app notifications. No external dispatch and no amounts or names in jobs."""
from datetime import datetime, timedelta
import json
import uuid
from zoneinfo import ZoneInfo

from api.app import security as sec

KINDS = {
    'approval_needed': (('approver',), 'Approvals'),
    'new_device': (('owner', 'approver', 'viewer', 'bookkeeper'), 'Settings'),
    'payment_shared': (('owner',), 'Invoices'),
    'payment_profile_changed': (('owner', 'approver', 'viewer', 'bookkeeper'), 'Invoices'),
    'duplicate_bill': (('owner',), 'Invoices'),
    'bill_due': (('owner',), 'Invoices'),
    'customer_overdue': (('owner',), 'Invoices'),
    'cash_breach': (('owner', 'approver'), 'Cash flow'),
    'boishakh_planning': (('owner',), 'Cash flow'),
    'eid_planning': (('owner',), 'Cash flow'),
    'collections_review': (('owner',), 'Overview'),
    'morning_briefing': (('owner',), 'Overview'),
    'market_risk': (('owner',), 'Overview'),
}
BD = ZoneInfo('Asia/Dhaka')


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS notification_prefs(
      user_id TEXT NOT NULL,business_id TEXT NOT NULL,alert_kind TEXT NOT NULL,channels_json TEXT NOT NULL,
      quiet_start_min INTEGER NOT NULL DEFAULT 1260,quiet_end_min INTEGER NOT NULL DEFAULT 480,
      privacy_mode INTEGER NOT NULL DEFAULT 1,language TEXT,updated_at INTEGER NOT NULL,
      PRIMARY KEY(user_id,business_id,alert_kind));
    CREATE TABLE IF NOT EXISTS notification_jobs(
      id TEXT PRIMARY KEY,business_id TEXT NOT NULL,dedupe_key TEXT NOT NULL UNIQUE,recipient_user_id TEXT NOT NULL,
      channel TEXT NOT NULL,payload_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued'
        CHECK(status IN ('queued','sending','sent','failed','cancelled')),attempts INTEGER NOT NULL DEFAULT 0,
      run_after INTEGER NOT NULL,claimed_until INTEGER,sent_at INTEGER,last_error TEXT,read_at INTEGER);
    CREATE INDEX IF NOT EXISTS notification_jobs_due ON notification_jobs(status,run_after);
    CREATE INDEX IF NOT EXISTS notification_jobs_inbox ON notification_jobs(business_id,recipient_user_id,status,sent_at);
    CREATE TABLE IF NOT EXISTS notification_rules_state(business_id TEXT PRIMARY KEY,last_slot INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS known_devices(account_id TEXT NOT NULL,device_hash TEXT NOT NULL,
      seen_at INTEGER NOT NULL,PRIMARY KEY(account_id,device_hash));
    """)


def pref(db, business, user):
    row = db.execute("""SELECT channels_json,quiet_start_min,quiet_end_min FROM notification_prefs
                        WHERE user_id=? AND business_id=? AND alert_kind='all'""", (user, business)).fetchone()
    return (True, 1260, 480) if row is None else ('inapp' in json.loads(row[0]), row[1], row[2])


def deliver_after(now, start, end):
    """Return now in the delivery window or the next end of local quiet hours."""
    local = datetime.fromtimestamp(now, BD)
    minute = 60 * local.hour + local.minute
    quiet = (start <= minute < end) if start <= end else (minute >= start or minute < end)
    if not quiet:
        return now
    wake = local.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if wake <= local:
        wake += timedelta(days=1)
    return int(wake.timestamp())


def claim(db, identity, at):
    return db.execute("""UPDATE notification_jobs SET status='sending',claimed_until=?,attempts=attempts+1
                         WHERE id=? AND ((status='queued' AND run_after<=?) OR
                         (status='sending' AND claimed_until<=?)) RETURNING id""",
                      (at+120, identity, at, at)).fetchone() is not None


def relevant(db, business, kind, record, at):
    if kind == 'cash_breach':
        return bool(db.execute("SELECT 1 FROM radar_state WHERE business_id=? AND level='alert'", (business,)).fetchone())
    if kind == 'collections_review':
        from api.app.feature_flags import workspace_status
        today = datetime.fromtimestamp(at, BD).date().isoformat()
        return bool(record == today and workspace_status(db, business, '/api/copilot/agent-review')[2] and
                    db.execute('SELECT 1 FROM receivable_balances WHERE business_id=? AND open_minor>0 AND due_on<? LIMIT 1',
                               (business, today)).fetchone())
    if kind == 'customer_overdue':
        return bool(db.execute("""SELECT 1 FROM receivable_balances WHERE business_id=? AND customer_id=?
                                  AND open_minor>0 AND due_on<? LIMIT 1""",
                               (business, record, datetime.fromtimestamp(at, BD).date().isoformat())).fetchone())
    if kind == 'approval_needed':
        row = db.execute('SELECT state,payload FROM proposals WHERE business_id=? AND proposal_id=?',
                         (business, record)).fetchone()
        return bool(row and row[0] == 'AWAITING_APPROVAL' and json.loads(row[1])['expires_at'] > at)
    if kind == 'bill_due':
        ds = sec.runtime_dataset(db, business)
        return any(i.invoice_id == record and i.status == 'open' for i in ds.invoices)
    return True


def dispatch(db, identity, at):
    if not claim(db, identity, at):
        return
    business, user, raw = db.execute('SELECT business_id,recipient_user_id,payload_json FROM notification_jobs WHERE id=?',
                                     (identity,)).fetchone()
    payload = json.loads(raw)
    kind = payload['kind']
    role = db.execute('SELECT role FROM effective_security_users WHERE business_id=? AND user_id=? AND active=1',
                      (business, user)).fetchone()
    enabled, start, end = pref(db, business, user)
    ready = at if kind == 'new_device' else deliver_after(at, start, end)
    if not enabled or not role or role[0] not in KINDS[kind][0] or not relevant(db, business, kind, payload['record'], at):
        db.execute("UPDATE notification_jobs SET status='cancelled',claimed_until=NULL WHERE id=?", (identity,))
    elif ready != at:
        db.execute("UPDATE notification_jobs SET status='queued',run_after=?,claimed_until=NULL WHERE id=?", (ready, identity))
    else:
        db.execute("UPDATE notification_jobs SET status='sent',sent_at=?,claimed_until=NULL WHERE id=?", (at, identity))


def enqueue(db, business, kind, record_id, *, at=None, recipients=None, period='event'):
    """Create in-app jobs inside the transaction that produced the source event."""
    if not db.in_transaction:
        raise RuntimeError('Enqueue inside the source transaction')
    at = sec.now() if at is None else at
    roles, page = KINDS[kind]
    members = db.execute('SELECT user_id,role FROM effective_security_users WHERE business_id=? AND active=1', (business,)).fetchall()
    for user, role in members:
        if role not in roles or (recipients is not None and user not in recipients):
            continue
        enabled, start, end = pref(db, business, user)
        from api.app.notifications import push
        push.queue(db,business,kind,record_id,period,user,at,(start,end))
        if not enabled:
            continue
        key = sec.signature('notification-key', [business, kind, record_id, user, 'inapp', period])
        run_after = at if kind == 'new_device' else deliver_after(at, start, end)
        payload = sec.canonical(dict(kind=kind, page=page, record=record_id))
        identity = uuid.uuid4().hex
        inserted = db.execute("""INSERT INTO notification_jobs
            (id,business_id,dedupe_key,recipient_user_id,channel,payload_json,run_after)
            VALUES (?,?,?,?,'inapp',?,?) ON CONFLICT(dedupe_key) DO NOTHING RETURNING id""",
            (identity, business, key, user, payload, run_after)).fetchone()
        if inserted and run_after <= at:
            dispatch(db, identity, at)


def duplicate_notice(db, business, dataset, new_id=None):
    from api.app.services.finance import analyze_duplicates
    groups = [flag for flag in analyze_duplicates(dataset) if new_id is None or new_id in flag['invoice_ids']]
    if groups:
        # One inbox item for the import/capture even when a file has many duplicates.
        identity = sec.signature('duplicate-review', sorted({s for g in groups for s in g['source_ids']}))
        enqueue(db, business, 'duplicate_bill', identity)
