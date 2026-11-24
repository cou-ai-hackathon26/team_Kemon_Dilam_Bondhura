from datetime import timedelta

from api.app.accounts import today_bd
from api.tests.test_accounts import signup, world
from api.tests.test_manual_inputs import record


def test_partial_advance_covers_gap_without_double_counting_or_writes(world):
    client, _ = world
    signup(client, 'collections_owner')
    today = today_bd()
    def day(offset):
        return (today + timedelta(days=offset)).isoformat()
    for amount, direction, offset in [(42000, 'outflow', 6), (60000, 'inflow', 25)]:
        response, _ = record(client, amount_minor=amount, direction=direction, status='scheduled', effective_on=day(offset))
        assert response.status_code == 201
    before = client.get('/api/workspace').json()
    scenario = dict(as_of=day(0), horizon_days=30, reserve_minor=0)
    response = client.post('/api/copilot/gap-fix', json=scenario)
    assert response.status_code == 200
    plan = response.json()
    assert plan['peak_gap_minor'] == 42000
    assert plan['options'] == [dict(source_id=before['events'][1]['source_record_id'],
                                    scheduled_on=day(25), collect_by=day(6), advance_minor=42000, remaining_gap_minor=0)]
    after = client.get('/api/workspace').json()
    assert before['audit'] == after['audit']
    assert before['dataset_revision'] == after['dataset_revision']
    assert after['month']['closing_balance_minor'] == 18000
    signup(client, 'separate_workspace')
    assert client.post('/api/copilot/gap-fix', json=scenario).json()['options'] == []
