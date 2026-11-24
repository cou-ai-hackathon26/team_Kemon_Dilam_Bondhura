"""Owner-reviewed wallet messages. No wallet connection and no payment execution."""
from datetime import datetime, time, timezone, timedelta
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract, RecordId
from api.app.notifications.service import enqueue
from api.app.inputs import append_record, InputRecord
from api.app.receivables import apply_receipt, customer_book, owner_transaction
from api.app.routes import principal, db_connection
from api.app.services.finance import fingerprint
from api.app.services.receivables import customer_key, key_tag, seal, unseal
from api.app.services.wallet import normalized, parse_payment, rank_matches

router = APIRouter(prefix='/api/payments')
BD = timezone(timedelta(hours=6))
PAYMENT_NAMESPACE = uuid.UUID('8cd0c259-dc00-47da-a32e-b8f3518ddc2e')


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS payment_events(
      id TEXT PRIMARY KEY, business_id TEXT NOT NULL, source TEXT NOT NULL, provider TEXT NOT NULL DEFAULT 'unknown',
      txn_id TEXT, amount_minor INTEGER NOT NULL CHECK(amount_minor>0), direction TEXT NOT NULL CHECK(direction IN ('in','out')),
      counterparty TEXT, key_tag TEXT, occurred_at INTEGER NOT NULL, raw_hash TEXT NOT NULL, matched_record_id TEXT,
      cash_record_id TEXT, flags TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','confirmed','dismissed')),
      created_at INTEGER NOT NULL, UNIQUE(business_id,raw_hash));
    CREATE UNIQUE INDEX IF NOT EXISTS payment_events_txn ON payment_events(business_id,provider,txn_id) WHERE txn_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS payment_events_feed ON payment_events(business_id,status,created_at);
    """)


class Share(Contract):
    text: str = Field(min_length=1, max_length=2000)
    source: str = Field(default='shared_text', pattern='^(shared_text|sms)$')  # sms: read by the Android app with the owner's permission
    expected_business_id: RecordId | None = None
    expected_user_id: RecordId | None = None


class Decision(Contract):
    receivable_id: RecordId | None = None
    checked_wallet: bool = Field(strict=True)


def event_for(db, business, identity):
    row = db.execute('SELECT * FROM payment_events WHERE business_id=? AND id=?', (business,identity)).fetchone()
    if not row:
        sec.fail(404, 'WALLET_EVENT_NOT_FOUND')
    return row


def present(row, book, key):
    occurred_on = datetime.fromtimestamp(row['occurred_at'], BD).date()
    party = ''
    if key and row['counterparty'] and key_tag(key)==row['key_tag']:
        party = unseal(key, row['counterparty'], row['id']).decode()
    candidates = []
    if row['status']=='new' and row['direction']=='in':
        candidates = rank_matches(book,party,row['amount_minor'],occurred_on)
    return dict(id=row['id'],provider=row['provider'],txn_id=row['txn_id'],amount_minor=row['amount_minor'],
                direction=row['direction'],counterparty=party,occurred_on=str(occurred_on),flags=json.loads(row['flags']),
                status=row['status'],matched_record_id=row['matched_record_id'],cash_record_id=row['cash_record_id'],
                candidates=candidates)


@router.post('/share', status_code=201)
def share(body: Share, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db,actor) as current:
        business=current.business_id
        if ((body.expected_business_id and body.expected_business_id!=business) or
                (body.expected_user_id and body.expected_user_id!=current.user_id)):
            sec.fail(409,'WALLET_WORKSPACE_CHANGED')
        if sec.original_dataset(db,business).currency!='BDT':
            sec.fail(422,'WALLET_REQUIRES_BDT')
        try:
            payment=parse_payment(body.text,today_bd())
        except ValueError as exc:
            sec.fail(422,str(exc))
        digest=hashlib.sha256(' '.join(normalized(body.text).split()).encode()).hexdigest()
        prior=db.execute("""SELECT id FROM payment_events WHERE business_id=? AND
                            (raw_hash=? OR (provider=? AND txn_id=?))""",(business,digest,payment.provider,payment.txn_id)).fetchone()
        if prior:
            sec.fail(409,'WALLET_ALREADY_SHARED')
        identity='wallet-'+uuid.uuid4().hex
        key=customer_key(db,business,create=True) if payment.party else None
        counterparty=seal(key,payment.party.encode(),identity) if key else None
        stamp=int(datetime.combine(payment.occurred_on,time(),BD).timestamp())
        db.execute("""INSERT INTO payment_events(id,business_id,source,provider,txn_id,amount_minor,direction,counterparty,
                      key_tag,occurred_at,raw_hash,flags,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (identity,business,body.source,payment.provider,payment.txn_id,payment.amount_minor,payment.direction,
                    counterparty,key_tag(key) if key else None,stamp,digest,json.dumps(payment.flags),sec.now()))
        sec.audit(db,current,identity,'PAYMENT_SHARED',dict(event_id=identity,provider=payment.provider))
        enqueue(db,business,'payment_shared',identity)
        return dict(present(event_for(db,business,identity),customer_book(db,business),key),simulation_only=True)


@router.get('/feed')
def feed(before: int | None = Query(default=None, gt=0), actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db,actor) as current:
        business=current.business_id
        rows=db.execute('''SELECT rowid AS cursor,* FROM payment_events WHERE business_id=? AND (? IS NULL OR rowid<?)
                           ORDER BY rowid DESC LIMIT 31''',(business,before,before)).fetchall()
        page=rows[:30]
        book=customer_book(db,business) if any(r['status']=='new' and r['direction']=='in' for r in page) else {}
        key=customer_key(db,business) if any(r['counterparty'] for r in page) else None
        return dict(events=[present(row,book,key) for row in page],next_cursor=page[-1]['cursor'] if len(rows)>30 else None,
                    simulation_only=True)


@router.post('/{identity}/confirm')
def confirm(identity: str, body: Decision, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db,actor) as current:
        business=current.business_id
        row=event_for(db,business,identity)
        if not body.checked_wallet:
            sec.fail(422,'VERIFY_WALLET_FIRST')
        if row['status']=='confirmed':
            if row['matched_record_id']!=body.receivable_id:
                sec.fail(409,'WALLET_DECISION_CONFLICT')
            return dict(cash_record_id=row['cash_record_id'],replayed=True,simulation_only=True)
        if row['status']!='new' or row['direction']!='in':
            sec.fail(409,'WALLET_NOT_CONFIRMABLE')
        if set(json.loads(row['flags'])) & {'UNVERIFIED_STATUS','BALANCE_MISMATCH','FUTURE_DATE'}:
            sec.fail(409,'WALLET_NOT_CONFIRMABLE')
        ds=sec.original_dataset(db,business)
        if ds.currency!='BDT':
            sec.fail(422,'WALLET_REQUIRES_BDT')
        occurred_on=datetime.fromtimestamp(row['occurred_at'],BD).date()
        request_id=str(uuid.uuid5(PAYMENT_NAMESPACE,f"{business}:{row['provider']}:{row['txn_id'] or row['raw_hash']}"))
        if body.receivable_id:
            _, cash=apply_receipt(db,current,body.receivable_id,row['amount_minor'],occurred_on,request_id,request_id,identity)
        else:
            result=append_record(db,current,InputRecord(request_id=request_id, expected_revision=fingerprint(ds),
                                 kind='cash',amount_minor=row['amount_minor'],effective_on=occurred_on))
            if result['replayed']:
                sec.fail(409,'CASH_REQUEST_ALREADY_USED')
            cash=result['source_id']
        db.execute("""UPDATE payment_events SET status='confirmed',matched_record_id=?,cash_record_id=?
                      WHERE business_id=? AND id=?""",(body.receivable_id,cash,business,identity))
        sec.audit(db,current,identity,'PAYMENT_CONFIRMED',dict(cash_record_id=cash,receivable_id=body.receivable_id))
        return dict(cash_record_id=cash,replayed=False,simulation_only=True)


@router.post('/{identity}/dismiss')
def dismiss(identity: str, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db,actor) as current:
        row=event_for(db,current.business_id,identity)
        if row['status']=='confirmed':
            sec.fail(409,'WALLET_DECISION_CONFLICT')
        if row['status']=='new':
            db.execute("UPDATE payment_events SET status='dismissed' WHERE business_id=? AND id=?",(current.business_id,identity))
            sec.audit(db,current,identity,'PAYMENT_DISMISSED',dict(event_id=identity))
    return dict(simulation_only=True)
