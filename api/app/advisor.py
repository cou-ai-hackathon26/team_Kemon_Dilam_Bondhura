"""The advisor: insights it finds on its own, three ways to act on each, competitor gaps, scenario answers and feedback.

Every number comes from `insights.build` (deterministic). Strategies are rule-based templates filled with the owner's
own figures, so they work without any AI service and never invent numbers. Feedback records what the owner chose and,
later, what changed, without claiming the advice caused it.
"""
import json
import re
import uuid
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.contracts import Contract
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api/advisor')
OUTCOME_AFTER = 30 * 86400
HIDE_DISMISSED = 30 * 86400


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS advisor_feedback(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, insight TEXT NOT NULL,
        tier TEXT NOT NULL, response TEXT NOT NULL CHECK(response IN ('doing','done','dismissed')), baseline_json TEXT NOT NULL,
        created_at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS advisor_feedback_by_business ON advisor_feedback(business_id, created_at);
    """)


def m(minor, currency):
    return f"{'-' if minor < 0 else ''}{currency} {abs(minor) / 100:,.2f}"


def share(total_minor, pct):
    return int((Decimal(total_minor) * Decimal(pct) / 100).to_integral_value(rounding=ROUND_HALF_EVEN))


def strategy(tier, title, steps, cost, benefit, effort, risk, measure):
    return dict(tier=tier, title=title, steps=steps, cost=cost, benefit=benefit, effort=effort, risk=risk, measure=measure)


def find(data, profile):
    """Insights, most urgent first. Each says what happened, why it matters, the impact, evidence, confidence and A/B/C."""
    c = data['currency']
    goals = profile.get('goals') or {}
    ops = profile.get('operations') or {}
    budget = goals.get('growth_budget_minor')
    budget_text = m(budget, c) if budget else 'the budget you set aside'
    out = []
    for p in data['positions']:
        if p['status'] not in ('loss', 'thin') or p['reference']['basis'] != 'market':
            continue
        ref = p['reference']
        exposure = p.get('exposure_minor') or 0
        further = share(exposure, 5)
        out.append(dict(
            key=f"risk:{p['id']}", kind='market_risk', severity=3 if p['status'] == 'loss' else 2,
            title=f"{p['name']} is {'below' if p['status'] == 'loss' else 'close to'} break-even",
            what=f"Today's reference price {m(ref['value_minor'], c)} against a break-even of {m(p['break_even_minor'], c)} "
                 f"(cost {m(p['cost_minor'], c)} plus selling costs {m(p['expense_minor'], c)}).",
            why='Selling now would not cover your full cost, and a further fall would widen the loss on all the stock you hold.',
            impact_minor=p.get('total_net_minor'), confidence='medium' if ref['kind'] in ('owner', 'research') else 'low',
            evidence=[f"{ref['source']}, {str(ref['observed_at'])[:10]}"],
            measure='Net position for this product on Your numbers.',
            strategies=[
                strategy('A', 'Protect what you have, no spending',
                         ['Ask two buyers or dealers for a firm price today; a published or listed price is not always what they pay.',
                          f"Sell only if a buyer pays above {m(p['break_even_minor'], c)}; otherwise hold.",
                          'Pause new purchases of this item until the price is back above break-even.'],
                         'No cost', f'Avoids adding to a position that would lose about {m(further, c)} more if prices fell another 5%.' if further else 'Avoids adding to a losing position.',
                         'Low', 'Prices may rise while you wait; holding also ties up cash.', 'Dealer quotes collected; no new stock bought below break-even.'),
                strategy('B', 'Move stock at a margin with a small offer',
                         ['Offer a bundle, exchange or upgrade deal that earns a service or add-on charge instead of a deep discount.',
                          f"Cap the promotion cost at a small part of {budget_text}."],
                         f"Up to {m(share(budget, 10), c)} (10% of your growth budget)" if budget else 'A small, fixed amount you choose',
                         'Turns stock into cash at or above break-even through charges rather than metal price.',
                         'Medium', 'Discounts can train customers to wait for offers.', 'Units sold through the offer and their net margin.'),
                strategy('C', 'Buy more only if cash is strong',
                         ['Consider adding stock at lower prices only when working capital covers three months of expenses.',
                          'Set a stop: if the price falls a further 5%, stop buying.'],
                         'Depends on the amount bought', 'Lowers your average cost if prices recover.',
                         'Medium', 'High: if prices keep falling, the loss grows with the extra stock.', 'Average cost per unit versus the reference price.'),
            ]))
    for p in data['positions']:
        if p['status'] == 'ok' and p['reference']['basis'] == 'own_price' and p['margin_bp'] is not None and p['margin_bp'] < 1500:
            qty = Decimal(p['quantity']) if p.get('quantity') else None
            gain = int(qty * share(p['reference']['value_minor'], 3)) if qty else None
            out.append(dict(
                key=f"margin:{p['id']}", kind='thin_margin', severity=1, title=f"Low margin on {p['name']}",
                what=f"You keep {m(p['net_minor'], c)} on each unit after costs, {p['margin_bp'] / 100:.1f}% over cost.",
                why='A thin margin leaves little room for discounts, damage or a cost increase from the supplier.',
                impact_minor=gain, confidence='high', evidence=['Your own cost and selling price'],
                measure='Margin for this product on Your numbers.',
                strategies=[
                    strategy('A', 'Price and supplier, no spending',
                             ['Check what nearby competitors charge for the same item.', 'If you are below them, raise the price by 3-5%.',
                              'Ask your supplier for a better rate on regular orders.'],
                             'No cost', f'A 3% higher price adds about {m(gain, c)} on the stock you hold.' if gain else 'A 3% higher price adds 3% to each sale.',
                             'Low', 'Some price-sensitive customers may buy less.', 'Units sold per week before and after.'),
                    strategy('B', 'Sell it alongside better-margin items',
                             ['Bundle it with a higher-margin product, or place those products next to it.'],
                             'Little or none', 'Raises the margin per customer visit.', 'Low', 'Bundles that are not wanted do not sell.', 'Average sale per customer.'),
                    strategy('C', 'Buy in bulk or change supplier',
                             ['Get quotes from two other suppliers; buy a larger lot only if it lowers unit cost by 5% or more.'],
                             f'Uses part of {budget_text}', 'Lower cost on every unit sold.', 'Medium', 'Stock that sells slowly ties up cash.', 'Unit cost on new purchases.'),
                ]))
    for p in data['positions']:
        ref = p.get('reference') or {}
        own = p.get('price_minor')
        if p['status'] == 'incomplete' or ref.get('basis') != 'market' or not own or not str(ref.get('indicator', '')).startswith('p:'):
            continue
        gap = round((own - ref['value_minor']) * 10000 / ref['value_minor'])
        qty = Decimal(p['quantity']) if p.get('quantity') else None
        if gap >= 1000:
            out.append(dict(
                key=f"above:{p['id']}", kind='priced_above_market', severity=2, title=f"{p['name']} is priced {gap / 100:.0f}% above the market",
                what=f"You sell at {m(own, c)}; the market price is {m(ref['value_minor'], c)} ({ref['source']}, {str(ref['observed_at'])[:10]}).",
                why='Customers who compare prices may buy elsewhere unless they see a clear reason to pay more.',
                impact_minor=None, confidence='medium', evidence=[f"{ref['source']}, {str(ref['observed_at'])[:10]}"],
                measure='Units sold per week of this product.',
                strategies=[
                    strategy('A', 'Show why you are worth more, or close the gap', ['Point out what you offer that others do not (quality, warranty, delivery, service).',
                             f"If sales are slowing, move the price toward {m(ref['value_minor'], c)} while staying above break-even {m(p['break_even_minor'], c)}."],
                             'No cost', 'Keeps price-sensitive customers.', 'Low', 'A lower price cuts margin on every unit.', 'Weekly units sold before and after.'),
                    strategy('B', 'A small, targeted offer', ['Run a limited offer for this product only, to repeat customers.'],
                             'A small, fixed amount you choose', 'Tests whether price is what holds sales back.', 'Low', 'Offers can become expected.', 'Sales during the offer.'),
                    strategy('C', 'Lower your cost to compete', ['Get quotes from other suppliers for this product.'],
                             'Time, and possibly a larger order', 'Lets you match the market without losing margin.', 'Medium', 'New suppliers can be less reliable.', 'Unit cost on new purchases.'),
                ]))
        elif gap <= -1000:
            gain = int(qty * (ref['value_minor'] - own) / 2) if qty else None
            out.append(dict(
                key=f"below:{p['id']}", kind='room_to_raise', severity=1, title=f"{p['name']} sells {abs(gap) / 100:.0f}% below the market",
                what=f"You sell at {m(own, c)}; the market price is {m(ref['value_minor'], c)} ({ref['source']}, {str(ref['observed_at'])[:10]}).",
                why='You may be giving away margin that customers would pay.',
                impact_minor=gain, confidence='medium', evidence=[f"{ref['source']}, {str(ref['observed_at'])[:10]}"],
                measure='Margin and units sold for this product.',
                strategies=[
                    strategy('A', 'Raise the price in a small step', [f"Move toward {m(ref['value_minor'], c)} in one or two steps and watch sales."],
                             'No cost', f'Closing half the gap adds about {m(gain, c)} on the stock you hold.' if gain else 'More margin on every unit.',
                             'Low', 'Some loyal customers may notice the change.', 'Units sold and margin per week.'),
                    strategy('B', 'Keep the price, add a paid extra', ['Offer delivery, packaging or a warranty as a paid add-on.'],
                             'Little or none', 'More income per sale without changing the headline price.', 'Low', 'Extras that are not wanted do not sell.', 'Add-ons sold.'),
                    strategy('C', 'Use the margin to grow', ['Spend part of the extra margin on reaching new customers.'],
                             'Part of the extra margin', 'Turns the price advantage into more customers.', 'Medium', 'Growth spending may not pay back.', 'New customers per month.'),
                ]))
    g = data['growth']
    if g['cash_margin_bp'] is not None and g['cash_margin_bp'] < 0:
        biggest = max(ops.get('expenses') or [], key=lambda e: e['monthly_minor'], default=None)
        out.append(dict(
            key=f"cash:{g['month']}", kind='cash_negative', severity=3,
            title='You spent more than you earned last month',
            what=f"Income {m(g['income_minor'], c)} against expenses {m(g['expenses_minor'], c)} in {g['month']}.",
            why='Repeated months like this drain cash; the forecast on Cash flow shows when it runs short.',
            impact_minor=g['net_minor'], confidence='high', evidence=['Your cleared records'], measure='Cash margin next month.',
            strategies=[
                strategy('A', 'Cut and collect, no spending',
                         [f"Review your largest regular cost{' (' + biggest['name'] + ', ' + m(biggest['monthly_minor'], c) + ' a month)' if biggest else ''}.",
                          'Call customers who owe you money; see Cash flow for the collection plan.', 'Delay purchases that are not urgent.'],
                         'No cost', 'Each taka collected or not spent goes straight to cash.', 'Low', 'Pushing customers too hard can cost sales.', 'Income minus expenses next month.'),
                strategy('B', 'Small targeted sales push', ['Run one offer for your best-selling item to your existing customers.'],
                         'A small, fixed amount you choose', 'More sales from people who already buy from you.', 'Medium', 'Offers cut margin.', 'Sales during the offer.'),
                strategy('C', 'Short-term financing as a bridge', ['Compare offers on the Loans page; borrow only to cover a known gap, not ongoing losses.'],
                         'Interest or profit share', 'Keeps the business running through a gap.', 'Medium', 'High if the shortfall continues.', 'Months the loan covers versus the gap.'),
            ]))
    if g['mom']['pct_bp'] is not None and g['mom']['pct_bp'] <= -1000:
        missing_channels = [x for x in ('facebook', 'marketplace', 'whatsapp') if x not in (ops.get('channels') or [])]
        out.append(dict(
            key=f"sales:{g['month']}", kind='sales_drop', severity=2, title=f"Income fell {abs(g['mom']['pct_bp']) / 100:.0f}% last month",
            what=f"{m(g['income_minor'], c)} in {g['month']}, down from {m(g['mom']['base_minor'], c)} the month before.",
            why='A drop that repeats becomes a cash problem; it is worth finding the cause early (season, prices, a competitor).',
            impact_minor=g['income_minor'] - g['mom']['base_minor'], confidence='high', evidence=['Your cleared records'],
            measure='Income this month versus last.',
            strategies=[
                strategy('A', 'Reach the customers you already have', ['Message past customers about new stock or an occasion.', 'Ask a few why they bought less.'],
                         'No cost', 'Repeat customers are the cheapest sales.', 'Low', 'Too many messages can annoy people.', 'Repeat purchases this month.'),
                strategy('B', 'A small paid promotion', [f"Boost one post or listing with a fixed budget{' from ' + budget_text if budget else ''}."],
                         'A small, fixed amount you choose', 'New customers in your area.', 'Medium', 'Ads can cost more than they bring.', 'Sales per taka spent.'),
                strategy('C', 'Open a new sales channel', [f"Start selling on {missing_channels[0]}." if missing_channels else 'Try a new area or wholesale buyers.'],
                         f'Uses part of {budget_text}', 'Reaches customers who do not visit you today.', 'High', 'Takes time before it pays back.', 'Sales from the new channel.'),
            ]))
    if any('cost' in p['missing'] for p in data['positions']):
        n = sum(1 for p in data['positions'] if 'cost' in p['missing'])
        out.append(dict(key='data:cost', kind='missing_data', severity=0, title=f'Add purchase cost to {n} product(s)',
                        what='Without the cost, profit, break-even and risk alerts cannot be calculated for them.',
                        why='Alerts and advice are only as good as the numbers behind them.', impact_minor=None, confidence='high',
                        evidence=[], measure='Products with full figures.', strategies=[]))
    return sorted(out, key=lambda x: -x['severity'])


def gaps(data, profile, research):
    """Where the owner can compete, with the reason for each. Uses only their own data and cited research."""
    ops = profile.get('operations') or {}
    goals = (profile.get('goals') or {}).get('goals') or []
    channels = set(ops.get('channels') or [])
    found = (research or {}).get('competitors') or []
    online = [c for c in found if any('facebook' in (s.get('url') or '') or 'instagram' in (s.get('url') or '') for s in c.get('sources', []))]
    out = []
    if not channels & {'facebook', 'website', 'marketplace'}:
        out.append(dict(lever='Digital visibility', why='You have no online channel listed' + (f', while {len(online)} competitor(s) were found with an online page.' if online else '.'),
                        idea='A free Facebook page or Google Business listing with prices and opening hours.'))
    priced = [p for p in data['positions'] if p['status'] != 'incomplete']
    if priced and any(p['status'] in ('loss', 'thin') for p in priced):
        out.append(dict(lever='Procurement and holding', why='Some stock is at or near break-even, so buying price and timing matter more than selling price.',
                        idea='Buy in smaller lots more often, and keep written buyback quotes from dealers.'))
    if 'retention' in goals or 'sales' in goals:
        out.append(dict(lever='Customer retention', why='You chose growing sales or keeping customers as a goal, and repeat buyers cost the least to win.',
                        idea='Keep a simple list of buyers and occasions (weddings, Eid) and contact them before those dates.'))
    if found:
        leaders = [c['name'] for c in found if c.get('kind') == 'leader']
        if leaders:
            out.append(dict(lever='Local positioning', why=f"Market leaders found nearby ({', '.join(leaders[:2])}) compete on brand and range.",
                            idea='Compete on personal service, custom orders and trust in your own area rather than on range.'))
    return out


SCENARIO = re.compile(r'(\d+(?:\.\d+)?)\s*(?:%|percent|parcent|shotangsho|শতাংশ|পার্সেন্ট)', re.I)
DOWN = re.compile(r'\b(drop|drops|fall|falls|falling|down|decrease|kome|kombe|komle|kom|kome jay|porle|pore)\b|কম|পড়', re.I)
UP = re.compile(r'\b(rise|rises|up|increase|bare|barle|barbe|bere)\b|বাড়', re.I)
PRICE = re.compile(r'price|dam|daam|dor|rate|দাম|দর|রেট', re.I)
BREAK_EVEN = re.compile(r'break.?even|minimum (selling )?price|lowest price|minimum dam|komp?ose kom dam|kom koto dam|সর্বনিম্ন দাম|ব্রেক.?ইভেন|লস ছাড়া', re.I)


def question(text):
    """('move', pct) for 'what if the price drops 10%?', ('break_even', None) for minimum-price questions, else None."""
    if BREAK_EVEN.search(text):
        return ('break_even', None)
    pct = SCENARIO.search(text)
    if pct and PRICE.search(text) and (DOWN.search(text) or UP.search(text)):
        value = Decimal(pct.group(1))
        if 0 < value <= 90:
            return ('move', -value if DOWN.search(text) and not UP.search(text) else value)
    return None


def net_at(positions, move):
    total = 0
    for p in positions:
        ref = p['reference']['value_minor']
        if p['reference']['basis'] == 'market':
            ref = int((Decimal(ref) * (100 + move) / 100).to_integral_value(rounding=ROUND_HALF_EVEN))
        total += int((Decimal(p['quantity']) * (ref - p['break_even_minor'])).to_integral_value(rounding=ROUND_HALF_EVEN))
    return total


def answer(db, business, ask):
    from api.app.insights import build
    return answer_from(build(db, business), ask)


def answer_from(data, ask):
    """The scenario or break-even answer from already-built figures (also used to make training data)."""
    c = data['currency']
    usable = [p for p in data['positions'] if p['status'] != 'incomplete']
    if ask[0] == 'break_even':
        if not usable:
            return ('I cannot work out a break-even price yet. Add your products with purchase cost (and any selling costs) '
                    'on the My business page, and a current price.')
        lines = ['Minimum selling price to avoid a loss (cost + your selling costs):']
        for p in usable:
            cushion = p['cushion_bp']
            state = (f"today's price is {abs(cushion) / 100:.1f}% below it" if cushion is not None and cushion < 0 else
                     f"today's price is {cushion / 100:.1f}% above it" if cushion is not None else '')
            lines.append(f"• {p['name']}: {m(p['break_even_minor'], c)} per {p['unit'] or 'unit'}" + (f" ({state})" if state else ''))
        missing = [p['name'] for p in data['positions'] if p['status'] == 'incomplete']
        if missing:
            lines.append(f"Not calculated (missing data): {', '.join(missing)}.")
        lines.append('Buyers may pay less than a listed or published price, so compare with a firm offer.')
        return '\n'.join(lines)
    move = Decimal(str(ask[1]))  # ints from callers and Decimals from the chat parser both work
    held = [p for p in usable if p.get('quantity') and p['reference']['basis'] == 'market']
    if not held:
        return ('I cannot run this scenario yet: it needs products with a market price, purchase cost and quantity. '
                'Add them on the My business page, with a current price.')
    worse, better = (move * Decimal('1.5'), move / 2) if move < 0 else (move / 2, move * Decimal('1.5'))
    today = net_at(held, 0)
    exposure = sum(p['exposure_minor'] for p in held)
    sources = sorted({f"{p['reference']['source']} ({str(p['reference']['observed_at'])[:10]})" for p in held})
    lines = [f"Your stock: {', '.join(p['quantity'] + ' ' + p['unit'] + ' ' + p['name'] for p in held)}, worth {m(exposure, c)} at today's prices.",
             f"If sold today after your selling costs: {m(today, c)}.",
             f"If the market price moves {move:+.0f}%:",
             f"• Base case ({move:+.0f}%): {m(net_at(held, move), c)}",
             f"• Better case ({better:+.1f}%): {m(net_at(held, better), c)}",
             f"• Worse case ({worse:+.1f}%): {m(net_at(held, worse), c)}"]
    if net_at(held, move) < 0:
        lines.append('In the base case you would be below break-even. Options: get firm prices from buyers, sell only above break-even, '
                     'and pause new purchases until prices recover.')
    else:
        lines.append('In the base case you would be above break-even.')
    lines.append(f"Prices used: {'; '.join(sources)}. These are calculations on your stock, not a forecast of where prices will go.")
    return '\n'.join(lines)


# ---------- routes ----------
def snapshot(data):
    return dict(net_minor=data['totals']['net_minor'], cash_margin_bp=data['growth']['cash_margin_bp'], income_minor=data['growth']['income_minor'])


class Feedback(Contract):
    insight: str = Field(min_length=3, max_length=80)
    tier: Literal['A', 'B', 'C', '-']
    response: Literal['doing', 'done', 'dismissed']


@router.get('')
def advise(actor=Depends(principal), db=Depends(db_connection)):
    from api.app.business import load_profile
    from api.app.insights import build
    from api.app.market import research_view
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        profile = load_profile(db, business)[0]
        data = build(db, business)
        research = research_view(db, business)
        now = sec.now()
        history = db.execute('SELECT insight,tier,response,baseline_json,created_at FROM advisor_feedback WHERE business_id=? '
                             'ORDER BY created_at, rowid', (business,)).fetchall()
        latest = {row[0]: row for row in history}
        hidden = {k for k, row in latest.items() if row[2] == 'dismissed' and now - row[4] < HIDE_DISMISSED}
        current = snapshot(data)
        found = []
        for item in find(data, profile):
            if item['key'] in hidden:
                continue
            row = latest.get(item['key'])
            item['choice'] = dict(tier=row[1], response=row[2], at=row[4]) if row else None
            found.append(item)
        outcomes = []
        for key, tier, response, baseline, at in history:
            if response in ('doing', 'done') and now - at >= OUTCOME_AFTER:
                before = json.loads(baseline)
                outcomes.append(dict(insight=key, tier=tier, since=at, before=before, now=current,
                                     note='This shows what changed since you chose it, not proof that the action caused the change.'))
        return dict(currency=data['currency'], insights=found, gaps=gaps(data, profile, research and research.get('result')),
                    outcomes=outcomes[-10:], simulation_only=True)


@router.post('/feedback', status_code=201)
def feedback(body: Feedback, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.insights import build
    with owner_transaction(db, actor) as owner:
        data = build(db, owner.business_id)
        db.execute('INSERT INTO advisor_feedback VALUES (?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, owner.business_id, body.insight, body.tier, body.response, json.dumps(snapshot(data)), sec.now()))
        sec.audit(db, owner, owner.business_id, 'ADVISOR_FEEDBACK', {'insight': body.insight.split(':')[0], 'response': body.response})
        return dict(saved=True, simulation_only=True)
