from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_workspaces_sharing import second_person
from api.tests.test_payroll import login

BASE = '/api/business'
IDENTITY = {'name': 'Golden Heritage BD', 'industry': 'jewelry', 'niche': 'Gold trading and jewelry retail',
            'business_types': ['retail', 'wholesale'], 'country': 'Bangladesh', 'city': 'Dhaka', 'website': 'goldenheritage.example'}


def test_setup_saves_each_step_and_resumes(world):  # noqa: F811
    c, _ = world
    signup(c, 'gold_owner')
    first = c.get(BASE).json()
    assert first['step'] == 0 and not first['completed'] and first['profile'] == {}
    assert c.post(BASE + '/step/operations', json={'mode': 'offline'}).status_code == 409  # identity first
    bad = c.post(BASE + '/step/identity', json=dict(IDENTITY, industry='made-up'))
    assert bad.status_code == 422
    saved = c.post(BASE + '/step/identity', json=IDENTITY).json()
    assert saved['step'] == 1 and saved['profile']['identity']['website'] == 'https://goldenheritage.example'
    assert saved['niche_category'] == 'jewelry'

    gold = c.post(BASE + '/products', json={'name': '22K Gold', 'category': 'Gold', 'unit': 'vori', 'quantity': '5',
                                            'cost_minor': 19_400_000, 'price_minor': 22_000_000})
    assert gold.status_code == 201
    item = gold.json()['products'][0]
    assert item['unit'] == 'bhori' and item['market_key'] == 'gold_22k' and item['quantity'] == '5'
    assert c.post(BASE + '/products', json={'name': '22k gold', 'unit': 'Bhori'}).status_code == 409  # duplicate
    imported = c.post(BASE + '/import', json={'csv': 'Product,Category,Unit,Cost,Selling price\n'
                                                     'Silver ring,Silver,piece,"1,200",1800\n'
                                                     '22K Gold,Gold,tola,,\n'
                                                     ',,,,\n'
                                                     '21K Gold,Gold,bhori,abc,\n'
                                                     'Gold chain 18K,Gold,gram,,9000\n'}).json()
    assert imported['added'] == 2 and {s['reason'] for s in imported['skipped']} == {'PRODUCT_EXISTS', 'INVALID_ROW'}
    names = {p['name']: p for p in imported['products']}
    assert names['Silver ring']['cost_minor'] == 120_000 and names['Silver ring']['market_key'] == 'silver'
    assert names['Gold chain 18K']['market_key'] == 'gold_18k'
    assert any(g['field'] == 'cost' for g in imported['gaps'])
    pid = names['Silver ring']['id']
    edited = c.post(f'{BASE}/products/{pid}', json={'name': 'Silver ring', 'unit': 'pcs', 'cost_minor': 110_000}).json()
    assert next(p for p in edited['products'] if p['id'] == pid)['cost_minor'] == 110_000
    assert len(c.post(f'{BASE}/products/{pid}/delete', json={}).json()['products']) == 2

    c.post(BASE + '/step/products', json={}).raise_for_status()
    c.post(BASE + '/step/operations', json={'mode': 'hybrid', 'competitors': ['Diamond World', 'diamond world'],
                                            'channels': ['shop', 'facebook'], 'branches': 2}).raise_for_status()
    goals = c.post(BASE + '/step/goals', json={'goals': ['protect_capital', 'profit'], 'monthly_revenue_minor': 500_000_000,
                                               'monthly_expenses_minor': 300_000_000}).json()
    assert goals['profile']['goals']['goals'] == ['profit', 'protect_capital']
    assert goals['profile']['operations']['competitors'] == ['Diamond World']
    assert not any(g['field'] in ('finance', 'goals', 'competitors') for g in goals['gaps'])
    assert goals['step'] == 4 and not goals['completed']

    # Returning owner: progress is kept; finishing marks it complete once and editing later keeps it complete.
    c.post('/api/auth/logout', json={})
    login(c, 'gold_owner', 'A long unique demo passphrase 2026!')
    back = c.get(BASE).json()
    assert back['step'] == 4 and back['profile']['identity']['name'] == 'Golden Heritage BD'
    c.post(BASE + '/step/consent', json={'personalized_ai': True, 'alerts': True}).raise_for_status()
    done = c.post(BASE + '/complete', json={}).json()
    assert done['completed'] and done['step'] == 5
    again = c.post(BASE + '/step/identity', json=dict(IDENTITY, city='Chattogram')).json()
    assert again['completed'] and again['step'] == 5 and again['profile']['identity']['city'] == 'Chattogram'


def test_profiles_are_private_to_the_owner_of_each_business(world):  # noqa: F811
    c, _ = world
    signup(c, 'shop_a')
    c.post(BASE + '/step/identity', json=IDENTITY).raise_for_status()
    c.post(BASE + '/products', json={'name': 'Secret product', 'cost_minor': 100}).raise_for_status()
    code = c.post('/api/account/invitations', json={'role': 'approver', 'hours': 24}).json()['code']
    second_person(c, 'shop_b')
    c.post('/api/account/join', json={'code': code}).raise_for_status()
    shared = next(w for w in c.get('/api/account/workspaces').json()['workspaces'] if w['role'] == 'approver')
    c.post('/api/account/switch', json={'business_id': shared['business_id']}).raise_for_status()
    assert c.get(BASE).status_code == 403 and c.post(BASE + '/products', json={'name': 'x'}).status_code == 403
    own = next(w for w in c.get('/api/account/workspaces').json()['workspaces'] if w['role'] == 'owner')
    c.post('/api/account/switch', json={'business_id': own['business_id']}).raise_for_status()
    mine = c.get(BASE).json()
    assert mine['profile'] == {} and mine['products'] == []
    assert c.post(BASE + '/step/identity', json=dict(IDENTITY, name='Shop B')).json()['profile']['identity']['name'] == 'Shop B'
