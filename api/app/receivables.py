"""Owner-managed customer receivables and explicit, atomic cash receipts."""
import uuid
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract, Currency, ISODate, PositiveMinor, RecordId
from api.app.inputs import InputRecord, append_record
from api.app.routes import db_connection, principal
from api.app.services.finance import fingerprint
from api.app.services.payment_timing import collection_outlook
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/workspace')
RequestId = Annotated[str, Field(pattern=r'^[a-f0-9-]{36}$')]


class Customer(Contract):
    request_id: RequestId
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, pattern=r'^\+?[0-9][0-9 ()-]{4,23}[0-9]$')


class Receivable(Contract):
    request_id: RequestId
    customer_id: RecordId
    number: str | None = Field(default=None, min_length=1, max_length=80)
    currency: Currency
    amount_minor: PositiveMinor
    issued_on: ISODate
    due_on: ISODate


class Receipt(Contract):
    request_id: RequestId
    amount_minor: PositiveMinor
    received_on: ISODate


@contextmanager
def owner_transaction(db, actor):
    if actor.role != 'owner':
        sec.fail(403, 'OWNER_REQUIRED')
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role != 'owner':
            sec.fail(403, 'OWNER_REQUIRED')
        if db.execute('SELECT 1 FROM workspace_meta WHERE business_id=? AND archived_at IS NOT NULL',
                      (current.business_id,)).fetchone():
            sec.fail(403, 'WORKSPACE_CLEARED')
        sec.verify_audit_head(db, current.business_id)
        yield current


def save_once(db, current, operation, body, write):
    business = current.business_id
    digest = sec.signature('collection-request', [business, operation, body.model_dump(mode='json')])
    prior = db.execute('SELECT digest,result_id FROM collection_requests WHERE business_id=? AND request_id=?',
                       (business, body.request_id)).fetchone()
    if prior:
        if prior[0] != digest:
            sec.fail(409, 'INPUT_REQUEST_CONFLICT')
        return dict(id=prior[1], replayed=True, simulation_only=True)
    identity = write()
    db.execute('INSERT INTO collection_requests VALUES (?,?,?,?)', (business, body.request_id, digest, identity))
    return dict(id=identity, replayed=False, simulation_only=True)


@router.post('/customers', status_code=201)
def add_customer(body: Customer, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        business = current.business_id
        def write():
            identity = 'customer-' + uuid.uuid4().hex
            key = customer_key(db, business, create=True)
            name = seal(key, body.name.encode(), identity + '/name')
            db.execute('INSERT INTO customers VALUES (?,?,?,?,?)', (identity, business, name, key_tag(key), sec.now()))
            if body.phone:
                phone = seal(key, body.phone.encode(), identity + '/phone')
                db.execute('INSERT INTO customer_contacts VALUES (?,?,?,?)', (identity, business, phone, sec.now()))
            sec.audit(db, current, identity, 'CUSTOMER_ADDED', {'customer_id': identity})
            return identity
        return save_once(db, current, 'customer', body, write)


@router.post('/customers/{customer_id}/forget-phone')
def forget_phone(customer_id: str, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        params = (current.business_id, customer_id)
        if not db.execute('SELECT 1 FROM customers WHERE business_id=? AND id=?', params).fetchone():
            sec.fail(404, 'CUSTOMER_NOT_FOUND')
        if db.execute('SELECT 1 FROM customer_contacts WHERE business_id=? AND customer_id=?', params).fetchone():
            db.execute('DELETE FROM customer_contacts WHERE business_id=? AND customer_id=?', params)
            sec.audit(db, current, customer_id, 'CUSTOMER_PHONE_REMOVED', {'customer_id': customer_id})
    return dict(simulation_only=True)


@router.post('/receivables', status_code=201)
def add_receivable(body: Receivable, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        business = current.business_id
        def write():
            ds = sec.original_dataset(db, business)
            if ds.currency != body.currency:
                sec.fail(409, 'WORKSPACE_CURRENCY_CHANGED')
            if not ds.opening_on <= body.issued_on <= today_bd() or body.due_on < body.issued_on:
                sec.fail(422, 'INVALID_INVOICE_DATES')
            if not db.execute('SELECT 1 FROM customers WHERE business_id=? AND id=?', (business, body.customer_id)).fetchone():
                sec.fail(404, 'CUSTOMER_NOT_FOUND')
            identity = 'receivable-' + uuid.uuid4().hex
            db.execute('INSERT INTO receivables VALUES (?,?,?,?,?,?,?,?)',
                       (identity, business, body.customer_id, body.number, body.amount_minor,
                        body.issued_on.isoformat(), body.due_on.isoformat(), sec.now()))
            sec.audit(db, current, identity, 'RECEIVABLE_ADDED', dict(source_id=identity, amount_minor=body.amount_minor))
            return identity
        return save_once(db, current, 'receivable', body, write)


def apply_receipt(db, current, receivable_id, amount_minor, received_on, request_id, cash_request_id, payment_event_id=None):
    """Record cleared cash and the linked settlement inside the caller's owner transaction."""
    business = current.business_id
    row = db.execute('SELECT open_minor,issued_on FROM receivable_balances WHERE business_id=? AND id=?',
                     (business, receivable_id)).fetchone()
    if not row:
        sec.fail(404, 'RECEIVABLE_NOT_FOUND')
    if amount_minor > row[0]:
        sec.fail(409, 'AMOUNT_EXCEEDS_RECEIVABLE')
    if received_on.isoformat() < row[1] or received_on > today_bd():
        sec.fail(422, 'INVALID_RECEIPT_DATE')
    ds = sec.original_dataset(db, business)
    cash = append_record(db, current, InputRecord(request_id=cash_request_id, expected_revision=fingerprint(ds),
                         kind='cash', amount_minor=amount_minor, effective_on=received_on))
    if cash['replayed']:
        sec.fail(409, 'CASH_REQUEST_ALREADY_USED')
    identity = 'settlement-' + uuid.uuid4().hex
    db.execute('INSERT INTO receivable_settlements VALUES (?,?,?,?,?,?,?,?)',
               (identity, business, receivable_id, payment_event_id, amount_minor, request_id, cash['source_id'], sec.now()))
    sec.audit(db, current, identity, 'RECEIVABLE_SETTLED', dict(receivable_id=receivable_id, cash_record_id=cash['source_id'],
                                                                amount_minor=amount_minor))
    return identity, cash['source_id']


@router.post('/receivables/{receivable_id}/settlements', status_code=201)
def settle(receivable_id: str, body: Receipt, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        def write():
            identity, _ = apply_receipt(db,current,receivable_id,body.amount_minor,body.received_on,
                                        body.request_id,str(uuid.uuid4()))
            return identity
        return save_once(db, current, 'settle/' + receivable_id, body, write)


def customer_book(db, business):
    rows = db.execute("""SELECT c.id,c.name_enc,p.phone_enc,c.key_tag FROM customers c LEFT JOIN customer_contacts p
                         ON p.customer_id=c.id AND p.business_id=c.business_id WHERE c.business_id=?
                         ORDER BY c.created_at,c.id""", (business,)).fetchall()
    customers = []
    if rows:
        key = customer_key(db, business)
        active_tag = key_tag(key) if key else None
        for identity, name, phone, tag in rows:
            customers.append(dict(id=identity, name=unseal(key, name, identity + '/name').decode() if tag == active_tag else None,
                                  phone=unseal(key, phone, identity + '/phone').decode() if tag == active_tag and phone else None))
    debts = db.execute("""SELECT id,customer_id,number,amount_minor,open_minor,issued_on,due_on
                          FROM receivable_balances WHERE business_id=? ORDER BY due_on,id""", (business,)).fetchall()
    return dict(customers=customers, receivables=[dict(row) for row in debts])


@router.get('/receivables')
def list_receivables(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        book=customer_book(db,current.business_id)
        outlook=collection_outlook(db,current.business_id,book["receivables"],today_bd())
        return dict(book,collection_outlook=outlook,simulation_only=True)
