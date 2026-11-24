import time
from contextlib import closing

from api.app import mailer, monitor
from api.app import security as sec
from api.app.database import connect
from api.app.notifications import email_alerts
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_monitor import gold_owner, quote, run


def test_export_includes_and_erase_removes_business_intelligence(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    session = gold_owner(c, 'bi_export')
    quote(c, 22_000_000)
    run(sec.now())
    quote(c, 20_000_000)
    run(sec.now() + 60)
    c.post('/api/advisor/feedback', json={'insight': 'risk:x', 'tier': 'A', 'response': 'doing'}).raise_for_status()
    bi = c.get('/api/workspace/export').json()['business_intelligence']
    assert bi['profile']['identity']['name'] == 'Golden Heritage BD' and bi['products'][0]['cost_minor'] == 19_400_000
    assert [q['value_minor'] for q in bi['market_quotes']] == [22_000_000, 20_000_000]
    assert bi['alerts'][0]['level'] == 3 and bi['advisor_feedback'][0]['tier'] == 'A'

    business = session['business_id']
    c.post('/api/account/workspaces/clear', json={'business_ids': [business], 'erase_customer_details': True}).raise_for_status()
    with closing(connect()) as db:
        for table in ('biz_profile', 'biz_products', 'biz_alerts', 'advisor_feedback', 'market_watch'):
            assert db.execute(f'SELECT COUNT(*) FROM {table} WHERE business_id=?', (business,)).fetchone()[0] == 0, table
        assert db.execute('SELECT COUNT(*) FROM market_observations WHERE business_id=?', (business,)).fetchone()[0] == 0
    assert run(sec.now() + 120)['businesses'] == 0


def test_critical_alert_email_has_no_amounts_and_retries(world, monkeypatch):  # noqa: F811
    c, _ = world
    sent, accept = [], {'ok': False}
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    monkeypatch.setattr(email_alerts, 'configured', lambda: True)
    monkeypatch.setattr(email_alerts, 'website', lambda: 'https://capzoryx.test')
    monkeypatch.setattr(email_alerts, 'verified_address', lambda db, b, u: 'owner@example.test')
    monkeypatch.setattr(mailer, 'send', lambda to, subject, text: sent.append((to, subject, text)) or accept['ok'])
    session = gold_owner(c, 'bi_email')
    quote(c, 22_000_000)
    at = sec.now()
    run(at)
    quote(c, 20_000_000)
    run(at + 60)
    assert sent[0][1] == 'Capzoryx: CRITICAL: 22K Gold profitability at risk'
    assert 'BDT' not in sent[0][2] and 'https://capzoryx.test/' in sent[0][2]
    with closing(connect()) as db:
        status = lambda: db.execute("SELECT status,attempts FROM notification_jobs WHERE business_id=? AND channel='market_email'",
                                    (session['business_id'],)).fetchone()
        assert status() == ('queued', 1)
        accept['ok'] = True
        monitor.deliver_email(db, session['business_id'], at + 1000)
        assert status() == ('sent', 2) and len(sent) == 2


def test_thousands_of_products_stay_fast(world):  # noqa: F811
    c, _ = world
    signup(c, 'bi_scale')
    c.post('/api/business/step/identity', json={'name': 'Big Wholesale', 'industry': 'grocery', 'niche': 'Rice and grain trading',
                                                 'business_types': ['wholesale'], 'country': 'Bangladesh', 'city': 'Dhaka'}).raise_for_status()
    rows = '\n'.join(f'Item {i},Grain,kg,{100 + i % 50},{(i % 90) + 10},{(i % 90) + 12}' for i in range(1500))
    imported = c.post('/api/business/import', json={'csv': 'Product,Category,Unit,Quantity,Cost,Price\n' + rows}).json()
    assert imported['added'] == 1500
    for path in ('/api/business', '/api/insights', '/api/advisor', '/api/market'):
        start = time.perf_counter()
        response = c.get(path)
        took = time.perf_counter() - start
        assert response.status_code == 200 and took < 5, (path, took)
    assert len(c.get('/api/insights').json()['positions']) == 1500
