"""Every industry, not only gold: any product can have a market price, and profit, alerts, advice and scenarios use it."""
from api.app import market
from api.app.ratelimit import limiter
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_market import grounded, net  # noqa: F401
from api.tests.test_monitor import run

SHOP = {'name': 'Rahman Fashion', 'industry': 'apparel', 'niche': "Women's clothing shop", 'business_types': ['retail'],
        'country': 'Bangladesh', 'city': 'Mirpur'}


def clothing_shop(c, name):
    signup(c, name)
    c.post('/api/business/step/identity', json=SHOP).raise_for_status()
    for item in ({'name': 'Cotton saree', 'unit': 'piece', 'quantity': '40', 'cost_minor': 150_000, 'price_minor': 200_000, 'expense_minor': 10_000},
                 {'name': 'Three-piece', 'unit': 'set', 'quantity': '20', 'cost_minor': 100_000, 'price_minor': 120_000}):
        c.post('/api/business/products', json=item).raise_for_status()
    c.post('/api/business/step/consent', json={'monitoring': True, 'alerts': True, 'external_research': True}).raise_for_status()
    return {p['name']: p for p in c.get('/api/business').json()['products']}


def market_price(c, product, value):
    return c.post('/api/market/quotes', json={'indicator': 'p:' + product['id'], 'value_minor': value,
                                              'observed_on': __import__('datetime').date.today().isoformat(), 'source': 'Shop next door'})


def test_any_product_gets_a_market_price_profit_alerts_and_advice(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    items = clothing_shop(c, 'any_clothes')
    view = c.get('/api/market').json()
    assert [p['name'] for p in view['products']] == ['Cotton saree', 'Three-piece'] and view['products'][0]['status'] == 'needs_quote'
    assert 'your products are priced from what you see in the market' in view['coverage_note']

    assert market_price(c, items['Cotton saree'], 170_000).status_code == 201
    saree = next(p for p in c.get('/api/market').json()['products'] if p['name'] == 'Cotton saree')
    assert saree['latest']['value_minor'] == 170_000 and saree['your_price_gap_bp'] == 1765  # you charge 17.6% more
    pos = next(p for p in c.get('/api/insights').json()['positions'] if p['name'] == 'Cotton saree')
    assert pos['reference']['basis'] == 'market' and pos['net_minor'] == 10_000 and pos['break_even_minor'] == 160_000

    advice = c.get('/api/advisor').json()['insights']
    above = next(i for i in advice if i['kind'] == 'priced_above_market')
    assert above['title'] == 'Cotton saree is priced 18% above the market' and len(above['strategies']) == 3

    at = __import__('api.app.security', fromlist=['now']).now()
    run(at)
    market_price(c, items['Cotton saree'], 158_000)  # the market drops below the 160,000 break-even
    assert run(at + 60)['alerts'] == 1
    alert = c.get('/api/alerts').json()['alerts'][0]
    assert alert['title'] == 'CRITICAL: Cotton saree profitability at risk' and 'buyers will actually pay' in alert['advice']

    limiter.reset()
    answer = c.post('/api/chat/message', json={'text': 'saree er dam 10% kome gele ki hobe?'}).json()['text']
    assert 'Base case (-10%)' in answer and '40 piece Cotton saree' in answer
    assert c.post('/api/market/quotes', json={'indicator': 'p:not-mine', 'value_minor': 1, 'observed_on': '2026-01-01', 'source': 'Somewhere'}).status_code == 404


def test_research_prices_each_product_with_citations(world, net, monkeypatch):  # noqa: F811
    c, _ = world
    items = clothing_shop(c, 'any_research')
    text = ('FINDING: Saree demand rises before Eid.\n'
            'COMPETITOR: Aarong | leader | Dhaka | National brand\n'
            'PRICE: 1 | 1,750 | 2026-11-20\n'
            'PRICE: 2 | 1300 | 2026-11-20\n'
            'PRICE: 9 | 999 | 2026-11-20\n')
    body = grounded(text)
    body['candidates'][0]['groundingMetadata']['groundingSupports'][2]['segment']['text'] = '1 | 1,750 | 2026-11-20'
    net['answer'] = body
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'test-key')
    c.post('/api/market/research', json={}).raise_for_status()
    prompt = net['gemini'][0]
    assert '1 (Cotton saree, per piece)' in prompt and '2 (Three-piece, per set)' in prompt and 'Mirpur' in prompt and '150' not in prompt
    products = {p['name']: p for p in c.get('/api/market').json()['products']}
    assert products['Cotton saree']['latest']['value_minor'] == 175_000 and products['Cotton saree']['latest']['kind'] == 'research'
    assert products['Three-piece']['latest'] is None  # its price line had no citation, so it was not stored
