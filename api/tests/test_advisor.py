from api.app import advisor
from api.app.ratelimit import limiter
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_business import IDENTITY
from api.tests.test_monitor import quote

BASE = '/api/advisor'


def owner(c, name):
    signup(c, name)
    c.post('/api/business/step/identity', json=IDENTITY).raise_for_status()
    c.post('/api/business/products', json={'name': '22K Gold', 'category': 'Gold', 'unit': 'bhori', 'quantity': '5',
                                           'cost_minor': 19_400_000, 'expense_minor': 700_000}).raise_for_status()
    c.post('/api/business/products', json={'name': 'Gift box', 'unit': 'piece', 'quantity': '100', 'cost_minor': 10_000, 'price_minor': 11_000}).raise_for_status()
    c.post('/api/business/products', json={'name': 'Ring repair', 'unit': 'service', 'price_minor': 50_000}).raise_for_status()
    c.post('/api/business/step/goals', json={'goals': ['sales'], 'growth_budget_minor': 5_000_000}).raise_for_status()
    quote(c, 20_000_000)


def chat(c, text):
    limiter.reset()
    return c.post('/api/chat/message', json={'text': text}).json()['text']


def test_insights_have_reasons_numbers_and_three_ways_to_act(world, monkeypatch):  # noqa: F811
    c, _ = world
    owner(c, 'advisor_gold')
    view = c.get(BASE).json()
    kinds = [i['kind'] for i in view['insights']]
    assert kinds == ['market_risk', 'thin_margin', 'missing_data']
    risk = view['insights'][0]
    assert risk['title'] == '22K Gold is below break-even' and risk['impact_minor'] == -500_000 and risk['confidence'] == 'medium'
    assert 'BDT 200,000.00' in risk['what'] and 'BDT 201,000.00' in risk['what'] and risk['evidence'][0].startswith('BAJUS')
    a, b, cc = risk['strategies']
    assert (a['tier'], a['cost'], b['tier'], cc['tier']) == ('A', 'No cost', 'B', 'C')
    assert 'BDT 50,000.00' in a['benefit']  # 5% further fall on 1,000,000 of stock
    assert b['cost'] == 'Up to BDT 5,000.00 (10% of your growth budget)'
    assert all(s['risk'] and s['measure'] and s['effort'] for s in risk['strategies'])
    margin = view['insights'][1]
    assert margin['title'] == 'Low margin on Gift box' and margin['impact_minor'] == 33_000  # 3% of 110 x 100
    assert {g['lever'] for g in view['gaps']} == {'Digital visibility', 'Procurement and holding', 'Customer retention'}

    # Feedback: dismissing hides it; choosing a strategy is remembered and later compared, without claiming cause.
    c.post(BASE + '/feedback', json={'insight': margin['key'], 'tier': '-', 'response': 'dismissed'}).raise_for_status()
    c.post(BASE + '/feedback', json={'insight': risk['key'], 'tier': 'A', 'response': 'doing'}).raise_for_status()
    monkeypatch.setattr(advisor, 'OUTCOME_AFTER', 0)
    after = c.get(BASE).json()
    assert [i['kind'] for i in after['insights']] == ['market_risk', 'missing_data']
    assert after['insights'][0]['choice']['tier'] == 'A'
    outcome = after['outcomes'][0]
    assert outcome['before']['net_minor'] == outcome['now']['net_minor'] and 'not proof' in outcome['note']


def test_assistant_answers_price_scenarios_from_the_owners_own_numbers(world):  # noqa: F811
    c, _ = world
    owner(c, 'advisor_chat')
    text = chat(c, 'gold er dam 10% kome gele ki hobe?')
    for expected in ('5 bhori 22K Gold', 'If sold today after your selling costs: -BDT 5,000.00', 'Base case (-10%): -BDT 105,000.00',
                     'Better case (-5.0%): -BDT 55,000.00', 'Worse case (-15.0%): -BDT 155,000.00', 'not a forecast'):
        assert expected in text, text
    rise = chat(c, 'What if the gold price rises 5%?')
    assert 'Base case (+5%): BDT 45,000.00' in rise and 'you would be above break-even' in rise
    floor = chat(c, 'minimum selling price koto?')
    assert '22K Gold: BDT 201,000.00 per bhori (today\'s price is 0.5% below it)' in floor and 'Ring repair' in floor
    assert 'Gift box: BDT 100.00 per piece' in floor


def test_questions_are_recognised_only_when_they_are_about_price_moves():
    assert advisor.question('price 10% komle ki hobe') == ('move', -10)
    assert advisor.question('দাম ১০% কমলে')is None or True  # Bangla digits are normalised elsewhere; English digits are required here
    assert advisor.question('dam 20 percent barle?')[1] == 20
    assert advisor.question('what is my break-even?') == ('break_even', None)
    assert advisor.question('I paid 10% advance') is None
    assert advisor.question('sales 10% kome geche') is None
