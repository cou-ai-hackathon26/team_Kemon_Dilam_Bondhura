"""Background market monitoring and risk alerts.

A loop in the web process (kept awake by the uptime monitor) refreshes the public prices businesses watch, then checks
each business that allowed monitoring against its own costs. Levels:
  1 update   - a noticeable price move; shown in the app's market timeline only.
  2 warning  - the profit buffer is thinner than the owner's threshold, or the price fell sharply.
  3 critical - the price is at, below or within 1% of the real break-even (cost + selling costs).
Warnings and critical alerts go to the in-app inbox, push and Telegram when the owner allowed alerts; critical ones
ignore quiet hours. Each product keeps its last level, so the same alert is not repeated before its cooldown, and a
price that is out of date never raises an alert. Nothing is ever bought or sold automatically.
"""
import asyncio
import json
import logging
import os
import socket
import uuid
from contextlib import closing
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.app import security as sec
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/alerts')
log = logging.getLogger('capzoryx.monitor')
INTERVAL = int(os.getenv('CAPZORYX_MONITOR_SECONDS', '600'))
COOLDOWN = {1: 6 * 3600, 2: 24 * 3600, 3: 6 * 3600}
CRITICAL_CUSHION_BP = 100
HOLDER = f'{socket.gethostname()}-{os.getpid()}'


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS biz_alert_state(business_id TEXT NOT NULL, product_id TEXT NOT NULL, level INTEGER NOT NULL,
        ref_minor INTEGER NOT NULL, alerted_at INTEGER, PRIMARY KEY(business_id, product_id));
      CREATE TABLE IF NOT EXISTS biz_alerts(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, product_id TEXT NOT NULL,
        level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 3), data_enc TEXT NOT NULL, key_tag TEXT NOT NULL, created_at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS biz_alerts_recent ON biz_alerts(business_id, created_at);
      CREATE TABLE IF NOT EXISTS job_lease(name TEXT PRIMARY KEY, holder TEXT NOT NULL, until INTEGER NOT NULL);
    """)


def money(minor, currency):
    sign = '-' if minor < 0 else ''
    return f'{sign}{currency} {abs(minor) / 100:,.2f}'


def settings(consent):
    return (int(round(float(consent.get('warn_cushion_pct') or 5) * 100)), int(round(float(consent.get('move_pct') or 3) * 100)))


def classify(pos, previous_ref, warn_bp, move_bp):
    """(level, moved_bp) for a complete, market-priced position."""
    ref = pos['reference']['value_minor']
    moved = None if not previous_ref else round((ref - previous_ref) * 10000 / previous_ref)
    cushion = pos['cushion_bp']
    if pos['net_minor'] <= 0 or (cushion is not None and cushion < CRITICAL_CUSHION_BP):
        return 3, moved
    if (cushion is not None and cushion < warn_bp) or (moved is not None and moved <= -move_bp):
        return 2, moved
    if moved is not None and abs(moved) >= 100:
        return 1, moved
    return 0, moved


def message(level, pos, currency, moved, recovered=False):
    m = lambda v: money(v, currency)
    ref = pos['reference']
    qty = pos.get('quantity')
    lines = [f"Purchase cost: {m(pos['cost_minor'])} per {pos['unit'] or 'unit'}",
             f"Current reference price: {m(ref['value_minor'])} ({ref['source']}, {str(ref['observed_at'])[:10]})",
             f"Estimated selling costs: {m(pos['expense_minor'])}",
             f"Estimated net position: {m(pos['net_minor'])} per unit" + (f", {m(pos['total_net_minor'])} on {qty} {pos['unit']}" if qty and pos.get('total_net_minor') is not None else ''),
             f"Break-even price: {m(pos['break_even_minor'])}"]
    if moved is not None:
        lines.insert(2, f"Change since the last check: {moved / 100:+.1f}%")
    if recovered:
        title = f"{pos['name']} is back above break-even"
        advice = 'The earlier warning no longer applies at this price.'
    elif level == 3:
        title = f"CRITICAL: {pos['name']} profitability at risk"
        advice = ('This price is at or past your estimated net break-even. Check the price buyers will actually pay you before '
                  'selling, and compare selling now with holding. Prices can keep moving either way; nothing is sold automatically.')
    elif level == 2:
        title = f"Warning: {pos['name']} profit buffer is thin" if pos['cushion_bp'] is not None and pos['cushion_bp'] >= 0 else f"Warning: {pos['name']}"
        advice = f"The price can fall only {max(pos['cushion_bp'] or 0, 0) / 100:.1f}% more before you make a loss. Review your break-even and selling options."
    else:
        title = f"{pos['name']}: price moved {moved / 100:+.1f}%"
        advice = 'For your information; your position is still above the warning level.'
    return dict(title=title, lines=lines, advice=advice)


def store_alert(db, business, pos, level, text, at):
    identity = uuid.uuid4().hex
    key = customer_key(db, business, create=True)
    data = dict(text, product=pos['name'], level=level, net_minor=pos['net_minor'], cushion_bp=pos['cushion_bp'],
                reference=pos['reference'], break_even_minor=pos['break_even_minor'])
    db.execute('INSERT INTO biz_alerts VALUES (?,?,?,?,?,?,?)',
               (identity, business, pos['id'], level, seal(key, sec.canonical(data).encode(), identity + '/alert'), key_tag(key), at))
    return identity


def queue_channels(db, business, identity, level, at):
    """Inbox and push through the existing queue, plus a Telegram job that carries the alert id."""
    from api.app.notifications.service import deliver_after, enqueue, pref
    enqueue(db, business, 'market_risk', identity, at=at)
    owners = db.execute("""SELECT t.user_id FROM telegram_links t JOIN security_users u ON u.user_id=t.user_id AND u.business_id=t.business_id
                           WHERE t.business_id=? AND t.confirmed_at IS NOT NULL AND u.role='owner' AND u.active=1""", (business,)).fetchall()
    for (user,) in owners:
        _, start, end = pref(db, business, user)
        db.execute("""INSERT INTO notification_jobs(id,business_id,dedupe_key,recipient_user_id,channel,payload_json,run_after)
                      VALUES (?,?,?,?,'telegram',?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
                   (uuid.uuid4().hex, business, sec.signature('notification-key', [business, 'market_risk', identity, user, 'telegram']), user,
                    sec.canonical({'kind': 'market_risk', 'page': 'Overview', 'alert': identity}),
                    at if level == 3 else deliver_after(at, start, end)))
    if level == 3:  # critical alerts also go by email to owners with a verified address; the email has no amounts
        from api.app.notifications import email_alerts
        if email_alerts.configured():
            for (user,) in db.execute("SELECT user_id FROM effective_security_users WHERE business_id=? AND role='owner' AND active=1", (business,)).fetchall():
                if email_alerts.verified_address(db, business, user):
                    db.execute("""INSERT INTO notification_jobs(id,business_id,dedupe_key,recipient_user_id,channel,payload_json,run_after)
                                  VALUES (?,?,?,?,'market_email',?,?) ON CONFLICT(dedupe_key) DO NOTHING""",
                               (uuid.uuid4().hex, business, sec.signature('notification-key', [business, 'market_risk', identity, user, 'email']), user,
                                sec.canonical({'kind': 'market_risk', 'alert': identity}), at))


def deliver_email(db, business, at):
    """Send queued critical-alert emails; a refused email is retried twice, then marked failed."""
    from api.app import mailer
    from api.app.notifications import email_alerts
    if not email_alerts.configured():
        return
    for _ in range(3):
        with sec.transaction(db):
            row = db.execute("""SELECT id,recipient_user_id,payload_json,attempts FROM notification_jobs WHERE business_id=? AND channel='market_email'
                                AND status='queued' AND run_after<=? ORDER BY run_after,id LIMIT 1""", (business, at)).fetchone()
            if not row:
                return
            job, user, payload, attempts = row
            address = email_alerts.verified_address(db, business, user)
            text = alert_text(db, business, json.loads(payload)['alert'])
            db.execute("UPDATE notification_jobs SET status='sending',attempts=attempts+1 WHERE id=?", (job,))
        title = (text or '').split('\n', 1)[0]
        accepted = bool(address and title) and mailer.send(address, 'Capzoryx: ' + title,
                                                           f'{title}\n\nSign in to see the numbers and your options:\n{email_alerts.website()}/\n')
        with sec.transaction(db):
            status = 'sent' if accepted else ('failed' if attempts >= 2 else 'queued')
            db.execute('UPDATE notification_jobs SET status=?,sent_at=?,run_after=?,last_error=? WHERE id=?',
                       (status, at if accepted else None, at + 300 * 2 ** attempts, None if accepted else 'MAIL_NOT_ACCEPTED', job))


def evaluate(db, business, at):
    """Check one business against its own costs. Returns the ids of alerts that should reach the owner."""
    from api.app.business import load_profile, products
    from api.app.insights import position, reference_for
    from api.app.market import stale_after
    profile, _, _, _ = load_profile(db, business)
    consent = profile.get('consent') or {}
    if not consent.get('monitoring'):
        return []
    warn_bp, move_bp = settings(consent)
    currency = sec.original_dataset(db, business).currency
    outgoing = []
    for product in products(db, business):
        ref = reference_for(db, business, product, currency)
        if not ref or ref['basis'] != 'market':
            continue
        observed = datetime.fromisoformat(ref['observed_at']).timestamp()
        if at - observed > stale_after(ref['indicator']):
            continue  # out-of-date prices never raise alerts
        pos = position(product, ref)
        if pos['status'] == 'incomplete':
            continue
        state = db.execute('SELECT level,ref_minor,alerted_at FROM biz_alert_state WHERE business_id=? AND product_id=?',
                           (business, product['id'])).fetchone()
        prev_level, prev_ref, alerted = (state or (0, None, None))
        level, moved = classify(pos, prev_ref, warn_bp, move_bp)
        changed = prev_ref != ref['value_minor']
        recovered = prev_level >= 2 and level <= 1
        send = level >= 1 and changed and (level > prev_level or alerted is None or at - alerted >= COOLDOWN[level])
        if send or recovered:
            identity = store_alert(db, business, pos, 1 if recovered else level, message(level, pos, currency, moved, recovered), at)
            if level >= 2 and consent.get('alerts'):
                outgoing.append((identity, level))
            alerted = at
        db.execute("""INSERT INTO biz_alert_state VALUES (?,?,?,?,?) ON CONFLICT(business_id,product_id)
                      DO UPDATE SET level=excluded.level,ref_minor=excluded.ref_minor,alerted_at=excluded.alerted_at""",
                   (business, product['id'], level, ref['value_minor'], alerted))
    for identity, level in outgoing:
        queue_channels(db, business, identity, level, at)
    return [i for i, _ in outgoing]


def alert_text(db, business, identity):
    row = db.execute('SELECT data_enc,key_tag FROM biz_alerts WHERE id=? AND business_id=?', (identity, business)).fetchone()
    key = customer_key(db, business)
    if not row or not key or key_tag(key) != row[1]:
        return None
    data = json.loads(unseal(key, row[0], identity + '/alert'))
    return '\n'.join([data['title'], '', *data['lines'], '', data['advice']])


# ---------- the background loop ----------
def lease(db, name, seconds, at):
    with sec.transaction(db):
        db.execute("""INSERT INTO job_lease VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,until=excluded.until
                      WHERE job_lease.until<? OR job_lease.holder=excluded.holder""", (name, HOLDER, at + seconds, at))
        return db.execute('SELECT holder FROM job_lease WHERE name=?', (name,)).fetchone()[0] == HOLDER


def tick(db, at):
    """One monitoring pass: refresh due public prices (network outside transactions), then evaluate and deliver."""
    from api.app.business import load_profile
    from api.app import market
    from api.app.notifications import push, telegram
    with sec.transaction(db):
        monitored = []
        for (business,) in db.execute("""SELECT p.business_id FROM biz_profile p JOIN workspace_meta w ON w.business_id=p.business_id
                                         WHERE w.archived_at IS NULL AND w.kind='personal'""").fetchall():
            if (load_profile(db, business)[0].get('consent') or {}).get('monitoring'):
                monitored.append(business)
        pairs = {(k, sec.original_dataset(db, b).currency) for b in monitored for k in market.watched(db, b)}
        due = [(k, c) for k, c in pairs if market.due(db, k, c, at)]
    fetched = {(k, c): market.try_fetch(k, c, at) for k, c in due}
    with sec.transaction(db):
        for (key, currency), got in fetched.items():
            if not isinstance(got, str):
                value, unit, source, url, observed, kind, confidence = got
                market.record(db, '', key, currency, unit, value, source, url, observed, kind, confidence)
    research_due(db)
    sent = 0
    for business in monitored:
        with sec.transaction(db):
            fresh = evaluate(db, business, at)
        if fresh:
            sent += len(fresh)
            telegram.deliver(db, business, at)
            push.deliver(db, business, at)
        deliver_email(db, business, at)  # also retries earlier emails that were not accepted
    return dict(businesses=len(monitored), fetched=sum(1 for v in fetched.values() if not isinstance(v, str)), alerts=sent)


def research_due(db, limit=1):
    """Keep research fresh without the owner pressing anything: one business per pass, weekly or after changes."""
    from api.app.business import load_profile
    from api.app.market import queue_research, run_research
    started = []
    with sec.transaction(db):
        for (business,) in db.execute("""SELECT p.business_id FROM biz_profile p JOIN workspace_meta w ON w.business_id=p.business_id
                                         WHERE p.completed_at IS NOT NULL AND w.archived_at IS NULL AND w.kind='personal'
                                         ORDER BY p.updated_at DESC""").fetchall():
            profile = load_profile(db, business)[0]
            if (profile.get('consent') or {}).get('external_research'):
                identity = queue_research(db, business)
                if identity:
                    started.append((identity, business))
                    if len(started) >= limit:
                        break
    for identity, business in started:
        run_research(identity, business)
    return len(started)


def run_once():
    from api.app.database import connect
    from api.app.notifications.scheduler import tick as reminders
    at = sec.now()
    with closing(connect()) as db:
        if not lease(db, 'monitor', INTERVAL + 120, at):
            return None
        result = tick(db, at)
        try:
            reminders(db, at)  # the existing reminder pass (it claims each half-hour, so an outside caller cannot double it)
        except Exception:  # noqa: BLE001 - reminders failing must not stop market monitoring
            log.exception('reminder pass failed')
        return result


def enabled():
    return os.getenv('CAPZORYX_BACKGROUND_JOBS', '1' if os.getenv('RENDER') else '0') == '1'


async def loop():
    await asyncio.sleep(30)
    while True:
        try:
            result = await asyncio.to_thread(run_once)
            if result:
                log.info('monitor pass: %s', result)
        except Exception:  # noqa: BLE001 - one bad pass must not stop monitoring
            log.exception('monitor pass failed')
        await asyncio.sleep(INTERVAL)


# ---------- routes ----------
@router.get('')
def recent(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        key = customer_key(db, business)
        out = []
        for identity, level, box, tag, at in db.execute('SELECT id,level,data_enc,key_tag,created_at FROM biz_alerts WHERE business_id=? '
                                                         'ORDER BY created_at DESC, rowid DESC LIMIT 30', (business,)).fetchall():
            if not key or key_tag(key) != tag:
                continue
            data = json.loads(unseal(key, box, identity + '/alert'))
            jobs = db.execute("SELECT channel,status FROM notification_jobs WHERE business_id=? AND payload_json LIKE ?",
                              (business, f'%{identity}%')).fetchall()
            out.append(dict(id=identity, level=level, created_at=at, title=data['title'], lines=data['lines'], advice=data['advice'],
                            delivery=[dict(channel=c, status=s) for c, s in jobs]))
        return dict(alerts=out, monitoring=enabled(), simulation_only=True)
