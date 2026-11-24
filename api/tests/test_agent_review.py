from datetime import timedelta

from api.app.accounts import today_bd
from api.tests.test_accounts import signup, world
from api.tests.test_receivables import post


def test_owner_review_ranks_distinct_overdue_customers_and_is_read_only(world):
    client, _ = world
    path = '/api/copilot/agent-review'
    assert client.get(path).status_code == 401
    signup(client, 'review_owner')
    today = today_bd()
    earlier = str(today - timedelta(days=6))
    people = [post(client, 'customers', name=name).json()['id'] for name in ('A', 'B', 'C', 'D')]
    def bill(customer, amount, due=earlier):
        return post(client, 'receivables', customer_id=customer, amount_minor=amount, issued_on=earlier,
                    due_on=due, currency='BDT').json()['id']
    selected = [bill(people[0], 700), bill(people[1], 300), bill(people[2], 200)]
    bill(people[0], 600)
    bill(people[3], 50)
    bill(people[3], 10000, str(today))
    before = client.get('/api/workspace').json()
    reviews = client.get(path).json()['collections']
    assert [row['receivable_id'] for row in reviews] == selected
    assert [row['customer_name'] for row in reviews] == ['A', 'B', 'C']
    assert all(row['probability_7d'] is None for row in reviews)
    assert 'phone' not in str(reviews)
    assert client.get('/api/workspace').json()['audit'] == before['audit']
    assert client.post('/api/workspace/features/agent_mode', json={'enabled': False}).status_code == 200
    assert client.get(path).status_code == 503
    signup(client, 'other_review_owner')
    assert client.get(path).json()['collections'] == []


def test_earlier_breach_prepares_a_new_plan_but_creates_no_tasks(world):
    import sqlite3
    from api.tests.test_manual_inputs import record
    from api.tests.test_notification_inbox import local
    from api.app.notifications.scheduler import tick_business

    client, path = world
    owner = signup(client, 'review_forecast')
    today = today_bd()
    def scheduled(amount, days, direction):
        result, _ = record(client, amount_minor=amount, effective_on=str(today+timedelta(days=days)),
                           status='scheduled', direction=direction)
        assert result.status_code == 201

    scheduled(9000, 10, 'inflow')
    scheduled(5000, 5, 'outflow')
    with sqlite3.connect(path) as db:
        business = owner['business_id']
        tick_business(db, business, local(today, 9))
        scheduled(2500, 2, 'outflow')
        tick_business(db, business, local(today, 10))
        tick_business(db, business, local(today, 11))
        plans = db.execute('SELECT breach_date,status FROM gap_plans ORDER BY breach_date').fetchall()
        assert list(map(tuple, plans)) == [(str(today+timedelta(days=n)), 'proposed') for n in (2, 5)]
        assert db.execute('SELECT count(*) FROM copilot_tasks').fetchone()[0] == 0


def test_reminder_handoff_rechecks_balance_and_limits_each_customer(world):
    import uuid
    from api.tests.test_receivables import issue
    client, _ = world
    signup(client, 'review_reminder')
    _, debt, _ = issue(client)
    draft = client.post('/api/copilot/message',json={'receivable_id':debt}).json()['message']
    approve = '/api/copilot/message/approve'
    body = dict(receivable_id=debt, request_id=str(uuid.uuid4()), reviewed_message=draft)
    post(client, f'receivables/{debt}/settlements', received_on=str(today_bd()), amount_minor=100)
    assert client.post(approve, json=body).status_code == 409
    body['reviewed_message'] = client.post('/api/copilot/message',json={'receivable_id':debt}).json()['message']
    assert client.post(approve, json=body).status_code == 200
    assert client.post(approve, json=body).status_code == 200
    assert client.post(approve, json={**body, 'request_id':str(uuid.uuid4())}).status_code == 409
    post(client, f'receivables/{debt}/settlements', received_on=str(today_bd()), amount_minor=11900)
    assert client.post(approve, json=body).status_code == 409
