from datetime import date

from api.app import insights
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_business import IDENTITY

BASE = '/api/insights'


def test_gold_position_counts_selling_costs_before_calling_it_profit(world):  # noqa: F811
    c, _ = world
    signup(c, 'insight_gold')
    c.post('/api/business/step/identity', json=IDENTITY).raise_for_status()
    c.post('/api/business/products', json={'name': '22K Gold', 'category': 'Gold', 'unit': 'bhori', 'quantity': '5',
                                           'cost_minor': 19_400_000, 'expense_minor': 700_000}).raise_for_status()
    first = c.get(BASE).json()['positions'][0]
    assert first['status'] == 'incomplete' and first['missing'] == ['reference']  # no local price yet: no guess
    c.post('/api/market/quotes', json={'indicator': 'gold_22k_local', 'value_minor': 22_000_000, 'observed_on': '2026-11-01', 'source': 'BAJUS'}).raise_for_status()
    c.post('/api/market/quotes', json={'indicator': 'gold_22k_local', 'value_minor': 20_000_000, 'observed_on': '2026-11-20', 'source': 'BAJUS'}).raise_for_status()
    view = c.get(BASE).json()
    gold = view['positions'][0]
    assert (gold['cost_minor'], gold['reference']['value_minor'], gold['gross_minor'], gold['expense_minor'], gold['net_minor']) == \
        (19_400_000, 20_000_000, 600_000, 700_000, -100_000)
    assert gold['break_even_minor'] == 20_100_000 and gold['status'] == 'loss' and gold['reference']['basis'] == 'market'
    assert gold['total_net_minor'] == -500_000 and gold['exposure_minor'] == 100_000_000 and gold['cushion_bp'] == -50
    rows = {r['move_pct']: r['total_net_minor'] for r in view['scenarios']['rows']}
    assert rows[0] == -500_000 and rows[-10] == 5 * (18_000_000 - 20_100_000) and rows[5] == 5 * (21_000_000 - 20_100_000)
    risk = next(a for a in view['capability']['areas'] if a['key'] == 'risk')
    assert risk['score'] == 0 and '100%' in risk['why']


def test_own_price_products_missing_data_and_growth_guards(world):  # noqa: F811
    c, _ = world
    signup(c, 'insight_shop')
    c.post('/api/business/step/identity', json=dict(IDENTITY, industry='apparel', niche='Saree shop')).raise_for_status()
    c.post('/api/business/products', json={'name': 'Cotton saree', 'unit': 'piece', 'quantity': '40', 'cost_minor': 150_000, 'price_minor': 200_000}).raise_for_status()
    c.post('/api/business/products', json={'name': 'Silk saree', 'unit': 'piece', 'price_minor': 900_000}).raise_for_status()
    view = c.get(BASE).json()
    cotton, silk = view['positions']
    assert cotton['reference']['basis'] == 'own_price' and cotton['net_minor'] == 50_000 and cotton['margin_bp'] == 3333 and cotton['status'] == 'ok'
    assert silk['status'] == 'incomplete' and silk['missing'] == ['cost']
    assert view['totals'] == {'cost_minor': 6_000_000, 'exposure_minor': 8_000_000, 'net_minor': 2_000_000, 'counted': 1, 'products': 2}
    assert len({r['total_net_minor'] for r in view['scenarios']['rows']}) == 1  # own prices do not move with the market
    g = view['growth']
    assert g['mom']['pct_bp'] is None and g['mom']['reason'] and g['cash_margin_bp'] is None
    cap = view['capability']
    missing = {a['key'] for a in cap['areas'] if a['score'] is None}
    assert {'financial', 'growth', 'inventory', 'marketing', 'retention'} <= missing and cap['coverage_pct'] < 60


def test_growth_uses_complete_months_and_refuses_a_zero_baseline():
    class E:
        def __init__(self, day, direction, amount):
            self.effective_on, self.direction, self.amount_minor, self.status = date.fromisoformat(day), direction, amount, 'cleared'

    class D:
        as_of = date(2026, 11, 23)
        events = [E('2025-10-05', 'inflow', 80_000), E('2026-09-10', 'inflow', 100_000), E('2026-10-02', 'inflow', 125_000),
                  E('2026-10-20', 'outflow', 100_000), E('2026-11-20', 'inflow', 999_999)]
    g = insights.growth(D())
    assert g['month'] == '2026-10' and g['income_minor'] == 125_000 and g['net_minor'] == 25_000 and g['cash_margin_bp'] == 2000
    assert g['mom']['pct_bp'] == 2500 and g['yoy']['pct_bp'] == 5625
    D.events = [E('2026-10-02', 'inflow', 125_000)]
    assert insights.growth(D())['mom']['pct_bp'] is None
    D.as_of = date(2026, 1, 15)
    D.events = [E('2025-11-02', 'inflow', 100), E('2025-12-02', 'inflow', 150)]
    assert insights.growth(D())['month'] == '2025-12' and insights.growth(D())['mom']['pct_bp'] == 5000


def test_capability_score_is_not_generous_and_flags_thin_data():
    positions = [dict(status='ok', margin_bp=2650, exposure_minor=100, cushion_bp=3000)]
    grow = dict(cash_margin_bp=None, mom=dict(pct_bp=None, reason='none'), months_recorded=0)
    cap = insights.capability(positions, grow, {})
    pricing = next(a for a in cap['areas'] if a['key'] == 'pricing')
    assert pricing['score'] == 66 and cap['preliminary'] and cap['coverage_pct'] == 35
    rich = insights.capability(positions, dict(cash_margin_bp=1000, mom=dict(pct_bp=-1000, reason=''), months_recorded=4), {})
    assert not rich['preliminary'] and next(a for a in rich['areas'] if a['key'] == 'financial')['score'] == 40
