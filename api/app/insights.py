"""Financial intelligence: real profit per product, break-even, price scenarios, growth and a capability score.

All money math is deterministic, in integer minor units (Decimal only for quantities and percentages). The AI never
computes these numbers. When an input is missing the result says what is missing instead of guessing.

A market price is not always what a buyer will actually pay, so the owner's per-unit selling costs (making charge, buyback
deduction, transport) are subtracted before calling something a profit.
"""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from fastapi import APIRouter, Depends

from api.app import security as sec
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api/insights')
SCENARIOS = (-20, -10, -5, 0, 5, 10)
THIN_CUSHION_BP = 300  # under 3% of headroom before a loss counts as thin
LOCAL_FOR = {'gold_22k': 'gold_22k_local', 'gold_21k': 'gold_21k_local', 'gold_18k': 'gold_18k_local',
             'gold_traditional': 'gold_traditional_local', 'silver': 'silver_local'}


def rounded(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_HALF_EVEN))


def pct_bp(part, whole):
    """part/whole in basis points (1% = 100), or None when the whole is zero or missing."""
    if not whole:
        return None
    return rounded(Decimal(part) * 10000 / Decimal(whole))


def reference_for(db, business, product, currency):
    """The price this product could be sold at today, and where that figure came from. None when there is no honest one."""
    from api.app.market import INDICATORS, latest, weight_unit
    key = LOCAL_FOR.get(product.get('market_key') or '')
    if key:
        unit = INDICATORS[key][1].format(weight=weight_unit(currency))
        obs = latest(db, business, key, currency, unit)
        if obs and product.get('unit') in ('', unit.removeprefix('per ')):
            return dict(value_minor=obs['value_minor'], basis='market', source=obs['source'], kind=obs['kind'],
                        observed_at=obs['observed_at'], indicator=key)
        if obs:
            return None  # a market price exists but in a different unit; converting silently would mislead
    from api.app.market import product_indicator
    own = latest(db, business, product_indicator(product['id']), currency, 'per ' + (product.get('unit') or 'unit'))
    if own:  # this product's own market price, from the owner's quote or cited research
        return dict(value_minor=own['value_minor'], basis='market', source=own['source'], kind=own['kind'],
                    observed_at=own['observed_at'], indicator=product_indicator(product['id']))
    if product.get('price_minor') is not None:
        return dict(value_minor=product['price_minor'], basis='own_price', source='Your selling price', kind='owner', observed_at=None, indicator=None)
    return None


def position(product, reference):
    """Per-unit and total position. The gold example: cost 194,000, reference 200,000, costs 7,000 → net −1,000."""
    out = dict(id=product['id'], name=product['name'], unit=product.get('unit') or '', quantity=product.get('quantity'),
               cost_minor=product.get('cost_minor'), price_minor=product.get('price_minor'), expense_minor=product.get('expense_minor') or 0, reference=reference, missing=[])
    if product.get('cost_minor') is None:
        out['missing'].append('cost')
    if reference is None:
        out['missing'].append('reference')
    if out['missing']:
        out['status'] = 'incomplete'
        return out
    cost, ref, costs = product['cost_minor'], reference['value_minor'], out['expense_minor']
    qty = Decimal(product['quantity']) if product.get('quantity') else None
    gross, net = ref - cost, ref - cost - costs
    break_even = cost + costs
    cushion = pct_bp(ref - break_even, ref)
    out.update(gross_minor=gross, net_minor=net, break_even_minor=break_even, margin_bp=pct_bp(net, cost), cushion_bp=cushion,
               total_cost_minor=rounded(qty * cost) if qty is not None else None,
               exposure_minor=rounded(qty * ref) if qty is not None else None,
               total_net_minor=rounded(qty * net) if qty is not None else None,
               status='loss' if net < 0 else 'thin' if cushion is not None and cushion < THIN_CUSHION_BP else 'ok')
    if qty is None:
        out['missing'].append('quantity')
    return out


def scenarios(positions):
    """Total net position if every market reference moved by the same percentage. Own selling prices stay put."""
    rows = []
    usable = [p for p in positions if p['status'] != 'incomplete' and p['quantity']]
    for move in SCENARIOS:
        total = 0
        for p in usable:
            ref = p['reference']['value_minor']
            if p['reference']['basis'] == 'market':
                ref = rounded(Decimal(ref) * (100 + move) / 100)
            total += rounded(Decimal(p['quantity']) * (ref - p['break_even_minor']))
        rows.append(dict(move_pct=move, total_net_minor=total))
    return dict(rows=rows, products=len(usable), market_linked=sum(1 for p in usable if p['reference']['basis'] == 'market'))


def month_totals(ds):
    months = defaultdict(lambda: [0, 0])
    for event in ds.events:
        if event.status != 'cleared':
            continue
        months[str(event.effective_on)[:7]][0 if event.direction == 'inflow' else 1] += event.amount_minor
    return months


def growth(ds):
    """Month-over-month and year-over-year from cleared records. A missing or zero baseline gives no percentage."""
    months = month_totals(ds)
    as_of = date.fromisoformat(str(ds.as_of))
    last = date(as_of.year, as_of.month, 1)
    last = date(last.year - (last.month == 1), (last.month - 2) % 12 + 1, 1)  # the last complete month
    prev = date(last.year - (last.month == 1), (last.month - 2) % 12 + 1, 1)
    year_ago = date(last.year - 1, last.month, 1)
    key = lambda d: d.strftime('%Y-%m')
    income, spent = months.get(key(last), [0, 0])

    def change(now_value, base_key):
        base = months.get(base_key, [0, 0])[0]
        if not base:
            return dict(pct_bp=None, reason='No income recorded in the comparison month.')
        return dict(pct_bp=pct_bp(now_value - base, base), base_minor=base, reason='')
    return dict(month=key(last), income_minor=income, expenses_minor=spent, net_minor=income - spent,
                cash_margin_bp=pct_bp(income - spent, income),
                mom=change(income, key(prev)) if income else dict(pct_bp=None, reason='No income recorded last month.'),
                yoy=change(income, key(year_ago)) if income else dict(pct_bp=None, reason='No income recorded last month.'),
                months_recorded=sum(1 for v in months.values() if v[0] or v[1]),
                history=[dict(month=m, income_minor=v[0], expenses_minor=v[1]) for m, v in sorted(months.items())[-12:]])


def clamp(value):
    return max(0, min(100, value))


def capability(positions, grow, goals):
    # Scales: 25% cash margin or 40% margin over cost earns full marks; growth of +20% a month does too.
    """An explained score. Areas without data are listed, lower the confidence, and never count as zero."""
    areas = []

    def add(key, label, weight, score, why, missing=''):
        areas.append(dict(key=key, label=label, weight=weight, score=None if score is None else clamp(round(score)), why=why, missing=missing))
    margin = grow['cash_margin_bp']
    if margin is None and goals.get('monthly_revenue_minor') and goals.get('monthly_expenses_minor') is not None:
        margin = pct_bp(goals['monthly_revenue_minor'] - goals['monthly_expenses_minor'], goals['monthly_revenue_minor'])
        source = 'from the monthly figures in your profile'
    else:
        source = 'from last month\'s recorded cash'
    add('financial', 'Financial health', 25, None if margin is None else margin / 25,
        f'Cash margin {margin / 100:.1f}% {source}.' if margin is not None else '', '' if margin is not None else 'Record sales and expenses, or add monthly figures in your profile.')
    priced = [p for p in positions if p['status'] != 'incomplete']
    if priced:
        avg = sum(p['margin_bp'] or 0 for p in priced) / len(priced)
        add('pricing', 'Product pricing', 15, avg / 40, f'Average margin over cost {avg / 100:.1f}% across {len(priced)} product(s).')
    else:
        add('pricing', 'Product pricing', 15, None, '', 'Add purchase cost and a selling price or market price to products.')
    exposed = [p for p in priced if p.get('exposure_minor')]
    if exposed:
        total = sum(p['exposure_minor'] for p in exposed)
        risky = sum(p['exposure_minor'] for p in exposed if p['status'] in ('loss', 'thin'))
        share = pct_bp(risky, total) or 0
        add('risk', 'Risk control', 20, 100 - share / 100, f'{share / 100:.0f}% of stock value is at or near break-even.')
    else:
        add('risk', 'Risk control', 20, None, '', 'Add stock quantities with costs so exposure can be measured.')
    mom = grow['mom']['pct_bp']
    add('growth', 'Sales growth', 15, None if mom is None else 50 + mom / 40,
        f'Income changed {mom / 100:+.1f}% versus the month before.' if mom is not None else '', grow['mom']['reason'] if mom is None else '')
    for key, label, weight, need in (('inventory', 'Stock turnover', 10, 'Needs sales per product, which is not recorded yet.'),
                                     ('marketing', 'Marketing results', 5, 'Needs marketing spend and results.'),
                                     ('retention', 'Customer retention', 5, 'Needs repeat-customer records.'),
                                     ('scalability', 'Room to grow', 5, 'Needs a few months of history.' if grow['months_recorded'] < 3 else '')):
        if key == 'scalability' and grow['months_recorded'] >= 3:
            add(key, label, weight, (grow['cash_margin_bp'] or 0) / 25, f'{grow["months_recorded"]} months of records.')
        else:
            add(key, label, weight, None, '', need)
    scored = [a for a in areas if a['score'] is not None]
    weight = sum(a['weight'] for a in scored)
    return dict(score=round(sum(a['score'] * a['weight'] for a in scored) / weight) if weight else None,
                coverage_pct=weight, preliminary=weight < 50, areas=areas)


def build(db, business):
    from api.app.business import load_profile, products
    profile, _, _, _ = load_profile(db, business)
    ds = sec.runtime_dataset(db, business)
    currency = ds.currency
    positions = [position(p, reference_for(db, business, p, currency)) for p in products(db, business)]
    complete = [p for p in positions if p['status'] != 'incomplete' and p.get('exposure_minor') is not None]
    grow = growth(ds)
    return dict(currency=currency, positions=positions,
                totals=dict(cost_minor=sum(p['total_cost_minor'] for p in complete), exposure_minor=sum(p['exposure_minor'] for p in complete),
                            net_minor=sum(p['total_net_minor'] for p in complete), counted=len(complete), products=len(positions)),
                scenarios=scenarios(positions), growth=grow, capability=capability(positions, grow, profile.get('goals') or {}),
                simulation_only=True)


@router.get('')
def insights(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        return build(db, owner.business_id)
