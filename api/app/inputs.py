"""Workspace record entry. Append only, versioned, atomic and idempotent."""
import hashlib
import json
import uuid
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import Field
from api.app import security as sec
from api.app.services import record_notes
from api.app.accounts import today_bd
from api.app.contracts import Contract, PositiveMinor, ISODate, RecordId, Currency
from api.app.routes import db_connection, principal
from api.app.services.finance import validated, fingerprint, snapshot

router=APIRouter(prefix='/api')


class RecordFields(Contract):
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    kind: Literal['invoice','cash']
    amount_minor: PositiveMinor
    effective_on: ISODate
    direction: Literal['inflow','outflow'] = 'inflow'
    status: Literal['cleared','scheduled'] = 'cleared'
    vendor_id: RecordId | None = None
    invoice_number: str | None = Field(default=None,min_length=1,max_length=80)
    due_on: ISODate | None = None
    memo: str = Field(default='',max_length=2000)
    trust_vendor: bool = Field(default=False,strict=True)


class InputRecord(RecordFields):
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class OfflineRecord(RecordFields):
    user_id: RecordId
    business_id: RecordId
    currency: Currency
    trust_vendor: Literal[False] = False


@router.post('/workspace/offline-records', status_code=201)
def sync_offline(body: OfflineRecord, actor=Depends(principal), db=Depends(db_connection)):
    """The signed-in recordkeeper already reviewed this independent record before queueing it on this device."""
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role not in ('owner','bookkeeper') or (current.business_id,current.user_id) != (body.business_id,body.user_id):
            sec.fail(403,'DRAFT_WORKSPACE_MISMATCH')
        active = db.execute("""SELECT 1 FROM account_members m JOIN workspace_meta w ON w.business_id=m.business_id
                               WHERE m.user_id=? AND m.business_id=? AND w.kind='personal' AND w.archived_at IS NULL""",
                            (current.user_id, current.business_id)).fetchone()
        if not active:
            sec.fail(403,'WORKSPACE_CLEARED')
        sec.verified_audit(db, current.business_id)
        return append_record(db,current,body,manual_entry=True)


@router.post('/workspace/records',status_code=201)
def add_record(body:InputRecord,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role not in ('owner', 'bookkeeper'):sec.fail(403,'RECORDKEEPER_REQUIRED')
    if not db.execute('SELECT 1 FROM account_members WHERE user_id=?',(actor.user_id,)).fetchone():sec.fail(403,'ACCOUNT_REQUIRED')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role not in ('owner', 'bookkeeper'):sec.fail(403,'RECORDKEEPER_REQUIRED')
        if body.trust_vendor and current.role != 'owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verify_audit_head(db,current.business_id)
        return append_record(db, current, body, manual_entry=True)


def append_record(db, current, body, *, manual_entry=False):
    """Append in the caller's verified recordkeeping transaction."""
    digest=hashlib.sha256(sec.canonical(body.model_dump(mode='json')).encode()).hexdigest()
    prior=db.execute('SELECT body_sha256 FROM workspace_input_requests WHERE business_id=? AND request_id=?',(current.business_id,body.request_id)).fetchone()
    if prior:
        if prior[0]!=digest:sec.fail(409,'INPUT_REQUEST_CONFLICT')
        return dict(replayed=True,simulation_only=True)
    ds=sec.original_dataset(db,current.business_id)
    if isinstance(body, OfflineRecord):
        if ds.currency != body.currency:sec.fail(409,'WORKSPACE_CURRENCY_CHANGED')
    elif fingerprint(ds)!=body.expected_revision:sec.fail(409,'DATA_CHANGED_REFRESH_REQUIRED')
    raw=ds.model_dump(mode='json');record_id='manual-'+body.request_id
    if body.effective_on<ds.opening_on:sec.fail(422,'DATE_BEFORE_OPENING')
    if body.kind=='invoice':
        if not body.vendor_id or not body.invoice_number or not body.due_on:sec.fail(422,'INVOICE_FIELDS_REQUIRED')
        if body.effective_on>today_bd() or body.due_on<body.effective_on:sec.fail(422,'INVALID_INVOICE_DATES')
        if body.direction!='inflow' or body.status!='cleared':sec.fail(422,'UNEXPECTED_INVOICE_CASH_FIELDS')
        record=dict(source_record_id=record_id,invoice_id=record_id,vendor_id=body.vendor_id,invoice_number=body.invoice_number,issued_on=body.effective_on.isoformat(),due_on=body.due_on.isoformat(),amount_minor=body.amount_minor,currency=ds.currency,status='open',memo=body.memo)
        raw['invoices'].append(record);kind='invoice'
    else:
        if body.vendor_id or body.invoice_number or body.due_on or body.trust_vendor:sec.fail(422,'UNEXPECTED_CASH_INVOICE_FIELDS')
        if body.status=='cleared' and body.effective_on>today_bd():sec.fail(422,'CLEARED_DATE_IN_FUTURE')
        record=dict(source_record_id=record_id,event_id=record_id,direction=body.direction,status=body.status,effective_on=body.effective_on.isoformat(),amount_minor=body.amount_minor,currency=ds.currency,invoice_id=None)
        raw['events'].append(record);kind='event'
    raw['as_of']=max(ds.as_of,today_bd()).isoformat()
    try:new=validated(raw)
    except ValueError:sec.fail(422,'INVALID_FINANCIAL_RECORD')
    # Save immutable old economics before the head revision advances.
    db.execute('INSERT OR IGNORE INTO dataset_versions VALUES (?,?,?)',(current.business_id,fingerprint(ds),ds.model_dump_json()))
    db.execute('UPDATE financial_datasets SET dataset_sha256=?,payload=? WHERE business_id=?',(fingerprint(new),new.model_dump_json(),current.business_id))
    typed=new.invoices[-1] if kind=='invoice' else new.events[-1]
    db.execute('INSERT INTO source_records VALUES (?,?,?,?,?,?)',(current.business_id,record_id,kind,record_id,fingerprint(typed),typed.model_dump_json()))
    if body.kind=='invoice' and body.trust_vendor:
        db.execute('INSERT INTO trusted_vendors VALUES (?,?,1) ON CONFLICT(business_id,vendor_id) DO UPDATE SET enabled=1',(current.business_id,body.vendor_id))
    # Validate all existing signed postings against the extended source before commit.
    runtime=sec.runtime_dataset(db,current.business_id)
    if len(runtime.events)>2000:sec.fail(409,'RUNTIME_EVENT_CAPACITY_EXCEEDED')
    if kind=='invoice':
        from api.app.notifications.service import duplicate_notice
        duplicate_notice(db,current.business_id,runtime,record_id)
    db.execute('INSERT INTO workspace_input_requests VALUES (?,?,?)',(current.business_id,body.request_id,digest))
    note_fields = record_notes.store(db, current.business_id, record_id, kind, body.memo)
    sec.audit(db,current,'input-'+body.request_id,'RECORD_ADDED',dict(source_id=record_id,kind=kind,entry_origin='manual' if manual_entry else 'linked',previous_revision=fingerprint(ds),dataset_sha256=fingerprint(new),record_sha256=fingerprint(typed),payee_allowed=body.trust_vendor,**note_fields))
    return dict(source_id=record_id,replayed=False,simulation_only=True)


class RecordBatch(Contract):
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    records: list[RecordFields] = Field(min_length=1, max_length=40)


@router.post('/workspace/records/batch',status_code=201)
def add_records(body:RecordBatch,actor=Depends(principal),db=Depends(db_connection)):
    """Save many confirmed drafts (for example a week of spending from one voice note) all at once or not at all."""
    if actor.role not in ('owner', 'bookkeeper'):sec.fail(403,'RECORDKEEPER_REQUIRED')
    if not db.execute('SELECT 1 FROM account_members WHERE user_id=?',(actor.user_id,)).fetchone():sec.fail(403,'ACCOUNT_REQUIRED')
    if len({r.request_id for r in body.records})!=len(body.records):sec.fail(422,'DUPLICATE_REQUEST_IDS')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role not in ('owner', 'bookkeeper'):sec.fail(403,'RECORDKEEPER_REQUIRED')
        if any(r.trust_vendor for r in body.records) and current.role != 'owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verify_audit_head(db,current.business_id)
        ids=[r.request_id for r in body.records]
        known=db.execute(f"SELECT COUNT(*) FROM workspace_input_requests WHERE business_id=? AND request_id IN ({','.join('?'*len(ids))})",  # noqa: S608
                         (current.business_id,*ids)).fetchone()[0]
        digest=hashlib.sha256(sec.canonical([r.model_dump(mode='json') for r in body.records]).encode()).hexdigest()
        batch_key='batch:'+hashlib.sha256('|'.join(ids).encode()).hexdigest()[:40]
        if known==len(ids):  # the same batch sent again (for example after a dropped connection): already saved
            stored=db.execute('SELECT body_sha256 FROM workspace_input_requests WHERE business_id=? AND request_id=?',(current.business_id,batch_key)).fetchone()
            if not stored or stored[0]!=digest:sec.fail(409,'INPUT_REQUEST_CONFLICT')  # same ids, different contents
            return dict(saved=0,replayed=True,source_ids=['manual-'+i for i in ids],
                        dataset_revision=fingerprint(sec.original_dataset(db,current.business_id)),simulation_only=True)
        if known:sec.fail(409,'INPUT_REQUEST_CONFLICT')
        revision=body.expected_revision
        saved=[]
        for record in body.records:
            # Each record extends the revision the previous one produced, inside one transaction.
            result=append_record(db,current,InputRecord(**record.model_dump(),expected_revision=revision),manual_entry=True)
            saved.append(result.get('source_id'))
            revision=fingerprint(sec.original_dataset(db,current.business_id))
        db.execute('INSERT INTO workspace_input_requests VALUES (?,?,?)',(current.business_id,batch_key,digest))
        return dict(saved=len(saved),source_ids=saved,dataset_revision=revision,simulation_only=True)


from api.app.services.finance import Dataset

def has_financial_activity(db, business, dataset):
    if dataset.invoices or dataset.events or dataset.opening_balance_minor:
        return True
    return bool(db.execute("""SELECT EXISTS(SELECT 1 FROM receivables WHERE business_id=?)
                             OR EXISTS(SELECT 1 FROM proposals WHERE business_id=?)""",
                           (business, business)).fetchone()[0])


@router.post('/workspace/import',status_code=201)
def import_initial(body:Dataset,actor=Depends(principal),db=Depends(db_connection)):
    """Replace only a completely untouched zero workspace, never existing finances."""
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role!='owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verify_audit_head(db,current.business_id)
        previous=sec.original_dataset(db,current.business_id)
        if has_financial_activity(db, current.business_id, previous):sec.fail(409,'WORKSPACE_NOT_EMPTY')
        new=validated(body)
        if new.currency!=previous.currency:sec.fail(409,'IMPORT_CURRENCY_MISMATCH')
        db.execute('INSERT OR IGNORE INTO dataset_versions VALUES (?,?,?)',(current.business_id,fingerprint(previous),previous.model_dump_json()))
        db.execute('UPDATE financial_datasets SET dataset_sha256=?,payload=? WHERE business_id=?',(fingerprint(new),new.model_dump_json(),current.business_id))
        for kind,record_id,record in [('invoice',i.invoice_id,i) for i in new.invoices]+[('event',e.event_id,e) for e in new.events]:
            db.execute('INSERT INTO source_records VALUES (?,?,?,?,?,?)',(current.business_id,record.source_record_id,kind,record_id,fingerprint(record),record.model_dump_json()))
        from api.app.notifications.service import duplicate_notice
        duplicate_notice(db,current.business_id,new)
        sec.audit(db,current,'import-'+uuid.uuid4().hex,'IMPORTED',dict(dataset_sha256=fingerprint(new),invoice_count=len(new.invoices),event_count=len(new.events),payees_allowlisted=False))
    return dict(dataset_sha256=fingerprint(new),simulation_only=True)


class VendorPermission(Contract):
    vendor_id: RecordId
    enabled: bool = Field(strict=True)

@router.post('/workspace/vendor-permission')
def vendor_permission(body:VendorPermission,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role!='owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verify_audit_head(db,current.business_id)
        ds=sec.original_dataset(db,current.business_id)
        if not any(i.vendor_id==body.vendor_id for i in ds.invoices):sec.fail(404,'VENDOR_NOT_FOUND')
        db.execute('INSERT INTO trusted_vendors VALUES (?,?,?) ON CONFLICT(business_id,vendor_id) DO UPDATE SET enabled=excluded.enabled',(current.business_id,body.vendor_id,int(body.enabled)))
        sec.audit(db,current,'payee-'+uuid.uuid4().hex,'PAYEE_PERMISSION_CHANGED',{'vendor_id':body.vendor_id,'enabled':body.enabled})
    return {'simulation_only':True}


from api.app.contracts import Currency
from api.app.currencies import scale

class WorkspaceCurrency(Contract):
    currency: Currency
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.post('/workspace/currency')
def set_currency(body:WorkspaceCurrency,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    with sec.transaction(db):
        current=sec.recheck(db,actor)
        if current.role!='owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verify_audit_head(db,current.business_id)
        previous=sec.original_dataset(db,current.business_id)
        if fingerprint(previous)!=body.expected_revision:sec.fail(409,'DATA_CHANGED_REFRESH_REQUIRED')
        if body.currency==previous.currency:return dict(simulation_only=True)
        if has_financial_activity(db, current.business_id, previous):sec.fail(409,'WORKSPACE_CURRENCY_LOCKED')
        raw=previous.model_dump(mode='json');raw['currency']=body.currency;new=validated(raw)
        db.execute('INSERT OR IGNORE INTO dataset_versions VALUES (?,?,?)',(current.business_id,fingerprint(previous),previous.model_dump_json()))
        db.execute('UPDATE financial_datasets SET dataset_sha256=?,payload=? WHERE business_id=?',(fingerprint(new),new.model_dump_json(),current.business_id))
        db.execute('UPDATE security_policies SET reserve_minor=0,payment_limit_minor=? WHERE business_id=?',(100000*scale(new.currency),current.business_id))
        sec.audit(db,current,'currency-'+uuid.uuid4().hex,'CURRENCY_CHANGED',dict(previous_currency=previous.currency,currency=new.currency,dataset_sha256=fingerprint(new),reserve_minor=0,payment_limit_minor=100000*scale(new.currency)))
    return dict(simulation_only=True)
