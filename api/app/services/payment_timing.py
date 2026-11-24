"""Workspace-local settlement timing, conditioned on a bill still being open today."""
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from fractions import Fraction
from statistics import median
from typing import NamedTuple


class Paid(NamedTuple):
    customer: str
    due: date
    cleared: date
    amount: int

    @property
    def delay(self):
        return (self.cleared-self.due).days


class History:
    def __init__(self, paid):
        self.rows=sorted(paid,key=lambda record:record.cleared)
        self.delays=sorted(record.delay for record in self.rows)
        self.total=sum(record.amount for record in self.rows)

    def remaining_at(self, age):
        return len(self.delays)-bisect_right(self.delays,age)

    def paid_within(self, age, days):
        return bisect_right(self.delays,age+days)-bisect_right(self.delays,age)


def histories(rows):
    grouped=defaultdict(list)
    for row in rows:
        grouped[row.customer].append(row)
    return History(rows), {key:History(items) for key,items in grouped.items()}


def estimate(overall,customer,age):
    """Among completed prior bills still outstanding at the same age, shrink customer rates toward the book."""
    n=customer.remaining_at(age)
    workspace_n=overall.remaining_at(age)
    if not workspace_n:
        return dict(status="insufficient_history",reason="no_comparable_settlements",history_count=0,customer_count=0)
    result={}
    for days in (7,14,30):
        # n/(n+5) weight for customer observations, 5/(n+5) for the workspace distribution.
        numerator=customer.paid_within(age,days)+5*Fraction(overall.paid_within(age,days),workspace_n)
        result[str(days)]=round(100*numerator/(n+5))
    return dict(status="estimated",reason="customer_history" if n else "workspace_history",history_count=workspace_n,
                customer_count=n,within_days_pct=result,model="hisab-pay-empirical-v1")


def collection_outlook(db, business, debts, today):
    open_debts=[row for row in debts if row["open_minor"]>0]
    if not open_debts:return {}
    # Effective date is the date the owner actually received the cash. Settlement
    # insert time can be much later when a past receipt is entered into the books.
    paid_rows=db.execute("""
        SELECT r.customer_id,r.due_on,r.amount_minor,MAX(json_extract(e.payload,'$.effective_on'))
        FROM receivable_balances r
        JOIN receivable_settlements s ON s.business_id=r.business_id AND s.receivable_id=r.id
        JOIN source_records e ON e.business_id=s.business_id AND e.source_record_id=s.cash_record_id
        WHERE r.business_id=? AND r.open_minor=0
        GROUP BY r.id,r.customer_id,r.due_on,r.amount_minor
        """,(business,)).fetchall()
    paid=[Paid(customer,date.fromisoformat(due),date.fromisoformat(cleared),amount)
          for customer,due,amount,cleared in paid_rows if cleared and date.fromisoformat(cleared)<=today]
    workspace,customers=histories(paid)
    empty=History([])
    result={}
    for debt in open_debts:
        due=date.fromisoformat(debt["due_on"]); c=customers.get(debt["customer_id"],empty)
        value=estimate(workspace,c,(today-due).days)
        if c.rows:
            value["customer_facts"]={
                "mean_days_late":round(sum(c.delays)/len(c.delays),1),"median_days_late":median(c.delays),
                "recent_days_late":[p.delay for p in c.rows[-3:]],"on_time_pct":round(100*bisect_right(c.delays,0)/len(c.delays)),
                "amount_over_customer_mean":round(debt["amount_minor"]*len(c.rows)/c.total,2),
                "days_since_payment":(today-c.rows[-1].cleared).days,
            }
        result[debt["id"]]=value
    return result
