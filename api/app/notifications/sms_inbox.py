"""Payment SMS inbox: bKash, Nagad, Rocket and bank messages become drafts the owner saves with one tap.

The owner installs a free SMS-forwarding app on their Android phone and points it at a secret link from Settings.
Each forwarded message is parsed on the server. OTP and PIN messages are dropped unread. A clear payment becomes a
pending draft, the owner is told on Telegram (when linked), and nothing enters the ledger until they tap Save.
"""
import hashlib
import json
import os
import secrets
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.notifications.telegram import Empty
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal
from api.app.services.wallet import parse_payment

router = APIRouter(prefix='/api/inbox/sms')
PROVIDER_LABELS = {'bkash': 'bKash', 'nagad': 'Nagad', 'rocket': 'Rocket'}
BANK_HINTS = ('bank', 'a/c', 'acct', 'account', 'dbbl', 'brac', 'city', 'ebl', 'islami', 'sonali', 'janata', 'ific',
              'ucb', 'prime', 'mtb', 'ব্যাংক')


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS sms_feeds(business_id TEXT NOT NULL,user_id TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,last_seen INTEGER,PRIMARY KEY(business_id,user_id));
      CREATE TABLE IF NOT EXISTS sms_drafts(id TEXT PRIMARY KEY,business_id TEXT NOT NULL,user_id TEXT NOT NULL,
        message_hash TEXT NOT NULL,received_at INTEGER NOT NULL,data_enc TEXT NOT NULL,key_tag TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','saved','skipped')),
        UNIQUE(business_id,message_hash));
    """)


def feed_url(token):
    target = urlsplit(os.getenv('SAFELEDGER_PUBLIC_URL', ''))
    base = f'{target.scheme}://{target.netloc}' if target.scheme in ('http', 'https') and target.netloc else ''
    return f'{base}/api/inbox/sms/hook/{token}'


def label(payment, sender):
    if payment.provider in PROVIDER_LABELS:
        return PROVIDER_LABELS[payment.provider]
    text = sender.casefold()
    return 'Bank' if any(hint in text for hint in BANK_HINTS) else (sender[:20] or 'SMS')


def describe(item, lang='en'):
    from api.app.chat.brain import money
    amount = money(item['amount_minor'], 'BDT', lang)
    party = f" {'from' if item['direction'] == 'inflow' else 'to'} {item['party']}" if item['party'] else ''
    verb = 'received' if item['direction'] == 'inflow' else 'paid'
    return f"{item['source']}: {amount} {verb}{party} on {item['day']}"


def pending_items(db, business):
    key = customer_key(db, business)
    rows = db.execute("SELECT id,data_enc,key_tag,received_at FROM sms_drafts WHERE business_id=? AND status='pending' "
                      'ORDER BY received_at DESC LIMIT 20', (business,)).fetchall()
    items = []
    for identity, data, tag, received in rows:
        if key and key_tag(key) == tag:
            item = json.loads(unseal(key, data, business + '/sms/' + identity))
            items.append(dict(item, id=identity, received_at=received))
    return items


def item(db, business, identity):
    return next((i for i in pending_items(db, business) if i['id'] == identity), None)


def as_draft(entry):
    """The chat brain's draft shape. The request id is derived from the SMS id so a double tap cannot save twice."""
    return dict(amount_minor=entry['amount_minor'], direction=entry['direction'], day=entry['day'],
                party=(entry['source'] + (' · ' + entry['party'] if entry['party'] else '')
                       + (' · ' + entry['txn_id'] if entry.get('txn_id') else ''))[:120],
                request_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'capzoryx-sms/' + entry['id'])),
                expires=sec.now() + 86400, sms_id=entry['id'])


def mark(db, business, identity, status):
    db.execute("UPDATE sms_drafts SET status=? WHERE business_id=? AND id=? AND status='pending'", (status, business, identity))


# ---------- owner endpoints ----------
@router.get('')
def status(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        row = db.execute('SELECT created_at,last_seen FROM sms_feeds WHERE business_id=? AND user_id=?',
                         (owner.business_id, owner.user_id)).fetchone()
        return dict(enabled=bool(row), last_seen=row[1] if row else None, pending=pending_items(db, owner.business_id),
                    simulation_only=True)


@router.post('/link')
def create_link(body: Empty, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        sec.budget(db, 'sms-feed:' + owner.user_id, 4)
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO sms_feeds VALUES (?,?,?,?,NULL) ON CONFLICT(business_id,user_id) DO UPDATE SET '
                   'token_hash=excluded.token_hash,created_at=excluded.created_at,last_seen=NULL',
                   (owner.business_id, owner.user_id, sec.signature('sms-feed', token), sec.now()))
        sec.audit(db, owner, uuid.uuid4().hex, 'SMS_FEED_CREATED', {})
        return dict(url=feed_url(token), simulation_only=True)


@router.post('/unlink')
def remove_link(body: Empty, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        db.execute('DELETE FROM sms_feeds WHERE business_id=? AND user_id=?', (owner.business_id, owner.user_id))
        db.execute("UPDATE sms_drafts SET status='skipped' WHERE business_id=? AND status='pending'", (owner.business_id,))
        sec.audit(db, owner, uuid.uuid4().hex, 'SMS_FEED_REMOVED', {})
    return dict(simulation_only=True)


@router.post('/drafts/{identity}/{action}')
def decide(identity: str, action: str, body: Empty, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.chat import brain
    if action not in ('save', 'skip') or len(identity) != 32 or not identity.isalnum():
        sec.fail(404, 'SMS_DRAFT_NOT_FOUND')
    with owner_transaction(db, actor) as owner:
        entry = item(db, owner.business_id, identity)
        if not entry:
            sec.fail(404, 'SMS_DRAFT_NOT_FOUND')
        if action == 'skip':
            mark(db, owner.business_id, identity, 'skipped')
            return dict(saved=False, simulation_only=True)
    ctx = brain.Context('sms', 'sms-web:' + owner.business_id, owner.business_id, owner.user_id)
    reply = brain.save_draft(db, ctx, as_draft(entry), 'en')
    if not reply.text.startswith('Saved'):
        sec.fail(409, 'SMS_DRAFT_NOT_SAVED')
    return dict(saved=True, simulation_only=True)


# ---------- forwarder webhook ----------
@router.post('/hook/{token}')
def hook(token: str, payload: dict, request: Request, db=Depends(db_connection)):
    if not 30 <= len(token) <= 64 or not token.replace('-', '').replace('_', '').isalnum():
        sec.fail(404, 'NOT_FOUND')
    digest = sec.signature('sms-feed', token)
    sec.budgets(db, [('sms-hook:' + digest[:16], 30), ('sms-hook-all', 300)])
    text = next((payload[k] for k in ('text', 'message', 'body', 'msg', 'content') if isinstance(payload.get(k), str)), '')
    sender = next((payload[k] for k in ('from', 'sender', 'address', 'number') if isinstance(payload.get(k), str)), '')
    text, sender = text.strip()[:1000], sender.strip()[:40]
    with sec.transaction(db):
        feed = db.execute('SELECT business_id,user_id FROM sms_feeds WHERE token_hash=?', (digest,)).fetchone()
        if not feed:
            sec.fail(404, 'NOT_FOUND')
        business, user = feed
        db.execute('UPDATE sms_feeds SET last_seen=? WHERE token_hash=?', (sec.now(), digest))
        if not text:
            return {'ok': True, 'result': 'empty'}
        try:
            payment = parse_payment(text, today_bd())
        except ValueError as error:
            # OTP/PIN and non-payment messages are dropped without being stored.
            return {'ok': True, 'result': 'ignored', 'reason': str(error)[:40]}
        if 'UNVERIFIED_STATUS' in payment.flags or 'FUTURE_DATE' in payment.flags:
            return {'ok': True, 'result': 'ignored', 'reason': 'UNVERIFIED'}
        message_hash = hashlib.sha256((business + '\n' + ' '.join(text.split())).encode()).hexdigest()
        if db.execute('SELECT 1 FROM sms_drafts WHERE business_id=? AND message_hash=?', (business, message_hash)).fetchone():
            return {'ok': True, 'result': 'duplicate'}
        identity = uuid.uuid4().hex
        entry = dict(source=label(payment, sender), txn_id=payment.txn_id, amount_minor=payment.amount_minor,
                     direction='inflow' if payment.direction == 'in' else 'outflow', party=payment.party[:60],
                     day=str(payment.occurred_on), flags=list(payment.flags))
        key = customer_key(db, business, create=True)
        db.execute('INSERT INTO sms_drafts(id,business_id,user_id,message_hash,received_at,data_enc,key_tag) VALUES (?,?,?,?,?,?,?)',
                   (identity, business, user, message_hash, sec.now(),
                    seal(key, sec.canonical(entry).encode(), business + '/sms/' + identity), key_tag(key)))
    notify_telegram(db, business, user, dict(entry, id=identity))
    return {'ok': True, 'result': 'draft'}


def notify_telegram(db, business, user, entry):
    from api.app.notifications import telegram
    if not telegram.configured():
        return
    row = db.execute('SELECT chat_enc,key_tag FROM telegram_links WHERE business_id=? AND user_id=? AND confirmed_at IS NOT NULL '
                     'AND chat_enabled=1', (business, user)).fetchone()
    key = customer_key(db, business)
    if not row or not key or key_tag(key) != row[1]:
        return
    chat = json.loads(unseal(key, row[0], business + '/' + user + '/telegram'))['id']
    import httpx
    with (telegram.TRANSPORT or httpx.HTTPTransport(trust_env=False)) as transport:
        telegram.send_message(transport, chat, '📩 New payment SMS — ' + describe(entry) + '. Save it to your books?',
                              [('Save', 'sms:save:' + entry['id']), ('Skip', 'sms:skip:' + entry['id'])])
