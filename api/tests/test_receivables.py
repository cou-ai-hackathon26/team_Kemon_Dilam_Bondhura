"""Receivable lifecycle, tenant isolation and cash/forecast reconciliation."""
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from api.app.accounts import today_bd
from api.tests.test_accounts import signup, world


def post(client, path, **fields):
    return client.post('/api/workspace/' + path, json=dict(request_id=str(uuid.uuid4()), **fields))


def issue(c, amount=12000):
    customer = post(c, 'customers', name='মেঘনা', phone='+880 1700000000')
    assert customer.status_code == 201, customer.text
    identity = customer.json()['id']
    fields = dict(customer_id=identity, currency='BDT', amount_minor=amount, issued_on=str(today_bd()),
                  due_on=str(today_bd() + timedelta(days=5)))
    debt = post(c, 'receivables', **fields)
    assert debt.status_code == 201, debt.text
    return identity, debt.json()['id'], fields


def test_reconciliation_and_private_fields(world):
    c, path = world
    signup(c, 'collections_owner')
    original = c.get('/api/workspace').json()
    customer, debt, _ = issue(c)
    bundle = c.get('/api/workspace').json()
    assert original['dataset_revision'] == bundle['dataset_revision']
    assert original['snapshot'] == bundle['snapshot']
    assert bundle['month']['days'][4]['inflow_minor'] == 12000
    assert debt in bundle['month']['days'][4]['source_ids']
    assert 'মেঘনা' not in str(bundle) and '1700000000' not in str(bundle)
    view = c.get('/api/workspace/receivables').json()
    assert view['customers'][0]['name'] == 'মেঘনা'
    assert view['customers'][0]['phone'] == '+880 1700000000'
    scenario = c.post('/api/cashflow/scenarios', json=dict(as_of=str(today_bd()), horizon_days=7,
                      delay_inflows_days=2, reserve_minor=0)).json()
    assert scenario['days'][6]['inflow_minor'] == 12000
    fixes = c.post('/api/copilot/gap-fix', json=dict(as_of=str(today_bd()), horizon_days=7, reserve_minor=5000)).json()
    assert fixes['options'][0]['source_id'] == debt
    assert c.post('/api/workspace/currency',json=dict(currency='USD',expected_revision=original['dataset_revision'])).status_code == 409
    route = f'/api/workspace/receivables/{debt}/settlements'
    fields = dict(request_id=str(uuid.uuid4()), amount_minor=4700, received_on=str(today_bd()))
    first = c.post(route, json=fields)
    assert first.status_code == 201, first.text
    assert c.post(route, json=fields).json()['replayed']
    assert c.post(route, json=dict(fields, amount_minor=4800)).status_code == 409
    bundle = c.get('/api/workspace').json()
    assert bundle['snapshot']['balance_minor'] == 4700
    assert bundle['month']['days'][4]['inflow_minor'] == 7300
    assert bundle['month']['closing_balance_minor'] == 12000
    assert post(c, f'receivables/{debt}/settlements', amount_minor=7301, received_on=str(today_bd())).status_code == 409
    assert post(c, f'receivables/{debt}/settlements', amount_minor=7300, received_on=str(today_bd())).status_code == 201
    final = c.get('/api/workspace').json()
    assert len(final['events']) == 2 and final['snapshot']['balance_minor'] == 12000
    assert debt not in final['month']['source_ids']
    assert c.post(f'/api/workspace/customers/{customer}/forget-phone', json={}).status_code == 200
    export = c.get('/api/workspace/export').json()
    assert export['customers'][0]['phone'] is None and len(export['settlements']) == 2
    assert export['receivables'][0]['open_minor'] == 0
    with sqlite3.connect(path) as db:
        assert not db.execute('SELECT 1 FROM customer_contacts').fetchone()
        assert 'মেঘনা' not in db.execute('SELECT name_enc FROM customers').fetchone()[0]
        for table in ['customers','receivables','receivable_settlements']:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f'DELETE FROM {table}')


def test_receivable_roles_tenants_and_concurrent_receipts(world):
    c, _ = world
    signup(c, 'receivable_owner')
    customer, debt, fields = issue(c)
    route = f'receivables/{debt}/settlements'
    def collect():
        return post(c, route, amount_minor=8000, received_on=str(today_bd())).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: collect(), range(2))) == [201, 409]
    invite = c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c, 'other_person')
    assert c.get('/api/workspace/receivables').json()['customers'] == []
    assert post(c,'receivables',**fields).status_code == 404
    assert collect() == 404
    assert c.post(f'/api/workspace/customers/{customer}/forget-phone',json={}).status_code == 404
    assert c.post('/api/account/join',json={'code':invite}).status_code == 200
    assert c.get('/api/workspace/receivables').status_code == 403
    assert post(c,'customers',name='New name').status_code == 403
    assert post(c,'receivables',**fields).status_code == 403
    assert collect() == 403
    assert 'customers' not in c.get('/api/workspace/export').json()


def test_clear_requires_notice_and_erases_personal_fields_after_restore(world):
    c, path = world
    business = signup(c, 'receivable_erase')['business_id']
    customer, _, _ = issue(c)
    selection = dict(business_ids=[business])
    assert c.post('/api/account/workspaces/clear',json=selection).status_code == 409
    assert c.post('/api/account/workspaces/clear',json=dict(selection,erase_customer_details=True)).status_code == 200
    with sqlite3.connect(path) as db:
        assert not db.execute('SELECT 1 FROM customer_keys WHERE business_id=?',(business,)).fetchone()
    assert c.post('/api/account/workspaces/restore',json=dict(business_id=business)).status_code == 200
    assert c.post('/api/account/switch',json=dict(business_id=business)).status_code == 200
    issue(c, amount=4000)
    view = c.get('/api/workspace/receivables').json()
    old = next(row for row in view['customers'] if row['id'] == customer)
    assert old['name'] is None and old['phone'] is None
    assert sum(row['open_minor'] for row in view['receivables']) == 16000
