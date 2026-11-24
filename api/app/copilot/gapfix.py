"""Find and verify a cash-gap plan containing up to three early collections."""
from collections import defaultdict
from datetime import date
from itertools import combinations
from typing import NamedTuple

from api.app.services.finance import CashAdjustment, project_cashflow


class Receipt(NamedTuple):
    end: int
    source: str
    due: date
    amount: int


def best_plan(dataset, request, receivables, baseline):
    """Minimize peak remaining shortfall, then the number of people to contact.

    Each early collection helps on [first breach, original due date). Within
    one such interval the three largest receipts dominate every smaller one.
    This bounds even a 2,000-record search to three receipts per horizon day.
    """
    if not baseline['first_shortfall_on']:
        return None
    days = baseline['days']
    breach = date.fromisoformat(baseline['first_shortfall_on'])
    eligible = [(e.source_record_id, e.effective_on, e.amount_minor) for e in dataset.events
                if e.status == 'scheduled' and e.direction == 'inflow']
    eligible.extend(receivables)
    buckets = defaultdict(list)
    for source, due, amount in eligible:
        if due > breach:
            end = min(request.horizon_days, (due - request.as_of).days - 1)
            buckets[end].append(Receipt(end, source, due, amount))
    candidates = []
    for end in sorted(buckets):
        candidates.extend(sorted(buckets[end], key=lambda r: (-r.amount, r.due, r.source))[:3])
    horizon = len(days)
    gaps = [row['shortfall_minor'] for row in days]
    # peaks[start][end] is the maximum gap in the half-open interval.
    peaks = [[0] * (horizon + 1) for _ in range(horizon + 1)]
    for start in range(horizon):
        for end in range(start + 1, horizon + 1):
            peaks[start][end] = max(peaks[start][end - 1], gaps[end - 1])
    original_gap = peaks[0][horizon]
    best = ()
    remaining = original_gap
    for size in (1, 2, 3):
        for group in combinations(candidates, size):
            available = sum(r.amount for r in group)
            shortfall = 0
            start = 0
            for receipt in group:
                shortfall = max(shortfall, peaks[start][receipt.end] - available)
                available -= receipt.amount
                start = receipt.end
            shortfall = max(shortfall, peaks[start][horizon])
            if shortfall < remaining:
                best = group
                remaining = shortfall
                if remaining == 0:
                    break
        if remaining == 0:
            break
    if not best:
        return None
    # Latest-expiring support first minimizes total requested money. Skip
    # the portion of each original receipt that the reserve does not need.
    needed = original_gap - remaining
    actions = []
    adjustments = []
    for receipt in reversed(best):
        advance = min(needed, receipt.amount)
        if not advance:
            break
        needed -= advance
        actions.append(dict(kind='collect_early', source_id=receipt.source, scheduled_on=receipt.due.isoformat(),
                            collect_by=breach.isoformat(), amount_minor=advance))
        adjustments.extend([CashAdjustment(breach, 'inflow', advance, receipt.source),
                            CashAdjustment(receipt.due, 'inflow', -advance, receipt.source)])
    proof = project_cashflow(dataset, request, receivables, extra_movements=adjustments)
    measured_gap = max(day['shortfall_minor'] for day in proof['days'])
    if measured_gap != remaining:
        raise ValueError('Collection simulation did not reconcile')
    return dict(actions=actions, remaining_gap_minor=measured_gap, original_gap_minor=original_gap,
                first_shortfall_on=baseline['first_shortfall_on'], forecast=proof)
