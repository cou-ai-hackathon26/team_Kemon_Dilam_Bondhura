import sqlite3

import pytest

from api.app.accounts import today_bd
from api.app.ratelimit import limiter
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_workspaces_sharing import second_person

BASE = '/api/payroll'


def login(c, username, password):
    limiter.reset()
    r = c.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.text


def test_invite_links_let_each_employee_see_only_their_own_pay(world, monkeypatch):  # noqa: F811
    c, path = world
    monkeypatch.setenv('SAFELEDGER_PUBLIC_URL', 'https://capzoryx.test')
    signup(c, 'payroll_admin')
    period = today_bd().strftime('%Y-%m')
    assert c.get(BASE).json()['employees'] == []
    team = {}
    for name, role in [('Rahim Uddin', 'Store manager'), ('Salma Begum', 'Cashier'), ('Jamal Mia', 'Delivery')]:
        team[name] = c.post(BASE + '/employees', json={'name': name, 'designation': role, 'salary_minor': 2_500_000}).json()['id']
    view = c.get(BASE).json()
    assert view['summary']['active'] == 3 and view['summary']['monthly_salary_minor'] == 7_500_000
    assert view['summary']['unpaid_count'] == 3 and not any(e['invited'] or e['username'] for e in view['employees'])

    link = c.post(f"{BASE}/employees/{team['Rahim Uddin']}/invite", json={}).json()['link']
    assert link.startswith('https://capzoryx.test/?employee=')
    code = link.split('employee=')[1]
    replaced = c.post(f"{BASE}/employees/{team['Salma Begum']}/invite", json={}).json()['link'].split('employee=')[1]
    salma = c.post(f"{BASE}/employees/{team['Salma Begum']}/invite", json={}).json()['link'].split('employee=')[1]
    assert next(e for e in c.get(BASE).json()['employees'] if e['name'] == 'Rahim Uddin')['invited']

    c.post(BASE + '/salaries', json={'period': period}).raise_for_status()
    c.post(BASE + '/bonus', json={'period': period, 'amount_minor': 500_000, 'note': 'Eid bonus',
                                  'employee_ids': [team['Rahim Uddin']]}).raise_for_status()
    assert len(c.get('/api/workspace').json()['events']) == 4  # three salaries and one bonus reach the books
    c.post('/api/auth/logout', json={})

    # Rahim opens his link, sees who invited him, and picks his own username and password.
    limiter.reset()
    shown = c.post(BASE + '/invite/check', json={'code': code}).json()
    assert shown['name'] == 'Rahim Uddin' and shown['employer'] == 'payroll_admin'
    joined = c.post(BASE + '/invite/accept', json={'code': code, 'username': 'Rahim_U', 'password': 'Rahim own passphrase 1'})
    assert joined.status_code == 201
    mine = c.get(BASE + '/me').json()
    job = mine['employments'][0]
    assert mine['employee_only'] and job['name'] == 'Rahim Uddin' and job['designation'] == 'Store manager'
    assert sorted(p['kind'] for p in job['payments']) == ['bonus', 'salary'] and job['year_to_date_minor'] == 3_000_000
    assert c.get(BASE).json()['employees'] == [] and c.get('/api/workspace').json()['events'] == []  # nothing of the employer's
    assert c.post(BASE + '/invite/accept', json={'code': code, 'username': 'someone', 'password': 'Another passphrase 2'}).status_code == 404

    # Links are single use and personal; a replaced link stops working; usernames stay unique.
    limiter.reset()
    assert c.post(BASE + '/invite/check', json={'code': replaced}).status_code == 404
    assert c.post(BASE + '/invite/accept', json={'code': salma, 'username': 'rahim_u', 'password': 'Salma passphrase 33'}).status_code == 409
    c.post(BASE + '/invite/accept', json={'code': salma, 'username': 'salma_b', 'password': 'Salma passphrase 33'}).raise_for_status()
    job = c.get(BASE + '/me').json()['employments'][0]
    assert job['name'] == 'Salma Begum' and [p['kind'] for p in job['payments']] == ['salary']

    login(c, 'rahim_u', 'Rahim own passphrase 1')  # he signs in normally afterwards
    assert c.get(BASE + '/me').json()['employments'][0]['name'] == 'Rahim Uddin'
    login(c, 'payroll_admin', 'A long unique demo passphrase 2026!')
    assert c.post(f"{BASE}/employees/{team['Rahim Uddin']}/invite", json={}).status_code == 409
    assert next(e for e in c.get(BASE).json()['employees'] if e['name'] == 'Rahim Uddin')['username'] == 'rahim_u'
    assert c.post(BASE + '/salaries', json={'period': period}).json()['paid'] == 0  # one salary per month

    with sqlite3.connect(path) as db:  # payroll history cannot be edited or deleted
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('UPDATE payroll_payments SET amount_minor=1')
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('DELETE FROM payroll_payments')


def test_only_the_owner_runs_payroll(world):  # noqa: F811
    c, _ = world
    signup(c, 'payroll_owner2')
    code = c.post('/api/account/invitations', json={'role': 'approver', 'hours': 24}).json()['code']
    second_person(c, 'payroll_helper')
    c.post('/api/account/join', json={'code': code}).raise_for_status()
    workspaces = c.get('/api/account/workspaces').json()['workspaces']
    shared = next(w for w in workspaces if w['role'] == 'approver')
    c.post('/api/account/switch', json={'business_id': shared['business_id']}).raise_for_status()
    assert c.get(BASE).status_code == 403
    assert c.post(BASE + '/employees', json={'name': 'Test', 'salary_minor': 100}).status_code == 403
    assert c.get(BASE + '/me').json() == {'employments': [], 'employee_only': False, 'simulation_only': True}


def test_people_who_left_are_not_paid(world):  # noqa: F811
    c, _ = world
    signup(c, 'payroll_owner3')
    identity = c.post(BASE + '/employees', json={'name': 'Mitu Das', 'designation': 'Designer', 'salary_minor': 3_000_000}).json()['id']
    c.post(f'{BASE}/employees/{identity}/status', json={'active': False}).raise_for_status()
    assert c.post(f'{BASE}/employees/{identity}/invite', json={}).status_code == 201
    period = today_bd().strftime('%Y-%m')
    assert c.post(BASE + '/salaries', json={'period': period}).json()['paid'] == 0
    assert c.post(BASE + '/salaries', json={'period': '2026-13'}).status_code == 422
