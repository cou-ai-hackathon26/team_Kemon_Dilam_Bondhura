"""In-app reminders computed in Bangladesh local time from existing records."""
from datetime import date, datetime, timedelta
import os

from api.app import security as sec
from api.app.contracts import ScenarioRequest
from api.app.feature_flags import workspace_status
from api.app.notifications import email_alerts
from api.app.notifications.service import BD, dispatch, enqueue
from api.app.services.finance import project_cashflow
from api.app.services.receivables import open_receipts


def anticipate_gap(db, business, ds, scenario, receipts, forecast):
    from api.app.copilot.gapfix import best_plan
    from api.app.copilot.plans import store_proposal
    breach = forecast['first_shortfall_on']
    if not breach:
        db.execute('DELETE FROM agent_gap_watch WHERE business_id=?', (business,))
        return
    previous = db.execute('SELECT breach_date FROM agent_gap_watch WHERE business_id=?', (business,)).fetchone()
    if previous and previous[0] == breach:
        return
    db.execute("""INSERT INTO agent_gap_watch VALUES (?,?) ON CONFLICT(business_id)
                  DO UPDATE SET breach_date=excluded.breach_date""", (business, breach))
    if (not previous or breach < previous[0]) and workspace_status(db, business, '/api/copilot/gap-fix')[2]:
        plan = best_plan(ds, scenario, receipts, forecast)
        if plan:
            store_proposal(db, business, ds, receipts, scenario, plan)


def run_workspace(db, business, at):
    if not db.execute("SELECT 1 FROM workspace_meta WHERE business_id=? AND kind='personal' AND archived_at IS NULL",
                      (business,)).fetchone():
        return
    now = datetime.fromtimestamp(at, BD)
    today = now.date()
    ds = sec.runtime_dataset(db, business)
    email_alerts.queue(db,business,ds,at)
    for bill in ds.invoices:
        if bill.status == 'open' and (bill.due_on-today).days in (0, 1, 3, 7):
            enqueue(db, business, 'bill_due', bill.invoice_id, at=at, period=str(today))
    rows = db.execute("""SELECT customer_id,due_on FROM receivable_balances
                         WHERE business_id=? AND open_minor>0 AND due_on<?""", (business, str(today))).fetchall()
    # A customer who always pays a week late is not chased on day one: remind only once they are later than their
    # own habit (every third day after that), so the reminder does not cry wolf.
    from api.app.services.radar import expected
    habit = {i['receivable_id']: i['habit_days'] for i in expected(db, business, ds, today)}
    due = set()
    for identity, customer, day in db.execute('SELECT id,customer_id,due_on FROM receivable_balances WHERE business_id=? '
                                              'AND open_minor>0 AND due_on<?', (business, str(today))).fetchall():
        past = (today - date.fromisoformat(day)).days - habit.get(identity, 0)
        if past > 0 and past % 3 == 1:
            due.add(customer)
    for customer in sorted(due):
        enqueue(db, business, 'customer_overdue', customer, at=at, period=str(today))
    receipts = open_receipts(db, business)
    if ds.events or ds.invoices or receipts or ds.opening_balance_minor:
        season_date = today + timedelta(days=30)
        if (season_date.month, season_date.day) == (4, 14):
            enqueue(db, business, 'boishakh_planning', str(season_date), at=at)
        eid_dates = os.environ.get('CAPZORYX_EID_PLANNING_DATES', '').split(',')
        if season_date.isoformat() in {value.strip() for value in eid_dates}:
            enqueue(db, business, 'eid_planning', str(season_date), at=at)
        agent_enabled = workspace_status(db, business, '/api/copilot/agent-review')[2]
        if now.hour >= 8:
            if rows and agent_enabled:
                enqueue(db, business, 'collections_review', str(today), at=at)
            enqueue(db, business, 'morning_briefing', str(today), at=at)
        reserve = db.execute('SELECT reserve_minor FROM security_policies WHERE business_id=?', (business,)).fetchone()
        if reserve:
            scenario = ScenarioRequest(as_of=ds.as_of, horizon_days=30, reserve_minor=reserve[0])
            forecast = project_cashflow(ds, scenario, receipts)
            if agent_enabled:
                anticipate_gap(db, business, ds, scenario, receipts, forecast)
            # Honest radar: alert only when the realistic outlook (customer payment habits, doubtful money left out)
            # falls short, once per episode, and again only when it gets clearly worse. See services/radar.py.
            from api.app.services.radar import radar, should_alert
            view = radar(db, business, ds, reserve[0], max(today, ds.as_of))
            new_alert = should_alert(db, business, view, at)
            if new_alert or view['level'] != 'alert':
                # Keep at most one pending reminder per channel across quiet hours, even when severity moves.
                db.execute("""UPDATE notification_jobs SET status='cancelled',claimed_until=NULL
                              WHERE business_id=? AND status='queued' AND json_extract(payload_json,'$.kind')='cash_breach'""",
                           (business,))
            if new_alert:
                enqueue(db, business, 'cash_breach', view['breach_on'], at=at, period=str(at // 1800))
    from api.app.notifications import telegram
    telegram.queue(db,business,at,has_due_bills=bool(email_alerts.due_count(ds,today)))
    # Dispatch once a deferred reminder can be shown. The transaction rechecks open balances and membership.
    pending = db.execute("""SELECT id FROM notification_jobs WHERE business_id=? AND channel='inapp' AND
                            ((status='queued' AND run_after<=?) OR (status='sending' AND claimed_until<=?))
                            ORDER BY run_after,id LIMIT 100""", (business,at,at)).fetchall()
    for (identity,) in pending:
        dispatch(db, identity, at)


def tick_business(db, business, at):
    with sec.transaction(db):
        # Claim each half-hour only once, including when both schedulers run.
        claimed = db.execute("""INSERT INTO notification_rules_state(business_id,last_slot) VALUES (?,?)
                                ON CONFLICT(business_id) DO UPDATE SET last_slot=excluded.last_slot
                                WHERE last_slot<excluded.last_slot RETURNING business_id""", (business, at//1800)).fetchone()
        if claimed:
            run_workspace(db, business, at)
    from api.app.notifications import telegram
    telegram.deliver(db,business,at)
    from api.app.notifications import push
    push.deliver(db,business,at)
    email_alerts.deliver(db,business,at)
    return bool(claimed)


def tick(db, at):
    batch = db.execute("""SELECT w.business_id FROM workspace_meta w LEFT JOIN notification_rules_state s
                          ON s.business_id=w.business_id WHERE w.kind='personal' AND w.archived_at IS NULL
                          AND COALESCE(s.last_slot,-1)<? ORDER BY COALESCE(s.last_slot,-1),w.business_id LIMIT 6""",
                       (at//1800,)).fetchall()
    count = sum(tick_business(db, row[0], at) for row in batch[:5])
    return dict(processed=count, more=len(batch)>5, simulation_only=True)
