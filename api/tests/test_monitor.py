from contextlib import closing
from datetime import date, timedelta

from api.app import monitor
from api.app import security as sec
from api.app.database import connect
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_business import IDENTITY
from api.tests.test_payroll import login

PASSWORD = 'A long unique demo passphrase 2026!'


def gold_owner(c, name, monitoring=True, alerts=True):
    signup(c, name)
    c.post('/api/business/step/identity', json=IDENTITY).raise_for_status()
    c.post('/api/business/products', json={'name': '22K Gold', 'category': 'Gold', 'unit': 'bhori', 'quantity': '5',
                                           'cost_minor': 19_400_000, 'expense_minor': 700_000}).raise_for_status()
    c.post('/api/business/step/consent', json={'monitoring': monitoring, 'alerts': alerts}).raise_for_status()
    return c.get('/api/session').json()


def quote(c, value, days_ago=0):
    c.post('/api/market/quotes', json={'indicator': 'gold_22k_local', 'value_minor': value,
                                       'observed_on': (date.today() - timedelta(days=days_ago)).isoformat(), 'source': 'BAJUS'}).raise_for_status()


def run(at):
    with closing(connect()) as db:
        return monitor.tick(db, at)


def jobs(business, channel):
    with closing(connect()) as db:
        return db.execute("SELECT run_after,payload_json FROM notification_jobs WHERE business_id=? AND channel=? AND payload_json LIKE '%market_risk%'",
                          (business, channel)).fetchall()


def test_offline_owner_gets_one_critical_alert_with_real_numbers(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    session = gold_owner(c, 'monitor_gold')
    business, user = session['business_id'], session['user_id']
    with closing(connect()) as db, db:
        db.execute('INSERT INTO telegram_links(business_id,user_id,confirmed_at) VALUES (?,?,?)', (business, user, sec.now()))
    quote(c, 22_000_000)
    c.post('/api/auth/logout', json={})  # J: nobody is signed in while monitoring runs
    at = sec.now()
    assert run(at)['alerts'] == 0  # 220,000 leaves a healthy buffer over the 201,000 break-even

    login(c, 'monitor_gold', PASSWORD)
    quote(c, 20_000_000)
    c.post('/api/auth/logout', json={})
    assert run(at + 60) == {'businesses': 1, 'fetched': 0, 'alerts': 1}
    assert run(at + 120)['alerts'] == 0  # same price: no repeat

    login(c, 'monitor_gold', PASSWORD)
    alert = c.get('/api/alerts').json()['alerts'][0]
    assert alert['level'] == 3 and alert['title'] == 'CRITICAL: 22K Gold profitability at risk'
    text = '\n'.join(alert['lines'])
    for expected in ('Purchase cost: BDT 194,000.00', 'Current reference price: BDT 200,000.00', 'Estimated selling costs: BDT 7,000.00',
                     'Estimated net position: -BDT 1,000.00 per unit, -BDT 5,000.00 on 5 bhori', 'Break-even price: BDT 201,000.00',
                     'Change since the last check: -9.1%'):
        assert expected in text
    assert 'nothing is sold automatically' in alert['advice']
    assert {d['channel'] for d in alert['delivery']} >= {'inapp', 'telegram'}
    telegram_jobs = jobs(business, 'telegram')
    assert len(telegram_jobs) == 1 and telegram_jobs[0][0] == at + 60  # critical ignores quiet hours
    with closing(connect()) as db:
        assert 'Estimated net position: -BDT 1,000.00' in monitor.alert_text(db, business, alert['id'])

    quote(c, 19_900_000)
    assert run(at + 180)['alerts'] == 0  # still critical, inside the cooldown
    quote(c, 19_800_000)
    assert run(at + 7 * 3600)['alerts'] == 1  # cooldown over and the price moved again
    quote(c, 23_000_000)
    run(at + 8 * 3600)
    latest = c.get('/api/alerts').json()['alerts'][0]
    assert latest['title'] == '22K Gold is back above break-even' and latest['level'] == 1
    assert len(jobs(business, 'telegram')) == 2  # recovery stays in the app

    c.post('/api/auth/logout', json={})
    signup(c, 'monitor_other')
    assert c.get('/api/alerts').json()['alerts'] == []


def test_no_alerts_from_stale_prices_or_without_permission(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    stale = gold_owner(c, 'monitor_stale')
    quote(c, 19_000_000, days_ago=10)
    c.post('/api/auth/logout', json={})
    quiet = gold_owner(c, 'monitor_quiet', alerts=False)
    quote(c, 19_000_000)
    c.post('/api/auth/logout', json={})
    off = gold_owner(c, 'monitor_off', monitoring=False)
    quote(c, 19_000_000)
    result = run(sec.now())
    assert result['businesses'] == 2 and result['alerts'] == 0
    with closing(connect()) as db:
        count = lambda b: db.execute('SELECT COUNT(*) FROM biz_alerts WHERE business_id=?', (b,)).fetchone()[0]
        assert count(stale['business_id']) == 0  # a 10-day-old price never raises an alert
        assert count(quiet['business_id']) == 1  # recorded for the app, but not sent anywhere
        assert count(off['business_id']) == 0
    assert jobs(quiet['business_id'], 'inapp') == []


def test_lease_lets_one_worker_run(world):  # noqa: F811
    with closing(connect()) as db:
        at = sec.now()
        assert monitor.lease(db, 'test-lease', 60, at)
        monkey = monitor.HOLDER
        monitor.HOLDER = 'other-worker'
        try:
            assert not monitor.lease(db, 'test-lease', 60, at + 1)
            assert monitor.lease(db, 'test-lease', 60, at + 61)
        finally:
            monitor.HOLDER = monkey
