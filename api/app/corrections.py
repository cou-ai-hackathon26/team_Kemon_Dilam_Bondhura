"""Owner-confirmed corrections to standalone manual cash. Each signed receipt is the correction entry."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract, ISODate, PositiveMinor, RecordId
from api.app.receivables import RequestId, owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.cash_corrections import KIND, eligible_ids

router = APIRouter(prefix='/api/workspace')


class Replacement(Contract):
    amount_minor: PositiveMinor
    direction: Literal['inflow','outflow']
    status: Literal['cleared','scheduled']
    effective_on: ISODate


class Correction(Contract):
    request_id: RequestId
    expected_business_id: RecordId
    expected_user_id: RecordId
    original_id: RecordId
    previous_source_id: RecordId
    confirmed: bool = Field(strict=True)
    # A null replacement explicitly voids a mistaken entry.
    replacement: Replacement | None


@router.get('/cash-corrections')
def correctable(actor=Depends(principal), db=Depends(db_connection)):
    if actor.role != 'owner':
        sec.fail(403, 'OWNER_REQUIRED')
    db.execute('BEGIN')
    with db:
        current = sec.recheck(db, actor)
        if current.role != 'owner':
            sec.fail(403, 'OWNER_REQUIRED')
        audit = sec.verified_audit(db, current.business_id)
        eligible = eligible_ids(db, current.business_id, audit)
        dataset = sec.runtime_dataset(db, current.business_id, verified_history=audit)
        rows = [e.model_dump(mode='json') for e in dataset.events if e.event_id in eligible and e.invoice_id is None]
        return dict(rows=rows, business_id=current.business_id, user_id=current.user_id, simulation_only=True)


@router.post('/cash-corrections', status_code=201)
def correct(body: Correction, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as current:
        business = current.business_id
        if (business, current.user_id) != (body.expected_business_id, body.expected_user_id):
            sec.fail(409, 'DRAFT_WORKSPACE_MISMATCH')
        if not body.confirmed:
            sec.fail(422, 'CORRECTION_CONFIRMATION_REQUIRED')
        audit = sec.verified_audit(db, business)
        identity = 'correction-' + body.request_id
        replacement = body.replacement.model_dump(mode='json') if body.replacement else None
        receipt = dict(original_id=body.original_id, previous_source_id=body.previous_source_id, replacement=replacement)
        prior = next((entry for entry in audit if entry['kind']==KIND and entry['trace_id']==identity), None)
        if prior:
            if prior['actor_id'] != current.user_id or any(prior['payload'][k] != v for k,v in receipt.items()):
                sec.fail(409, 'INPUT_REQUEST_CONFLICT')
            return dict(id=identity, replayed=True, simulation_only=True)
        if body.original_id not in eligible_ids(db, business, audit):
            sec.fail(404, 'CORRECTABLE_CASH_RECORD_NOT_FOUND')
        ds = sec.runtime_dataset(db, business, verified_history=audit)
        row = next((e for e in ds.events if e.event_id==body.original_id and e.invoice_id is None), None)
        if row is None or row.source_record_id != body.previous_source_id:
            sec.fail(409, 'CORRECTION_CHANGED_REFRESH_REQUIRED')
        values = row.model_dump(mode='json')
        before = {key:values[key] for key in Replacement.model_fields}
        if replacement is not None:
            if before == replacement:
                sec.fail(422, 'CORRECTION_UNCHANGED')
            if body.replacement.effective_on < ds.opening_on:
                sec.fail(422, 'DATE_BEFORE_OPENING')
            if body.replacement.status == 'cleared' and body.replacement.effective_on > today_bd():
                sec.fail(422, 'CLEARED_DATE_IN_FUTURE')
        sec.audit(db, current, identity, KIND, dict(receipt, previous=before, observed_on=today_bd().isoformat()))
        # Validate new source identities and all economic invariants before commit.
        sec.runtime_dataset(db, business)
        return dict(id=identity, replayed=False, simulation_only=True)
