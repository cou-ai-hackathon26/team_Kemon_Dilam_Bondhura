"""Workspace-scoped plan acceptance and owner-controlled reminder drafts."""
import json
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract, RecordId, ScenarioRequest
from api.app.currencies import CURRENCIES, scale
from api.app.copilot.gapfix import best_plan
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.finance import fingerprint, project_cashflow, snapshot
from api.app.services.gap_fix import collection_plan
from api.app.services.payment_timing import collection_outlook
from api.app.services.receivables import customer_key, key_tag, open_receipts, unseal

router = APIRouter(prefix='/api/copilot')


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS collection_handoffs(
            business_id TEXT NOT NULL, customer_id TEXT NOT NULL, local_day TEXT NOT NULL,
            request_id TEXT NOT NULL, receipt_id TEXT NOT NULL, draft_digest TEXT NOT NULL,
            PRIMARY KEY(business_id,customer_id,local_day));
        CREATE TABLE IF NOT EXISTS agent_gap_watch(
            business_id TEXT PRIMARY KEY, breach_date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gap_plans(
            id TEXT PRIMARY KEY, business_id TEXT NOT NULL, created_at INTEGER NOT NULL,
            breach_date TEXT NOT NULL, shortfall_minor INTEGER NOT NULL, plan_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('proposed','accepted','dismissed')),
            decided_by TEXT, decided_at INTEGER);
        CREATE TABLE IF NOT EXISTS copilot_tasks(
            id TEXT PRIMARY KEY, business_id TEXT NOT NULL, plan_id TEXT REFERENCES gap_plans(id),
            kind TEXT NOT NULL, payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done','dismissed')),
            due_on TEXT, created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS copilot_tasks_due ON copilot_tasks(business_id,status,due_on);
    """)


def proof_id(business, dataset, receipts, scenario, actions):
    evidence = [business, fingerprint(dataset), [(r.source_id, str(r.due_on), r.amount_minor) for r in receipts],
                scenario.model_dump(mode='json'), actions]
    return sec.signature('collection-plan-v1', evidence)


def store_proposal(db, business, dataset, receipts, scenario, plan):
    identity = proof_id(business, dataset, receipts, scenario, plan['actions'])
    body = dict(scenario=scenario.model_dump(mode='json'), actions=plan['actions'])
    db.execute("""INSERT OR IGNORE INTO gap_plans(id,business_id,created_at,breach_date,shortfall_minor,plan_json,status)
                  VALUES (?,?,?,?,?,?,'proposed')""",
               (identity, business, sec.now(), plan['first_shortfall_on'],
                plan['original_gap_minor'], sec.canonical(body)))
    plan['id'] = identity


def propose(db, actor, dataset, receipts, scenario):
    baseline = project_cashflow(dataset, scenario, receipts)
    result = collection_plan(dataset, baseline, receipts)
    plan = best_plan(dataset, scenario, receipts, baseline)
    if plan and actor.role == 'owner':
        store_proposal(db, actor.business_id, dataset, receipts, scenario, plan)
    return dict(result, plan=plan, simulation_only=True)


class Accept(Contract):
    plan_id: str = Field(pattern=r'^[a-f0-9]{64}$')


def tasks(db, business, plan_id=None):
    sql = 'SELECT id,payload_json,due_on,status FROM copilot_tasks WHERE business_id=?'
    params = [business]
    if plan_id is None:
        sql += " AND status='open'"
    else:
        sql += ' AND plan_id=?'
        params.append(plan_id)
    rows = db.execute(sql + ' ORDER BY due_on,created_at,id', params)
    return [dict(id=identity, action=json.loads(payload), due_on=due, status=status)
            for identity, payload, due, status in rows]


@router.post('/gap-fix/accept')
def accept(body: Accept, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        row = db.execute('SELECT status,plan_json FROM gap_plans WHERE business_id=? AND id=?',
                         (business, body.plan_id)).fetchone()
        if not row:
            sec.fail(404, 'PLAN_NOT_FOUND')
        if row[0] == 'accepted':
            return dict(tasks=tasks(db, business, body.plan_id), replayed=True, simulation_only=True)
        saved = json.loads(row[1])
        dataset = sec.runtime_dataset(db, business)
        receipts = open_receipts(db, business)
        scenario = ScenarioRequest.model_validate(saved['scenario'])
        if row[0] != 'proposed' or proof_id(business, dataset, receipts, scenario, saved['actions']) != body.plan_id:
            sec.fail(409, 'FORECAST_CHANGED_REQUEST_NEW_PLAN')
        pending_sources = {task['action']['source_id'] for task in tasks(db, business)}
        if pending_sources.intersection(action['source_id'] for action in saved['actions']):
            sec.fail(409, 'COLLECTION_TASK_ALREADY_OPEN')
        now = sec.now()
        for action in saved['actions']:
            db.execute("""INSERT INTO copilot_tasks(id,business_id,plan_id,kind,payload_json,due_on,created_at)
                          VALUES (?,?,?,?,?,?,?)""",
                       (uuid.uuid4().hex, business, body.plan_id, action['kind'], sec.canonical(action), action['collect_by'], now))
        db.execute("UPDATE gap_plans SET status='accepted',decided_by=?,decided_at=? WHERE business_id=? AND id=?",
                   (owner.user_id, now, business, body.plan_id))
        sec.audit(db, owner, body.plan_id, 'GAP_PLAN_ACCEPTED', dict(task_count=len(saved['actions'])))
        return dict(tasks=tasks(db, business, body.plan_id), replayed=False, simulation_only=True)


@router.get('/tasks')
def list_tasks(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        return dict(tasks=tasks(db, owner.business_id), simulation_only=True)


class TaskDecision(Contract):
    status: Literal['done', 'dismissed']


@router.post('/tasks/{task_id}')
def decide(task_id: str, body: TaskDecision, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        params = (owner.business_id, task_id)
        row = db.execute('SELECT status FROM copilot_tasks WHERE business_id=? AND id=?', params).fetchone()
        if not row:
            sec.fail(404, 'TASK_NOT_FOUND')
        if row[0] == 'open':
            db.execute('UPDATE copilot_tasks SET status=? WHERE business_id=? AND id=?', (body.status, *params))
            sec.audit(db, owner, task_id, 'COLLECTION_TASK_UPDATED', dict(status=body.status))
        elif row[0] != body.status:
            sec.fail(409, 'TASK_ALREADY_CLOSED')
        return dict(tasks=tasks(db, owner.business_id), simulation_only=True)


class Reminder(Contract):
    receivable_id: RecordId
    language: Literal['bn', 'en'] = 'bn'


def reminder_text(db, owner, body):
    row = db.execute('SELECT open_minor,due_on,customer_id FROM receivable_balances WHERE business_id=? AND id=?',
                     (owner.business_id, body.receivable_id)).fetchone()
    if not row:
        sec.fail(404, 'RECEIVABLE_NOT_FOUND')
    if row[0] <= 0:
        sec.fail(409, 'RECEIVABLE_PAID')
    currency = sec.original_dataset(db, owner.business_id).currency
    precision = CURRENCIES[currency]['digits']
    amount = f'{Decimal(row[0]) / scale(currency):,.{precision}f} {currency}'
    if body.language == 'bn':
        digits = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')
        content = f"নমস্কার। আমাদের হিসাবে আপনার কাছে {amount.translate(digits)} বাকি আছে। পরিশোধের তারিখ {row[1].translate(digits)}। সুবিধামতো কবে পরিশোধ করতে পারবেন জানাবেন। ধন্যবাদ।"
    else:
        content = f"Hello. Our records show an outstanding balance of {amount}, due on {row[1]}. Please let us know when you can pay. Thank you."
    return row[2], dict(message=content, source_id=body.receivable_id, open_minor=row[0], due_on=row[1],
                currency=currency, simulation_only=True)


@router.post('/message')
def message(body: Reminder, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        return reminder_text(db, owner, body)[1]


class ApproveReminder(Reminder):
    request_id: uuid.UUID
    reviewed_message: str = Field(min_length=1, max_length=1000)


@router.post('/message/approve')
def approve_reminder(body: ApproveReminder, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        customer, draft = reminder_text(db, owner, body)
        if draft['message'] != body.reviewed_message:
            sec.fail(409, 'REMINDER_CHANGED')
        scope = (owner.business_id, customer, str(today_bd()))
        identity = (str(body.request_id), body.receivable_id, sec.signature('collection-reminder', draft['message']))
        existing = db.execute("""SELECT request_id,receipt_id,draft_digest FROM collection_handoffs
                                 WHERE business_id=? AND customer_id=? AND local_day=?""", scope).fetchone()
        if existing:
            if tuple(existing) != identity:
                sec.fail(409, 'CUSTOMER_REMINDER_ALREADY_APPROVED_TODAY')
        else:
            db.execute('INSERT INTO collection_handoffs VALUES (?,?,?,?,?,?)', (*scope, *identity))
            sec.audit(db, owner, str(body.request_id), 'COLLECTION_REMINDER_APPROVED', dict(receivable_id=body.receivable_id))
        return draft


def ranked_collections(db, business, today):
    debts = [dict(row) for row in db.execute("""
        SELECT r.id,r.customer_id,r.due_on,r.open_minor,r.amount_minor,c.name_enc,c.key_tag
        FROM receivable_balances r JOIN customers c ON c.id=r.customer_id AND c.business_id=r.business_id
        WHERE r.business_id=? AND r.open_minor>0 AND r.due_on<?
        """, (business, today.isoformat())).fetchall()]
    outlook = collection_outlook(db, business, debts, today)
    for row in debts:
        row['percent'] = outlook[row['id']].get('within_days_pct', {}).get('7')
    # Use the seven-day expected collection when evidence exists, then rank
    # unscored bills by outstanding amount. Offer one contact per customer.
    debts.sort(key=lambda r: (r['percent'] is None,
        -r['open_minor'] * (r['percent'] if r['percent'] is not None else 100),
        r['due_on'], r['id']))
    seen = set()
    selected = []
    key = customer_key(db, business) if debts else None
    tag = key_tag(key) if key else None
    for row in debts:
        customer = row['customer_id']
        if customer in seen:
            continue
        seen.add(customer)
        name = unseal(key, row['name_enc'], customer + '/name').decode() if key and row['key_tag'] == tag else ''
        selected.append(dict(receivable_id=row['id'], customer_name=name, due_on=row['due_on'],
                             open_minor=row['open_minor'],
                             probability_7d=row['percent'] / 100 if row['percent'] is not None else None))
        if len(selected) == 3:
            break
    return selected


@router.get('/agent-review')
def agent_review(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        today = today_bd()
        return dict(as_of=today.isoformat(), currency=sec.original_dataset(db, owner.business_id).currency,
                    collections=ranked_collections(db, owner.business_id, today), simulation_only=True)


@router.get('/briefing')
def briefing(actor=Depends(principal), db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        current = sec.recheck(db, actor)
        if current.role not in ('owner', 'viewer', 'approver', 'bookkeeper'):
            sec.fail(403, 'MEMBER_REQUIRED')
        sec.verified_audit(db, current.business_id)
        dataset = sec.runtime_dataset(db, current.business_id)
        receipts = open_receipts(db, current.business_id)
        reserve = db.execute('SELECT reserve_minor FROM security_policies WHERE business_id=?', (current.business_id,)).fetchone()[0]
        request = ScenarioRequest(as_of=dataset.as_of, horizon_days=7, reserve_minor=reserve)
        projected = project_cashflow(dataset, request, receipts)
        return dict(as_of=str(dataset.as_of), currency=dataset.currency, balance_minor=snapshot(dataset)['balance_minor'],
                    closing_7_day_minor=projected['closing_balance_minor'], first_shortfall_on=projected['first_shortfall_on'],
                    amount_owed_minor=sum(r.amount_minor for r in receipts), source_ids=sorted(set(projected['source_ids']) | {r.source_id for r in receipts}),
                    simulation_only=True)
