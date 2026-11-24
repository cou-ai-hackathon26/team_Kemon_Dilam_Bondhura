"""Independent cash oracle, acceptance race/retry and verified reminder amounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from itertools import combinations
from random import Random
from time import perf_counter

import pytest

from api.app.accounts import today_bd
from api.app.contracts import ScenarioRequest
from api.app.copilot.gapfix import best_plan
from api.app.services.finance import Dataset, project_cashflow
from api.app.services.receivables import ExpectedReceipt
from api.tests.test_accounts import signup, world
from api.tests.test_manual_inputs import record
from api.tests.test_receivables import issue, post


def scenario(seed, extra_outflow=0):
    rng = Random(seed)
    as_of = date(2026, 11, 21)
    amounts = [rng.randint(10, 90) for _ in range(7)]
    receipts = [ExpectedReceipt(f'receipt-{i}', as_of + timedelta(days=rng.randint(6, 10)), amount)
                for i, amount in enumerate(amounts)]
    # Start with a gap known to be closable by <=3 receipts; the reference
    # solver below enumerates all choices independently of this construction.
    due = sum(sorted(amounts, reverse=True)[:1 + seed % 3]) - rng.randint(0, 5)
    days = [(1, due // 2), (4, due - due // 2), (7, extra_outflow)]
    dataset = Dataset(opening_source_id='opening', opening_on=as_of, as_of=as_of, opening_balance_minor=0,
                      currency='BDT', invoices=[], events=[
                          dict(source_record_id=f'wage-{i}', event_id=f'wage-{i}', direction='outflow',
                               status='scheduled', effective_on=as_of + timedelta(days=day), amount_minor=amount,
                               currency='BDT') for i, (day, amount) in enumerate(days) if amount])
    return dataset, ScenarioRequest(as_of=as_of, horizon_days=30, reserve_minor=0), receipts


def oracle(dataset, request, receipts):
    offsets = [(r.due_on - request.as_of).days for r in receipts]
    outflows = [(e.effective_on - request.as_of, e.amount_minor) for e in dataset.events]
    def peak(picked):
        worst = 0
        for day in range(1, request.horizon_days + 1):
            incoming = sum(r.amount_minor for i, r in enumerate(receipts) if i in picked or offsets[i] <= day)
            spent = sum(amount for offset, amount in outflows if offset.days <= day)
            worst = max(worst, spent - incoming)
        return worst
    return min((peak(group), size) for size in range(4) for group in combinations(range(len(receipts)), size))


@pytest.mark.parametrize('seed', range(20))
def test_twenty_feasible_scenarios(seed):
    ds, req, receipts = scenario(seed)
    baseline = project_cashflow(ds, req, receipts)
    start = perf_counter()
    plan = best_plan(ds, req, receipts, baseline)
    assert perf_counter() - start < 4
    assert (plan['remaining_gap_minor'], len(plan['actions'])) == oracle(ds, req, receipts)
    assert plan['remaining_gap_minor'] == 0
    assert plan['forecast']['closing_balance_minor'] == baseline['closing_balance_minor']
    assert all(d['inflow_minor'] >= 0 for d in plan['forecast']['days'])
    assert [d['outflow_minor'] for d in plan['forecast']['days']] == [d['outflow_minor'] for d in baseline['days']]


@pytest.mark.parametrize('extra', [100, 200, 2000])
def test_oracle_includes_unclosable_gaps(extra):
    ds, req, receipts = scenario(2, extra)
    baseline = project_cashflow(ds, req, receipts)
    plan = best_plan(ds, req, receipts, baseline)
    gap = max(d['shortfall_minor'] for d in baseline['days'])
    assert ((plan['remaining_gap_minor'], len(plan['actions'])) if plan else (gap, 0)) == oracle(ds, req, receipts)


def test_accept_is_atomic_and_tenant_scoped_and_fresh(world):
    c, _ = world
    signup(c, 'cashgap_owner')
    today = today_bd()
    fields = dict(as_of=str(today), horizon_days=30, reserve_minor=10000)
    _, debt, _ = issue(c, amount=12000)
    preview = lambda: c.post('/api/copilot/gap-fix', json=fields).json()['plan']
    old = preview()
    assert old['actions'][0]['source_id'] == debt
    assert record(c, amount_minor=1000)[0].status_code == 201
    assert c.post('/api/copilot/gap-fix/accept', json={'plan_id':old['id']}).status_code == 409
    plan = preview()
    before = c.get('/api/workspace').json()
    assert before['next_collection'] is None
    def accept():
        r = c.post('/api/copilot/gap-fix/accept', json={'plan_id':plan['id']})
        assert r.status_code == 200, r.text
        return r.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert sorted(r['replayed'] for r in results) == [False, True]
    tasks = c.get('/api/copilot/tasks').json()['tasks']
    assert len(tasks) == 1
    assert tasks[0]['action']['amount_minor'] == 9000
    assert c.get('/api/workspace').json()['next_collection'] == {'due_on':tasks[0]['due_on'],'amount_minor':9000}
    assert before['dataset_revision'] == c.get('/api/workspace').json()['dataset_revision']
    invite = c.post('/api/account/invitations', json={'role':'viewer'}).json()['code']
    signup(c, 'separate_cashgap')
    assert c.post('/api/copilot/gap-fix/accept', json={'plan_id':plan['id']}).status_code == 404
    assert c.get('/api/copilot/tasks').json()['tasks'] == []
    assert c.post('/api/account/join', json={'code':invite}).status_code == 200
    assert c.get('/api/workspace').json()['next_collection'] is None
    assert c.post('/api/copilot/gap-fix/accept', json={'plan_id':plan['id']}).status_code == 403
    assert c.post('/api/copilot/tasks/' + tasks[0]['id'], json={'status':'done'}).status_code == 403


def test_task_decision_and_reminder_never_post_money(world):
    c, _ = world
    signup(c, 'reminder_owner')
    _, debt, _ = issue(c, amount=12550)
    assert post(c, f'receivables/{debt}/settlements', received_on=str(today_bd()), amount_minor=5000).status_code == 201
    plan = c.post('/api/copilot/gap-fix', json=dict(as_of=str(today_bd()), horizon_days=7, reserve_minor=6000)).json()['plan']
    task = c.post('/api/copilot/gap-fix/accept', json={'plan_id':plan['id']}).json()['tasks'][0]
    before = c.get('/api/workspace').json()['dataset_revision']
    assert c.post('/api/copilot/tasks/' + task['id'], json={'status':'done'}).json()['tasks'] == []
    assert c.get('/api/workspace').json()['next_collection'] is None
    assert c.post('/api/copilot/tasks/' + task['id'], json={'status':'done'}).status_code == 200
    assert c.post('/api/copilot/tasks/' + task['id'], json={'status':'dismissed'}).status_code == 409
    msg = lambda lang: c.post('/api/copilot/message', json=dict(receivable_id=debt, language=lang))
    assert '75.50 BDT' in msg('en').json()['message']
    assert '৭৫.৫০ BDT' in msg('bn').json()['message']
    assert c.get('/api/copilot/briefing').json()['amount_owed_minor'] == 7550
    assert c.get('/api/workspace').json()['dataset_revision'] == before
    assert post(c, f'receivables/{debt}/settlements', received_on=str(today_bd()), amount_minor=7550).status_code == 201
    assert msg('en').status_code == 409
