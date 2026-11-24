"""Payroll: employees, monthly salaries and bonuses.

The workspace owner (admin) adds any number of employees and sends each one a private invite link. The employee
opens it, chooses their own username and password, and from then on signs in to an employee dashboard that shows only
their own payslips. Employees never join the employer's workspace, so they cannot read its books.
Payments are append-only, one salary per employee per month, and each one is also posted to the cash books as a
cleared outflow, so the forecast and balance stay true.
"""
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from api.app import security as sec
from api.app.accounts import Credentials, today_bd
from api.app.contracts import Contract
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/payroll')
PERIOD = r'^\d{4}-(0[1-9]|1[0-2])$'
INVITE_TTL = 7 * 86400


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS payroll_employees(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, profile_enc TEXT NOT NULL,
        key_tag TEXT NOT NULL, salary_minor INTEGER NOT NULL CHECK(salary_minor > 0), joined_on TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','left')), account_id TEXT UNIQUE,
        created_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS payroll_payments(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, employee_id TEXT NOT NULL,
        period TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('salary','bonus')), amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
        paid_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL, created_by TEXT NOT NULL, created_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS payroll_invites(code_hash TEXT PRIMARY KEY, employee_id TEXT NOT NULL UNIQUE,
        business_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS payroll_one_salary ON payroll_payments(employee_id, period) WHERE kind='salary';
      CREATE INDEX IF NOT EXISTS payroll_by_business ON payroll_payments(business_id, period);
      CREATE TRIGGER IF NOT EXISTS payroll_payments_no_update BEFORE UPDATE ON payroll_payments BEGIN SELECT RAISE(ABORT, 'append-only'); END;
      CREATE TRIGGER IF NOT EXISTS payroll_payments_no_delete BEFORE DELETE ON payroll_payments BEGIN SELECT RAISE(ABORT, 'append-only'); END;
    """)


# ---------- helpers ----------
def profile(db, business, row):
    key = customer_key(db, business)
    if not key or key_tag(key) != row['key_tag']:
        return {'name': '—', 'designation': ''}
    return json.loads(unseal(key, row['profile_enc'], row['id'] + '/profile'))


def employees(db, business):
    rows = db.execute('SELECT id,profile_enc,key_tag,salary_minor,joined_on,status,account_id FROM payroll_employees '
                      'WHERE business_id=? ORDER BY created_at,id', (business,)).fetchall()
    out = []
    for r in rows:
        row = dict(zip(('id', 'profile_enc', 'key_tag', 'salary_minor', 'joined_on', 'status', 'account_id'), r))
        username = None
        if row['account_id']:
            hit = db.execute('SELECT username FROM auth_accounts WHERE account_id=?', (row['account_id'],)).fetchone()
            username = hit[0] if hit else None
        invited = not username and db.execute('SELECT 1 FROM payroll_invites WHERE employee_id=? AND expires_at>?',
                                              (row['id'], sec.now())).fetchone() is not None
        out.append(dict(id=row['id'], **profile(db, business, row), salary_minor=row['salary_minor'], joined_on=row['joined_on'],
                        status=row['status'], username=username, invited=invited))
    return out


def payments(db, where, params):
    rows = db.execute(f'SELECT id,employee_id,period,kind,amount_minor,paid_on,note FROM payroll_payments WHERE {where} '
                      'ORDER BY period DESC, paid_on DESC, created_at DESC', params).fetchall()
    return [dict(zip(('id', 'employee_id', 'period', 'kind', 'amount_minor', 'paid_on', 'note'), r)) for r in rows]


def currency_of(db, business):
    return sec.original_dataset(db, business).currency


def period_label(period):
    year, month = map(int, period.split('-'))
    return date(year, month, 1).strftime('%b %Y')


def post_to_books(db, owner, amount_minor, paid_on, memo):
    """Record the payment as a cleared cash outflow in the same transaction."""
    from api.app.inputs import InputRecord, append_record
    from api.app.services.finance import fingerprint
    ds = sec.original_dataset(db, owner.business_id)
    request_id = str(uuid.uuid4())
    append_record(db, owner, InputRecord(request_id=request_id, kind='cash', amount_minor=amount_minor, effective_on=paid_on,
                                         direction='outflow', status='cleared', memo=memo, expected_revision=fingerprint(ds)),
                  manual_entry=True)
    return 'manual-' + request_id


def record_payment(db, owner, employee, period, kind, amount_minor, paid_on, note=''):
    memo = f"{'Salary' if kind == 'salary' else 'Bonus'} · {employee['name']} · {period_label(period)}" + (f' · {note}' if note else '')
    source = post_to_books(db, owner, amount_minor, paid_on, memo)
    db.execute('INSERT INTO payroll_payments VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, owner.business_id, employee['id'], period, kind, amount_minor, paid_on, note[:120], source,
                owner.user_id, sec.now()))


def add_employee(db, owner, name, designation, salary_minor, joined_on):
    identity = uuid.uuid4().hex
    key = customer_key(db, owner.business_id, create=True)
    box = seal(key, sec.canonical({'name': name, 'designation': designation}).encode(), identity + '/profile')
    db.execute('INSERT INTO payroll_employees(id,business_id,profile_enc,key_tag,salary_minor,joined_on,created_at) VALUES (?,?,?,?,?,?,?)',
               (identity, owner.business_id, box, key_tag(key), salary_minor, joined_on, sec.now()))
    sec.audit(db, owner, identity, 'PAYROLL_EMPLOYEE_ADDED', {'salary_minor': salary_minor})
    return identity


def employee_or_404(db, business, identity):
    found = next((e for e in employees(db, business) if e['id'] == identity), None)
    if not found:
        sec.fail(404, 'EMPLOYEE_NOT_FOUND')
    return found


# ---------- admin ----------
@router.get('')
def overview(period: str | None = None, actor=Depends(principal), db=Depends(db_connection)):
    month = period if period and re.fullmatch(PERIOD, period) else today_bd().strftime('%Y-%m')
    with owner_transaction(db, actor) as owner:
        team = employees(db, owner.business_id)
        history = payments(db, 'business_id=?', (owner.business_id,))
        paid_salary = {p['employee_id'] for p in history if p['period'] == month and p['kind'] == 'salary'}
        active = [e for e in team if e['status'] == 'active']
        this_month = [p for p in history if p['period'] == month]
        year = month[:4]
        return dict(period=month, currency=currency_of(db, owner.business_id), employees=team, payments=history,
                    summary=dict(active=len(active), monthly_salary_minor=sum(e['salary_minor'] for e in active),
                                 paid_this_period_minor=sum(p['amount_minor'] for p in this_month),
                                 due_this_period_minor=sum(e['salary_minor'] for e in active if e['id'] not in paid_salary),
                                 unpaid_count=sum(1 for e in active if e['id'] not in paid_salary),
                                 year_to_date_minor=sum(p['amount_minor'] for p in history if p['period'].startswith(year))),
                    simulation_only=True)


class NewEmployee(Contract):
    name: str = Field(min_length=2, max_length=80)
    designation: str = Field(default='', max_length=80)
    salary_minor: int = Field(gt=0, le=10**12)
    joined_on: date | None = None


@router.post('/employees', status_code=201)
def new_employee(body: NewEmployee, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        if db.execute("SELECT count(*) FROM payroll_employees WHERE business_id=?", (owner.business_id,)).fetchone()[0] >= 500:
            sec.fail(409, 'EMPLOYEE_LIMIT_REACHED')
        identity = add_employee(db, owner, body.name.strip(), body.designation.strip(), body.salary_minor,
                                str(body.joined_on or today_bd()))
    return dict(id=identity, simulation_only=True)


class Empty(Contract):
    pass


def invite_link(code):
    target = urlsplit(os.getenv('SAFELEDGER_PUBLIC_URL', ''))
    base = f'{target.scheme}://{target.netloc}' if target.scheme in ('http', 'https') and target.netloc else ''
    return f'{base}/?employee={code}'


@router.post('/employees/{identity}/invite', status_code=201)
def employee_invite(identity: str, body: Empty, actor=Depends(principal), db=Depends(db_connection)):
    """A private, single-use link for this employee, valid for seven days. A new link replaces the old one."""
    with owner_transaction(db, actor) as owner:
        sec.budget(db, 'payroll-invite:' + owner.user_id, 30)
        if employee_or_404(db, owner.business_id, identity)['username']:
            sec.fail(409, 'EMPLOYEE_ALREADY_JOINED')
        code = secrets.token_urlsafe(24)
        db.execute('DELETE FROM payroll_invites WHERE employee_id=?', (identity,))
        db.execute('INSERT INTO payroll_invites VALUES (?,?,?,?)',
                   (sec.signature('payroll-invite', code), identity, owner.business_id, sec.now() + INVITE_TTL))
        sec.audit(db, owner, identity, 'PAYROLL_INVITE_CREATED', {'expires_in_seconds': INVITE_TTL})
    return dict(link=invite_link(code), expires_at=sec.now() + INVITE_TTL, simulation_only=True)


class InviteCode(Contract):
    code: str = Field(min_length=20, max_length=64)



def employer_name(db, business):
    """The name employees see: the workspace's own name, or the owner's name when it still has the default one."""
    row = db.execute('SELECT name FROM workspace_meta WHERE business_id=?', (business,)).fetchone()
    if row and row[0] and row[0] not in ('My workspace', 'My payslips'):
        return row[0]
    owner = db.execute("SELECT COALESCE(NULLIF(p.display_name,''),a.username,'') FROM security_users u "
                       "JOIN account_members m ON m.user_id=u.user_id JOIN auth_accounts a ON a.account_id=m.account_id "
                       "LEFT JOIN account_profiles p ON p.account_id=a.account_id "
                       "WHERE u.business_id=? AND u.role='owner' ORDER BY m.rowid LIMIT 1", (business,)).fetchone()
    return owner[0] if owner and owner[0] else 'Your employer'


def open_invite(db, code):
    row = db.execute('SELECT employee_id,business_id FROM payroll_invites WHERE code_hash=? AND expires_at>?',
                     (sec.signature('payroll-invite', code), sec.now())).fetchone()
    if not row:
        sec.fail(404, 'INVITE_EXPIRED_OR_USED')
    employee = employee_or_404(db, row[1], row[0])
    if employee['username']:  # people who have left can still join to see their past payslips
        sec.fail(404, 'INVITE_EXPIRED_OR_USED')
    return employee, row[1], employer_name(db, row[1])


@router.post('/invite/check')
def check_invite(body: InviteCode, request: Request, db=Depends(db_connection)):
    """What the invite page shows before the employee picks a username: their name and the employer's name."""
    from api.app.accounts import rate
    rate(request, db, 'payroll-invite', 10, 60, 600)
    with sec.transaction(db):
        employee, _, employer = open_invite(db, body.code)
    return dict(name=employee['name'], designation=employee['designation'], employer=employer, simulation_only=True)


class InviteAccept(Credentials):
    code: str = Field(min_length=20, max_length=64)


@router.post('/invite/accept', status_code=201)
def accept_invite(body: InviteAccept, request: Request, response: Response, db=Depends(db_connection)):
    """The employee sets their own username and password; the link is then used up and they are signed in."""
    from api.app import app_sessions
    from api.app.accounts import ACCOUNT_CAP, SESSION_TTL, browser_origin, create_workspace_data, password_hash, rate, session_result
    browser_origin(request)
    rate(request, db, 'payroll-invite', 10, 60, 600)
    hashed = password_hash(body.password)
    account, business, user = uuid.uuid4().hex, 'workspace-' + uuid.uuid4().hex, 'user-' + uuid.uuid4().hex
    try:
        with sec.transaction(db):
            employee, employer_business, _ = open_invite(db, body.code)
            if db.execute('SELECT count(*) FROM auth_accounts').fetchone()[0] >= ACCOUNT_CAP:
                sec.fail(503, 'ACCOUNT_CAPACITY_REACHED')
            db.execute('INSERT INTO auth_accounts VALUES (?,?,?,?)', (account, body.username, hashed, sec.now()))
            db.execute("INSERT INTO security_users VALUES (?,?, 'owner',1)", (user, business))
            db.execute('INSERT INTO account_members VALUES (?,?,?)', (account, business, user))
            create_workspace_data(db, business, 'personal', 'My payslips', currency_of(db, employer_business))
            db.execute('UPDATE payroll_employees SET account_id=? WHERE id=? AND account_id IS NULL', (account, employee['id']))
            db.execute('DELETE FROM payroll_invites WHERE employee_id=?', (employee['id'],))
            token = app_sessions.issue_client(db, request, response, user, SESSION_TTL)
            sec.audit(db, sec.Principal(user, business, 'owner', ''), 'account-' + account, 'PAYROLL_INVITE_ACCEPTED',
                      {'employer': employer_business})
    except sqlite3.IntegrityError:
        sec.fail(409, 'USERNAME_UNAVAILABLE')
    return session_result(db, request, token)


class Status(Contract):
    active: bool = Field(strict=True)


@router.post('/employees/{identity}/status')
def employee_status(identity: str, body: Status, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        employee_or_404(db, owner.business_id, identity)
        db.execute('UPDATE payroll_employees SET status=? WHERE id=? AND business_id=?',
                   ('active' if body.active else 'left', identity, owner.business_id))
        sec.audit(db, owner, identity, 'PAYROLL_EMPLOYEE_STATUS', {'active': body.active})
    return dict(simulation_only=True)


class PayRun(Contract):
    period: str = Field(pattern=PERIOD)
    paid_on: date | None = None
    employee_ids: list[str] = Field(default_factory=list, max_length=500)


@router.post('/salaries')
def pay_salaries(body: PayRun, actor=Depends(principal), db=Depends(db_connection)):
    """Pay this month's salary to every active employee not yet paid (or to the ones listed). Safe to repeat."""
    paid_on = str(body.paid_on or today_bd())
    with owner_transaction(db, actor) as owner:
        done = {r[0] for r in db.execute("SELECT employee_id FROM payroll_payments WHERE business_id=? AND period=? AND kind='salary'",
                                         (owner.business_id, body.period))}
        chosen = [e for e in employees(db, owner.business_id) if e['status'] == 'active' and e['id'] not in done
                  and (not body.employee_ids or e['id'] in body.employee_ids)]
        for employee in chosen:
            record_payment(db, owner, employee, body.period, 'salary', employee['salary_minor'], paid_on)
        if chosen:
            sec.audit(db, owner, uuid.uuid4().hex, 'PAYROLL_SALARIES_PAID',
                      {'period': body.period, 'count': len(chosen), 'total_minor': sum(e['salary_minor'] for e in chosen)})
    return dict(paid=len(chosen), total_minor=sum(e['salary_minor'] for e in chosen), simulation_only=True)


class Bonus(Contract):
    period: str = Field(pattern=PERIOD)
    amount_minor: int = Field(gt=0, le=10**12)
    note: str = Field(default='', max_length=120)
    employee_ids: list[str] = Field(default_factory=list, max_length=500)
    paid_on: date | None = None


@router.post('/bonus')
def pay_bonus(body: Bonus, actor=Depends(principal), db=Depends(db_connection)):
    """The same bonus to each listed employee, or to every active employee when none are listed."""
    paid_on = str(body.paid_on or today_bd())
    with owner_transaction(db, actor) as owner:
        chosen = [e for e in employees(db, owner.business_id) if e['status'] == 'active'
                  and (not body.employee_ids or e['id'] in body.employee_ids)]
        if not chosen:
            sec.fail(422, 'NO_EMPLOYEES_SELECTED')
        for employee in chosen:
            record_payment(db, owner, employee, body.period, 'bonus', body.amount_minor, paid_on, body.note.strip())
        sec.audit(db, owner, uuid.uuid4().hex, 'PAYROLL_BONUS_PAID',
                  {'period': body.period, 'count': len(chosen), 'total_minor': body.amount_minor * len(chosen)})
    return dict(paid=len(chosen), total_minor=body.amount_minor * len(chosen), simulation_only=True)


# ---------- employee ----------
@router.get('/me')
def my_pay(actor=Depends(principal), db=Depends(db_connection)):
    """The signed-in person's own employments and payslips, from any employer that pays them through Capzoryx."""
    from api.app.profile import account_of
    account = account_of(db, actor)
    jobs = []
    with sec.transaction(db):
        links = db.execute('SELECT id,business_id FROM payroll_employees WHERE account_id=?', (account,)).fetchall()
        for identity, business in links:
            jobs.append(employment(db, identity, business))
    # Accounts are linked only when an employer creates them for payroll, so they show the payslips and nothing else.
    return dict(employments=jobs, employee_only=bool(jobs), simulation_only=True)


def employment(db, identity, business):
    me = employee_or_404(db, business, identity)
    mine = payments(db, 'employee_id=?', (identity,))
    return dict(employer=employer_name(db, business), name=me['name'], designation=me['designation'],
                salary_minor=me['salary_minor'], status=me['status'], joined_on=me['joined_on'],
                currency=currency_of(db, business), payments=mine,
                year_to_date_minor=sum(p['amount_minor'] for p in mine if p['period'].startswith(str(today_bd().year))))
