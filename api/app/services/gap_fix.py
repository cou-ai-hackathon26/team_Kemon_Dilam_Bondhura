"""Simulate bringing one scheduled receipt forward. Never modify a record."""
from api.app.services.finance import Dataset


def collection_plan(dataset: Dataset, forecast: dict, receivables=()) -> dict:
    days = forecast["days"]
    peak_gap = max(day["shortfall_minor"] for day in days)
    result = {"peak_gap_minor": peak_gap, "first_shortfall_on": forecast["first_shortfall_on"], "options": []}
    if not peak_gap:
        return result

    breach = forecast["first_shortfall_on"]
    candidates = []
    receipts = [(e.source_record_id, e.effective_on, e.amount_minor) for e in dataset.events
                if e.status == "scheduled" and e.direction == "inflow"]
    receipts.extend(receivables)
    for source_id, effective_on, amount in receipts:
        due = effective_on.isoformat()
        if due <= breach:
            continue
        # Advance the minimum useful portion. On the original receipt date the
        # scenario rejoins the baseline, so the inflow can never count twice.
        advance = min(amount, max(day["shortfall_minor"] for day in days if day["date"] < due))
        remaining = max(
            max(0, day["shortfall_minor"] - (advance if breach <= day["date"] < due else 0))
            for day in days
        )
        if remaining < peak_gap:
            candidates.append(dict(source_id=source_id, scheduled_on=due, collect_by=breach,
                                   advance_minor=advance, remaining_gap_minor=remaining))
    candidates.sort(key=lambda row: (row["remaining_gap_minor"], row["advance_minor"], row["scheduled_on"], row["source_id"]))
    result["options"] = candidates[:3]
    return result
