"""Challenge 2: the cash radar stays honest when a customer pays late, so the alert does not cry wolf."""
import sqlite3
from datetime import date, timedelta

from api.app.services import radar as r
from api.app.services.finance import validated
from api.tests.test_accounts import signup, world  # noqa: F401

TODAY = date(2026, 11, 24)
D = lambda n: TODAY + timedelta(days=n)  # noqa: E731


def books(cash=15_000_00, bills=((5, 30_000_00),)):
    """Cash today and supplier bills due in n days."""
    return validated(dict(opening_source_id='opening', opening_on=str(TODAY - timedelta(days=60)), as_of=str(TODAY),
                          opening_balance_minor=cash, currency='BDT', events=[],
                          invoices=[dict(source_record_id=f'bill-{i}', invoice_id=f'BILL-{i}', vendor_id='supplier',
                                         invoice_number=f'S-{i}', issued_on=str(TODAY), due_on=str(D(n)), amount_minor=a,
                                         currency='BDT', status='open', memo='') for i, (n, a) in enumerate(bills)]))


def test_habit_is_learned_per_customer_then_business_then_due_date():
    payments = [('karim', D(-40), D(-33)), ('karim', D(-20), D(-11)), ('karim', D(-60), D(-52)),  # 7, 9, 8 days late
                ('nila', D(-30), D(-30))]
    per, overall = r.habits_from(payments)
    assert per['karim'] == (8, 3) and per['nila'] == (0, 1) and overall == 8
    items = r.expectations([('k1', 'karim', D(2), 25_000_00), ('n1', 'nila', D(2), 1_000_00), ('x1', 'new', D(2), 500_00)],
                           per, overall, TODAY)
    assert [(i['expected_on'], i['basis'], i['status']) for i in items] == [
        (str(D(10)), 'customer', 'usually_late'),
        (str(D(10)), 'business', 'usually_late'),   # one payment is not a habit: the business-wide habit is used
        (str(D(10)), 'business', 'usually_late')]
    assert r.expectations([('y', 'new', D(2), 1)], {}, None, TODAY)[0]['status'] == 'on_time'


def test_a_late_payer_moves_the_shortfall_into_view_honestly():
    # Karim's 25,000 is due in 2 days, a 30,000 bill in 5. On paper (due dates) cash never drops below the reserve;
    # Karim always pays about 8 days late, so in reality the bill comes first. That is a real alert, not a false one.
    items = r.expectations([('k1', 'karim', D(2), 25_000_00)], {'karim': (8, 3)}, 8, TODAY)
    view = r.outlook(books(), 5_000_00, TODAY, items)
    assert view['outlooks']['on_time']['first_shortfall_on'] is None
    assert view['level'] == 'alert' and view['breach_on'] == str(D(5)) and view['shortfall_minor'] == 20_000_00
    assert view['available_cash_minor'] == 15_000_00
    assert view['confirmed']['closing_minor'] == -15_000_00
    assert view['outlooks']['realistic']['closing_minor'] == 10_000_00
    assert view['outlooks']['stress']['closing_minor'] == -15_000_00


def test_radar_rebases_old_books_and_carries_over_unpaid_bills():
    old = D(-40)
    ds = books(bills=((2, 30_000_00),))
    invoice = ds.invoices[0].model_copy(update={'issued_on': old})
    stale = ds.model_copy(update={'as_of': old, 'invoices': [invoice]})
    view = r.outlook(stale, 0, TODAY, [])
    assert (view['level'], view['as_of'], view['breach_on']) == ('alert', str(TODAY), str(D(2)))
    overdue = stale.model_copy(update={'invoices': [invoice.model_copy(update={'due_on': D(-20)})]})
    assert r.outlook(overdue, 0, TODAY, [])['breach_on'] == str(D(1))


def test_money_a_little_late_is_a_quiet_watch_not_an_alarm():
    # A new customer's 25,000 is 4 days overdue. Expected soon; only if it never comes is there a shortfall.
    items = r.expectations([('c1', 'cust', D(-4), 25_000_00)], {}, None, TODAY)
    assert items[0]['status'] == 'later_than_usual' and items[0]['expected_on'] == str(D(1))
    view = r.outlook(books(), 5_000_00, TODAY, items)
    assert view['level'] == 'watch' and view['outlooks']['realistic']['first_shortfall_on'] is None
    assert view['depends_on'][0]['receivable_id'] == 'c1' and view['late_minor'] == 25_000_00


def test_long_overdue_money_is_not_counted_as_arriving_tomorrow():
    items = r.expectations([('old', 'gone', D(-45), 25_000_00)], {}, None, TODAY)
    assert items[0]['status'] == 'doubtful'
    view = r.outlook(books(), 5_000_00, TODAY, items)
    assert view['level'] == 'alert' and view['doubtful_minor'] == 25_000_00
    assert view['outlooks']['on_time']['first_shortfall_on'] is None  # the old forecast would have said all is well


def test_one_alert_per_episode_and_again_only_when_clearly_worse():
    db = sqlite3.connect(':memory:')
    r.schema(db)
    alert = dict(level='alert', breach_on=str(D(6)), shortfall_minor=10_000_00)
    t = 1_800_000_000
    assert r.should_alert(db, 'b', alert, t) is True
    assert r.should_alert(db, 'b', dict(alert, breach_on=str(D(5))), t + 3600) is False      # moved a day: no new alarm
    assert r.should_alert(db, 'b', dict(alert, shortfall_minor=11_000_00), t + 7200) is False  # 10% worse: no new alarm
    assert r.should_alert(db, 'b', dict(alert, breach_on=str(D(2))), t + 9000) is True        # 4 days earlier: tell again
    assert r.should_alert(db, 'b', dict(alert, breach_on=str(D(2))), t + 9900) is False
    assert r.should_alert(db, 'b', dict(alert, breach_on=str(D(2)), shortfall_minor=20_000_00), t + 10000) is True  # much bigger
    assert r.should_alert(db, 'b', dict(level='watch', breach_on=str(D(2)), shortfall_minor=1), t + 11000) is False  # watch: silent
    assert r.should_alert(db, 'b', dict(level='clear', breach_on=None, shortfall_minor=0), t + 12000) is False
    assert r.should_alert(db, 'b', alert, t + 13000) is True                                  # a new episode
    assert r.should_alert(db, 'b', alert, t + 13000 + 8 * 86400) is True                      # a week on: one reminder


def test_no_nagging_within_a_customers_habit():
    # 3 days overdue for a customer who pays 8 days late: still within habit. 10 days overdue: later than usual.
    items = r.expectations([('k1', 'karim', D(-3), 1), ('k2', 'karim', D(-10), 1)], {'karim': (8, 3)}, 8, TODAY)
    assert [i['status'] for i in items] == ['usually_late', 'later_than_usual']


def test_radar_resends_after_cumulative_deterioration():
    db = sqlite3.connect(':memory:')
    r.schema(db)
    original = dict(level='alert', breach_on=str(D(7)), shortfall_minor=100)
    assert r.should_alert(db, 'b', original, 1000)
    assert not r.should_alert(db, 'b', dict(original, shortfall_minor=115), 1100)
    assert r.should_alert(db, 'b', dict(original, shortfall_minor=126), 1200)


def test_radar_endpoint(world):  # noqa: F811
    import uuid
    from api.app.accounts import today_bd
    c, _ = world
    signup(c, 'radar_owner')
    customer = c.post('/api/workspace/customers', json=dict(request_id=str(uuid.uuid4()), name='Karim Store')).json()['id']
    c.post('/api/workspace/receivables', json=dict(request_id=str(uuid.uuid4()), customer_id=customer, currency='BDT',
                                                   amount_minor=25_000_00, issued_on=str(today_bd()),
                                                   due_on=str(today_bd() + timedelta(days=3)))).raise_for_status()
    view = c.get('/api/radar').json()
    assert view['level'] in ('clear', 'watch', 'alert') and set(view['outlooks']) == {'on_time', 'realistic', 'stress'}
    assert view['receivables'][0]['customer'] == 'Karim Store' and view['receivables'][0]['basis'] == 'due_date'
