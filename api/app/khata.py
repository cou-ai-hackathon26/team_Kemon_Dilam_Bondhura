"""Persist a single owner-reviewed notebook row using the normal ledger or receivable operation."""
from typing import Literal
import uuid

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.contracts import Contract, Currency, ISODate, PositiveMinor, RecordId
from api.app.inputs import InputRecord, append_record
from api.app.receivables import Receipt, Receivable, add_receivable, owner_transaction, save_once, settle
from api.app.routes import db_connection, principal
from api.app.services.finance import fingerprint

router=APIRouter(prefix='/api/workspace/khata')
NAMESPACE=uuid.UUID('d7965ff5-f95e-4f2c-869f-93e1a9f40838')


class KhataRow(Contract):
    page_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    line: int = Field(strict=True,ge=1,le=400)
    expected_business_id: RecordId
    expected_user_id: RecordId
    checked: bool=Field(strict=True)
    kind: Literal['inflow','outflow','customer_credit','customer_receipt','supplier_bill']
    currency: Currency
    amount_minor: PositiveMinor
    day: ISODate
    customer_id: RecordId | None=None
    receivable_id: RecordId | None=None
    due_on: ISODate | None=None
    vendor_id: RecordId | None=None
    number: str | None=Field(default=None,min_length=1,max_length=80)


class ReviewedCashRow(KhataRow):
    request_id: str


@router.post('/confirm',status_code=201)
def confirm(row:KhataRow,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    if row.expected_business_id!=actor.business_id or row.expected_user_id!=actor.user_id:
        sec.fail(409,'KHATA_WORKSPACE_CHANGED')
    if not row.checked:sec.fail(422,'KHATA_REVIEW_REQUIRED')
    provided={key for key in ('vendor_id','customer_id','receivable_id','due_on','number') if getattr(row,key) is not None}
    allowed={'customer_credit':{'customer_id','due_on','number'},'customer_receipt':{'receivable_id'},
             'supplier_bill':{'vendor_id','number','due_on'}}.get(row.kind,set())
    if provided-allowed:sec.fail(422,'KHATA_UNEXPECTED_FIELDS')
    identity=str(uuid.uuid5(NAMESPACE,f'{actor.business_id}:{row.page_hash}:{row.line}'))
    if row.kind=='customer_credit':
        if not row.customer_id or not row.due_on:sec.fail(422,'KHATA_CUSTOMER_AND_DUE_DATE_REQUIRED')
        debt=Receivable(customer_id=row.customer_id,number=row.number,currency=row.currency,amount_minor=row.amount_minor,
                        issued_on=row.day,due_on=row.due_on,request_id=identity)
        return add_receivable(debt,actor,db)
    if row.kind=='customer_receipt':
        if not row.receivable_id:sec.fail(422,'KHATA_RECEIVABLE_REQUIRED')
        ds=sec.original_dataset(db,actor.business_id)
        if ds.currency!=row.currency:sec.fail(409,'WORKSPACE_CURRENCY_CHANGED')
        return settle(row.receivable_id,Receipt(request_id=identity,amount_minor=row.amount_minor,received_on=row.day),actor,db)
    with owner_transaction(db,actor) as current:
        def write():
            ds=sec.original_dataset(db,current.business_id)
            if ds.currency!=row.currency:sec.fail(409,'WORKSPACE_CURRENCY_CHANGED')
            invoice=row.kind=='supplier_bill'
            body=InputRecord(request_id=identity,expected_revision=fingerprint(ds),kind='invoice' if invoice else 'cash',
                             direction='outflow' if row.kind=='outflow' else 'inflow',amount_minor=row.amount_minor,
                             effective_on=row.day,vendor_id=row.vendor_id,invoice_number=row.number,due_on=row.due_on)
            result=append_record(db,current,body)
            if result['replayed']:sec.fail(409,'CASH_REQUEST_ALREADY_USED')
            return result['source_id']
        return save_once(db,current,'khata-'+row.kind,ReviewedCashRow(**row.model_dump(),request_id=identity),write)
