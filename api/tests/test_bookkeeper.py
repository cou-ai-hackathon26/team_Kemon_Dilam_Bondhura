import sqlite3
import uuid

from api.app.accounts import COOKIE, today_bd
from api.tests.test_accounts import world, signup
from api.tests.test_manual_inputs import record


def select_cookie(client, value):
    client.cookies.clear()
    client.cookies.set(COOKIE, value, domain='testserver.local', path='/')


def test_invited_bookkeeper_only_records_in_shared_workspace(world):
    c, path = world
    unrelated = signup(c, 'separate_books')
    owner = signup(c, 'shared_books')
    owner_cookie = c.cookies.get(COOKIE)
    invite = c.post('/api/account/invitations', json={'role': 'bookkeeper'})
    assert invite.status_code == 201
    code = {'code': invite.json()['code']}
    assert c.post('/api/invitations/preview', json=code).json()['role'] == 'bookkeeper'
    assert c.get('/api/account/members').json()['invitations'][0]['role'] == 'bookkeeper'
    assert record(c)[0].status_code == 201
    invoice = dict(kind='invoice', vendor_id='supplier', invoice_number='BILL-1', due_on=str(today_bd()), amount_minor=150)
    result, _ = record(c, **invoice, trust_vendor=True)
    proposal = c.post('/api/proposals', json={'invoice_id': result.json()['source_id']}).json()
    signup(c, 'shared_recorder')
    joined = c.post('/api/account/join', json=code).json()
    assert joined['role'] == 'bookkeeper' and joined['business_id'] == owner['business_id']
    assert {w['role'] for w in c.get('/api/account/workspaces').json()['workspaces']} == {'owner', 'bookkeeper'}
    assert {m['role'] for m in c.get('/api/account/members').json()['members']} == {'owner', 'bookkeeper'}
    result, body = record(c, amount_minor=3750)
    assert result.status_code == 201 and c.post('/api/workspace/records', json=body).json()['replayed']
    invoice.update(invoice_number='BILL-2', vendor_id='other-supplier')
    assert record(c, **invoice, trust_vendor=True)[0].status_code == 403
    result, _ = record(c, **invoice)
    assert result.status_code == 201
    for route, data in [
        ('/api/proposals', {'invoice_id': result.json()['source_id']}),
        ('/api/proposals/' + proposal['proposal_id'] + '/approve', {'proposal_hash': proposal['action_hash'], 'decision': 'approve'}),
        ('/api/account/members/role', {'member_id': joined['user_id'], 'role': 'approver'}),
        ('/api/account/members/remove', {'member_id': owner['user_id']}),
        ('/api/account/invitations', {'role': 'bookkeeper'}),
        ('/api/account/workspaces/rename', {'name': 'New shop'}),
        ('/api/account/workspaces/clear', {'business_ids': [owner['business_id']]}),
        ('/api/account/switch', {'business_id': unrelated['business_id']}),
        ('/api/workspace/currency', {'currency': 'USD', 'expected_revision': body['expected_revision']}),
        ('/api/workspace/vendor-permission', {'vendor_id': 'other-supplier', 'enabled': True}),
    ]:
        assert c.post(route, json=data).status_code == 403, route
    assert c.post('/api/workspace/records', json=dict(body, business_id=unrelated['business_id'])).status_code == 422
    assert c.get('/api/copilot/briefing').status_code == 200
    assert c.post('/api/copilot/ask', json={'question': 'What is my cash balance?'}).status_code == 200
    offline = dict(kind='cash', amount_minor=99, effective_on=str(today_bd()), request_id=str(uuid.uuid4()),
                   user_id=joined['user_id'], business_id=joined['business_id'], currency='BDT')
    assert c.post('/api/workspace/offline-records', json=dict(offline, business_id=unrelated['business_id'])).status_code == 403
    assert c.post('/api/workspace/offline-records', json=offline).status_code == 201
    audit = c.get('/api/workspace').json()['audit']
    assert audit['chain_verified']
    assert any(e['kind'] == 'RECORD_ADDED' and e['actor_id'] == joined['user_id'] for e in audit['events'])
    select_cookie(c, owner_cookie)
    assert c.post('/api/account/members/role', json={'member_id': unrelated['user_id'], 'role': 'bookkeeper'}).status_code == 404
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT role FROM security_users WHERE user_id=?', (joined['user_id'],)).fetchone()[0] == 'viewer'
        assert not db.execute("SELECT 1 FROM trusted_vendors WHERE vendor_id='other-supplier'").fetchone()
        assert not db.execute('SELECT 1 FROM postings').fetchone()
        assert not db.execute('SELECT 1 FROM workspace_input_requests WHERE business_id=?', (unrelated['business_id'],)).fetchone()


def test_change_and_reinvite_bookkeeper(world):
    c, path = world
    owner = signup(c, 'bookkeeper_owner')
    owner_cookie = c.cookies.get(COOKIE)
    code = c.post('/api/account/invitations', json={'role': 'viewer'}).json()['code']
    signup(c, 'bookkeeper_member')
    personal_cookie = c.cookies.get(COOKIE)
    joined = c.post('/api/account/join', json={'code': code}).json()
    member = {'member_id': joined['user_id']}
    for role in ('bookkeeper', 'viewer', 'approver', 'bookkeeper'):
        old_cookie = c.cookies.get(COOKIE)
        select_cookie(c, owner_cookie)
        assert c.post('/api/account/members/role', json={**member, 'role': role}).status_code == 200
        select_cookie(c, old_cookie)
        assert c.get('/api/session').status_code == 401
        select_cookie(c, personal_cookie)
        assert c.post('/api/account/switch', json={'business_id': owner['business_id']}).json()['role'] == role
        assert record(c, amount_minor=10)[0].status_code == (201 if role == 'bookkeeper' else 403)
    select_cookie(c, owner_cookie)
    assert c.post('/api/account/members/remove', json=member).status_code == 200
    code = c.post('/api/account/invitations', json={'role': 'viewer'}).json()['code']
    select_cookie(c, personal_cookie)
    assert c.post('/api/account/join', json={'code': code}).json()['role'] == 'viewer'
    assert record(c)[0].status_code == 403
    with sqlite3.connect(path) as db:
        assert not db.execute('SELECT 1 FROM bookkeeper_seats WHERE user_id=?', (joined['user_id'],)).fetchone()
