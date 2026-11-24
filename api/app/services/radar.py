"""Challenge 2: a cash radar that stays honest when customers pay late, so its alert does not cry wolf.

The plain forecast assumes every customer pays on the due date. Real customers have habits: some always pay a week
late. The radar:
  1. learns each customer's habit from their past payments (days paid after the due date; the median, so one very
     late payment does not define a customer). With fewer than two payments it uses the business-wide habit, and
     with no history at all, the due date;
  2. expects each open invoice on due date + habit. Money already later than the habit is expected soon but flagged
     "later than usual"; money more than 30 days overdue and two weeks past the habit is "doubtful" and left out of
     the realistic forecast (it is not counted as arriving tomorrow);
  3. compares three outlooks with the same bills: everyone pays on the due date, realistic (habits), and a stress
     case where late and doubtful money does not arrive within the period;
  4. raises an ALERT only when the realistic outlook falls below the reserve within 14 days. If only the stress case
     does, it is a quiet WATCH shown in the app, with the customers it depends on. Alerts are sent once per episode
     and again only when things get clearly worse; when the shortfall disappears the episode closes.
"""
from datetime import date, timedelta
from statistics import median

from api.app.contracts import ScenarioRequest
from api.app.services.finance import project_cashflow
from api.app.services.receivables import ExpectedReceipt

DOUBTFUL_OVERDUE_DAYS = 30
DOUBTFUL_PAST_HABIT_DAYS = 14
ALERT_WINDOW_DAYS = 14
RESEND_EARLIER_DAYS = 3
RESEND_GROWTH = 1.25
RESEND_AFTER_DAYS = 7


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS radar_state(business_id TEXT PRIMARY KEY, level TEXT NOT NULL, breach_on TEXT,
      shortfall_minor INTEGER NOT NULL DEFAULT 0, alerted_at INTEGER, updated_at INTEGER NOT NULL);
    """)


def habits_from(payments):
    """payments: (customer_id, due_on, paid_on). -> ({customer: (typical days late, count)}, business-wide or None)."""
    late = {}
    for customer, due_on, paid_on in payments:
        late.setdefault(customer, []).append(max(0, (paid_on - due_on).days))
    per_customer = {c: (int(round(median(days))), len(days)) for c, days in late.items()}
    every = [d for days in late.values() for d in days]
    return per_customer, (int(round(median(every))) if len(every) >= 3 else None)


def expectations(open_items, per_customer, overall, today):
    """open_items: (receivable_id, customer_id, due_on, open_minor). When each is realistically expected, and why."""
    out = []
    for identity, customer, due, amount in open_items:
        own = per_customer.get(customer)
        if own and own[1] >= 2:
            habit, basis = own[0], 'customer'
        elif overall is not None:
            habit, basis = overall, 'business'
        else:
            habit, basis = 0, 'due_date'
        expected_on = due + timedelta(days=habit)
        overdue = (today - due).days
        status = 'on_time' if habit == 0 and overdue <= 0 else 'usually_late'
        if expected_on <= today:
            past_habit = (today - expected_on).days
            if overdue > DOUBTFUL_OVERDUE_DAYS and past_habit > DOUBTFUL_PAST_HABIT_DAYS:
                status = 'doubtful'
            elif overdue > habit:
                status = 'later_than_usual'
            expected_on = today + timedelta(days=1)
        out.append(dict(receivable_id=identity, customer_id=customer, due_on=due.isoformat(), amount_minor=amount,
                        habit_days=habit, basis=basis, expected_on=expected_on.isoformat(), overdue_days=max(0, overdue),
                        status=status))
    return out


def _receipts(items, when):
    return [ExpectedReceipt(i['receivable_id'], date.fromisoformat(when(i)), i['amount_minor']) for i in items]


def _first_breach(forecast, today, window):
    last = (today + timedelta(days=window)).isoformat()
    for day in forecast['days']:
        if day['shortfall_minor'] and day['date'] <= last:
            return day
    return None


def outlook(ds, reserve_minor, today, items, horizon=30):
    """Three outlooks with the same bills and the alert level that follows from them."""
    today = max(today, ds.as_of)
    if ds.as_of != today:
        ds = ds.model_copy(update={'as_of': today})
    scenario = ScenarioRequest(as_of=today, horizon_days=horizon, reserve_minor=reserve_minor)
    on_time = project_cashflow(ds, scenario, _receipts(items, lambda i: i['due_on']))
    realistic = project_cashflow(ds, scenario, _receipts([i for i in items if i['status'] != 'doubtful'], lambda i: i['expected_on']))
    reliable = [i for i in items if i['status'] == 'on_time' and i['overdue_days'] == 0]
    stress = project_cashflow(ds, scenario, _receipts(reliable, lambda i: i['expected_on']))
    # Start with cleared cash and include known bills and scheduled expenses. Future money contributes only
    # to the projected collection scenarios.
    cleared_cash = ds.model_copy(update={'events': [event for event in ds.events
                                                  if event.status == 'cleared' or event.direction == 'outflow']})
    confirmed = project_cashflow(cleared_cash, scenario)
    hit, worst = _first_breach(realistic, today, ALERT_WINDOW_DAYS), _first_breach(stress, today, ALERT_WINDOW_DAYS)
    level = 'alert' if hit else 'watch' if worst else 'clear'
    breach = hit or worst
    window_end = (today + timedelta(days=ALERT_WINDOW_DAYS)).isoformat()
    depends = [i for i in items if i['status'] in ('later_than_usual', 'doubtful') or i['expected_on'] <= window_end]

    def summary(f):
        b = _first_breach(f, today, horizon)
        return dict(closing_minor=f['closing_balance_minor'], lowest_minor=f['minimum_balance_minor'],
                    first_shortfall_on=b['date'] if b else None, shortfall_minor=b['shortfall_minor'] if b else 0)
    return dict(level=level, breach_on=breach['date'] if breach else None, shortfall_minor=breach['shortfall_minor'] if breach else 0,
                reserve_minor=reserve_minor, currency=ds.currency, as_of=today.isoformat(),
                outlooks=dict(on_time=summary(on_time), realistic=summary(realistic), stress=summary(stress)),
                confirmed=summary(confirmed), available_cash_minor=confirmed['opening_balance_minor'],
                receivables=items, depends_on=depends[:12],
                doubtful_minor=sum(i['amount_minor'] for i in items if i['status'] == 'doubtful'),
                late_minor=sum(i['amount_minor'] for i in items if i['status'] == 'later_than_usual'))


def expected(db, business, ds, today):
    """Every open customer invoice in this workspace with its realistic date (reads payments and open invoices)."""
    paid_on = {e.source_record_id: e.effective_on for e in ds.events}
    settled = {}
    for identity, customer, due, cash in db.execute(
        """SELECT r.id, r.customer_id, r.due_on, s.cash_record_id FROM receivable_settlements s
           JOIN receivable_balances r ON r.business_id=s.business_id AND r.id=s.receivable_id
           WHERE s.business_id=? AND r.open_minor=0""", (business,)).fetchall():
        if cash in paid_on:
            key = (identity, customer, date.fromisoformat(due))
            settled[key] = max(settled.get(key, date.min), paid_on[cash])
    per_customer, overall = habits_from((customer, due, paid) for (_, customer, due), paid in settled.items())
    rows = db.execute("""SELECT id, customer_id, due_on, open_minor FROM receivable_balances
                         WHERE business_id=? AND open_minor>0 ORDER BY due_on, id""", (business,)).fetchall()
    return expectations([(i, c, date.fromisoformat(d), a) for i, c, d, a in rows], per_customer, overall, today)


def radar(db, business, ds, reserve_minor, today, horizon=30):
    today = max(today, ds.as_of)
    return outlook(ds, reserve_minor, today, expected(db, business, ds, today), horizon)


def should_alert(db, business, view, at):
    """True when an ALERT should be sent now: a new episode, or one that got clearly worse, or a week-old reminder.
    Keeps one row of state per business; a clear or watch outlook closes the episode."""
    row = db.execute('SELECT level,breach_on,shortfall_minor,alerted_at FROM radar_state WHERE business_id=?', (business,)).fetchone()
    send = False
    if view['level'] == 'alert':
        if not row or row[0] != 'alert' or row[3] is None:
            send = True
        else:
            earlier = row[1] and view['breach_on'] and \
                (date.fromisoformat(row[1]) - date.fromisoformat(view['breach_on'])).days >= RESEND_EARLIER_DAYS
            grew = view['shortfall_minor'] >= max(1, row[2]) * RESEND_GROWTH
            stale = at - row[3] >= RESEND_AFTER_DAYS * 86400
            send = bool(earlier or grew or stale)
    breach, shortfall = view['breach_on'], view['shortfall_minor']
    alerted = at if send else None
    if not send and row and view['level'] == 'alert':
        # Measure deterioration against the last alert the owner was offered.
        breach, shortfall, alerted = row[1:]
    db.execute("""INSERT INTO radar_state(business_id,level,breach_on,shortfall_minor,alerted_at,updated_at) VALUES (?,?,?,?,?,?)
                  ON CONFLICT(business_id) DO UPDATE SET level=excluded.level,breach_on=excluded.breach_on,
                  shortfall_minor=excluded.shortfall_minor,alerted_at=excluded.alerted_at,updated_at=excluded.updated_at""",
               (business, view['level'], breach, shortfall, alerted, at))
    return send
