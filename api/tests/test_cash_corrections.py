import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from api.app import security as sec
from api.app.accounts import today_bd
from api.tests.test_accounts import signup, world
from api.tests.test_khata import row as khata_row
from api.tests.test_manual_inputs import record
from api.tests.test_receivables import issue
from api.tests.test_statements import decision, upload
from api.tests.test_wallet_payments import share, message

URL = '/api/workspace/cash-corrections'


def correction(owner, source, **changes):
    body = dict(expected_business_id=owner['business_id'], expected_user_id=owner['user_id'],
                request_id=str(uuid.uuid4()), confirmed=True, original_id=source, previous_source_id=source,
                replacement=dict(amount_minor=125, effective_on=str(today_bd()), status='cleared', direction='outflow'))
    return dict(body, **changes)


def test_replace_schedule_then_void_preserves_evidence_and_gross_totals(world):
    c,path = world
    owner = signup(c, 'correct_owner')
    source = record(c, amount_minor=750, memo='paper books')[0].json()['source_id']
    original = c.get('/api/workspace/export').json()['original_dataset']
    assert c.get(URL).json()['rows'][0]['source_record_id'] == source
    first = correction(owner, source)
    response = c.post(URL, json=first)
    assert response.status_code == 201, response.text
    head = response.json()['id']
    bundle = c.get('/api/workspace').json()
    cash = bundle['snapshot']
    assert (cash['income_minor'],cash['expenses_minor'],cash['balance_minor']) == (0,125,-125)
    assert bundle['events'][0]['source_record_id'] == head and bundle['events'][0]['event_id'] == source
    assert bundle['events'][0]['note'] == 'paper books' and head in cash['source_ids']
    assert c.get(URL).json()['rows'][0]['source_record_id'] == head
    next_day = str(today_bd()+timedelta(days=1))
    second = correction(owner, source, previous_source_id=head, replacement=dict(
        amount_minor=250, direction='inflow', status='scheduled', effective_on=next_day))
    head2 = c.post(URL, json=second).json()['id']
    bundle = c.get('/api/workspace').json()
    assert bundle['snapshot']['balance_minor']==bundle['snapshot']['expenses_minor']==0
    assert bundle['week']['closing_balance_minor']==bundle['month']['closing_balance_minor']==250
    assert c.post(URL,json=first).json()['replayed']
    assert c.post(URL,json=correction(owner,source,replacement=None)).status_code==409
    void = correction(owner, source, previous_source_id=head2, replacement=None)
    assert c.post(URL, json=void).status_code == 201
    bundle=c.get('/api/workspace').json()
    assert bundle['week']['closing_balance_minor']==bundle['month']['closing_balance_minor']==0
    assert not bundle['events'] and not c.get(URL).json()['rows']
    assert c.post(URL,json=void).json()['replayed']
    exported=c.get('/api/workspace/export').json()
    assert exported['original_dataset']==original and not exported['current_dataset']['events']
    assert exported['cash_notes'][source]=='paper books'
    receipts=[e for e in exported['audit_events'] if e['kind']=='CASH_CORRECTED']
    assert [e['payload']['previous_source_id'] for e in receipts]==[source,head,head2]
    assert [e['payload']['previous']['amount_minor'] for e in receipts]==[750,125,250]
    assert bundle['audit']['chain_verified']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM source_records WHERE business_id=?',(owner['business_id'],)).fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE audit_events SET kind='changed' WHERE kind='CASH_CORRECTED'")


def test_same_revision_only_one_winner_and_replays(world):
    c,_=world;owner=signup(c,'correct_race')
    source=record(c)[0].json()['source_id']
    body=correction(owner,source)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=list(pool.map(lambda _:c.post(URL,json=body),range(3)))
    assert sorted(r.json()['replayed'] for r in responses)==[False,True,True]
    assert c.post(URL,json=dict(body,replacement=None)).status_code==409
    previous=responses[0].json()['id']
    requests=[correction(owner,source,previous_source_id=previous,replacement=dict(body['replacement'],amount_minor=n)) for n in (200,300)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda b:c.post(URL,json=b),requests))
    assert sorted(r.status_code for r in results)==[201,409]
    audit=c.get('/api/workspace').json()['audit']
    assert sum(e['kind']=='CASH_CORRECTED' for e in audit['events'])==2


def test_origin_restrictions_and_legacy_records(world,monkeypatch):
    c,_=world;owner=signup(c,'correct_legacy')
    # Simulate the signed audit format generated before entry_origin existed.
    audit=sec.audit
    def legacy(db,actor,trace,kind,payload):
        audit(db,actor,trace,kind,{k:v for k,v in payload.items() if k!='entry_origin'})
    monkeypatch.setattr(sec,'audit',legacy)
    source=record(c,amount_minor=120)[0].json()['source_id']
    invoice=record(c,kind='invoice',due_on=str(today_bd()),vendor_id='shop',invoice_number='SYN-1')[0].json()['source_id']
    _,debt,_=issue(c)
    assert c.post('/api/workspace/receivables/'+debt+'/settlements',json=dict(request_id=str(uuid.uuid4()),amount_minor=500,received_on=str(today_bd()))).status_code==201
    wallet=share(c,message('16.10','SYNT123456')).json()['id']
    assert c.post('/api/payments/'+wallet+'/confirm',json=dict(checked_wallet=True)).status_code==200
    assert c.post('/api/workspace/khata/confirm',json=khata_row(owner)).status_code==201
    stmt=upload(c,('Date,Ref,Amount,Balance\n'+str(today_bd())+',S1,34,34\n').encode()).json()
    assert c.post('/api/statements/confirm',json=decision(stmt)).status_code==200
    before=c.get('/api/workspace/export').json()
    assert [e['event_id'] for e in c.get(URL).json()['rows']]==[source]
    for target in [invoice,*[e['event_id'] for e in before['current_dataset']['events'] if e['event_id']!=source]]:
        assert c.post(URL,json=correction(owner,target)).status_code==404
    assert c.post(URL,json=correction(owner,source,replacement=None)).status_code==201
    after=c.get('/api/workspace/export').json()
    assert [e for e in before['current_dataset']['events'] if e['event_id']!=source]==after['current_dataset']['events']
    assert after['settlements']==before['settlements'] and after['receivables']==before['receivables']


def test_role_scope_and_confirmation(world):
    c,_=world;owner=signup(c,'correct_auth')
    source=record(c,amount_minor=50)[0].json()['source_id']
    body=correction(owner,source)
    assert c.post(URL,json=dict(body,confirmed=False)).status_code==422
    assert c.post(URL,json=dict(body,expected_user_id='wrong')).status_code==409
    assert c.post(URL,json=dict(body,replacement=dict(body['replacement'],amount_minor=0))).status_code==422
    assert c.post(URL,json=dict(body,replacement=dict(body['replacement'],amount_minor=50,direction='inflow'))).status_code==422
    assert c.post(URL,json=dict(body,replacement=dict(body['replacement'],effective_on=str(today_bd()+timedelta(days=1))))).status_code==422
    code=c.post('/api/account/invitations',json={'role':'bookkeeper'}).json()['code']
    other=signup(c,'correct_other')
    assert c.post(URL,json=body).status_code==409
    assert c.post(URL,json=correction(other,source)).status_code==404
    c.post('/api/account/join',json={'code':code}).raise_for_status()
    assert c.get(URL).status_code==403 and c.post(URL,json=body).status_code==403
