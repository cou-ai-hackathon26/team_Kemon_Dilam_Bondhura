"""Fold signed correction receipts into a copy of the cash ledger for reporting."""
from api.app import security as sec

KIND = 'CASH_CORRECTED'


def effective_events(db, business, raw, audit=None):
    if audit is None:
        if not db.execute("""SELECT 1 FROM audit_events WHERE business_id=? AND kind='CASH_CORRECTED'
                             UNION ALL SELECT 1 FROM audit_history_changes WHERE business_id=? LIMIT 1""",
                          (business, business)).fetchone():
            return
        audit = sec.verified_audit(db, business)
    positions = {row['event_id']: index for index, row in enumerate(raw['events'])}
    for entry in audit:
        if entry['kind'] != KIND:
            continue
        receipt = entry['payload']
        original = receipt['original_id']
        index = positions.get(original)
        current = raw['events'][index] if index is not None else None
        if not current or current['invoice_id'] or current['source_record_id'] != receipt['previous_source_id']:
            sec.fail(409, 'CORRECTION_INTEGRITY_FAILURE')
        if any(current[key] != value for key,value in receipt['previous'].items()):
            sec.fail(409, 'CORRECTION_INTEGRITY_FAILURE')
        after = receipt['replacement']
        raw['events'][index] = None if after is None else dict(current, **after, source_record_id=entry['trace_id'])
        raw['as_of'] = max(raw['as_of'], receipt['observed_on'])
    raw['events'] = [row for row in raw['events'] if row is not None]


def eligible_ids(db, business, audit):
    """Use signed provenance. Older input receipts predate the explicit entry-origin field."""
    manual = set()
    legacy = set()
    linked = set()
    for entry in audit:
        payload = entry['payload']
        if entry['kind'] == 'RECORD_ADDED' and payload['kind'] == 'event':
            if payload.get('entry_origin') == 'manual':
                manual.add(payload['source_id'])
            elif 'entry_origin' not in payload:
                legacy.add(payload['source_id'])
        if entry['kind'] in ('RECEIVABLE_SETTLED','PAYMENT_CONFIRMED','STATEMENT_ROW_IMPORTED'):
            linked.add(payload['cash_record_id'])
    if legacy:
        # Historical khata rows returned the cash source as the result of their collection request.
        linked.update(row[0] for row in db.execute('SELECT result_id FROM collection_requests WHERE business_id=?',
                                                   (business,)))
    return (manual | legacy) - linked
