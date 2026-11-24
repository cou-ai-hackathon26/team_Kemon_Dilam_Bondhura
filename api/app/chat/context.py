"""One picture of the business for every AI answer: web assistant, Telegram, the own model and its training data.

`gather` reads everything the owner may see (profile, products with real profit, market prices, research, alerts,
advice, cash and growth) into a plain dict. `render` turns that dict into the compact text block a model reads.
Training data is made with the same `render`, so the own model learns exactly the format it gets in production.
Non-owners only get the cash facts, as before.
"""
from api.app import security as sec

MAX_PRODUCTS = 12


def money(minor, currency):
    if minor is None:
        return 'not set'
    return f"{'-' if minor < 0 else ''}{currency} {abs(minor) / 100:,.2f}"


def gather(db, business, role='owner'):
    """Everything the AI may use for this business, as plain data. Call inside a transaction."""
    from api.app.assist import facts_text
    out = dict(cash=facts_text(db, business), owner=role == 'owner')
    if role != 'owner':
        return out
    from api.app import advisor, business as biz, insights, market
    from api.app.business_catalog import INDUSTRIES
    from api.app.monitor import alert_text
    profile = biz.load_profile(db, business)[0]
    data = insights.build(db, business)
    currency = data['currency']
    ident, ops, goals = profile.get('identity') or {}, profile.get('operations') or {}, profile.get('goals') or {}
    research = market.research_view(db, business)
    watched = [market.indicator_view(db, business, k, currency, sec.now()) for k in market.watched(db, business)]
    alerts = [alert_text(db, business, row[0]) for row in db.execute(
        'SELECT id FROM biz_alerts WHERE business_id=? ORDER BY created_at DESC, rowid DESC LIMIT 3', (business,)).fetchall()]
    out.update(
        currency=currency,
        profile=dict(name=ident.get('name'), industry=INDUSTRIES.get(ident.get('industry', ''), ('',))[0], niche=ident.get('niche'),
                     types=ident.get('business_types', []), city=ident.get('city'), country=ident.get('country'),
                     description=ident.get('description', ''), channels=ops.get('channels', []), customers=ops.get('customers', []),
                     branches=ops.get('branches'), competitors=ops.get('competitors', []), goals=goals.get('goals', []),
                     challenges=goals.get('challenges', ''), monthly_revenue_minor=goals.get('monthly_revenue_minor'),
                     monthly_expenses_minor=goals.get('monthly_expenses_minor')) if ident else None,
        positions=data['positions'], totals=data['totals'], growth=data['growth'], capability=data['capability'],
        scenarios=data['scenarios'],
        market=[dict(label=i['label'], unit=i['unit'], status=i['status'], latest=i['latest']) for i in watched],
        research=(research or {}).get('result') if (research or {}).get('status') == 'done' else None,
        research_date=(research or {}).get('finished_at'),
        alerts=[a.split('\n', 1)[0] for a in alerts if a],
        advice=[dict(title=i['title'], what=i['what'], why=i['why'], impact_minor=i['impact_minor'],
                     first=(i['strategies'][0]['title'] + ': ' + i['strategies'][0]['steps'][0]) if i['strategies'] else '')
                for i in advisor.find(data, profile)][:4],
        gaps=[g['field'] for g in biz.gaps(profile, biz.products(db, business))],
        notes=[dict(kind=n['kind'], title=n['title'], text=' '.join(n['text'].split())[:800]) for n in biz.notes(db, business, limit=8)])
    return out


def render(ctx):
    """The text block every model reads. Short lines, every figure with its source, nothing invented."""
    lines = ['CASH AND BOOKS:', ctx['cash']]
    if not ctx.get('owner'):
        return '\n'.join(lines)
    p = ctx.get('profile')
    if not p:
        lines.insert(0, 'BUSINESS: not set up yet (the owner can add it on the My business page).')
        lines += [f"- Owner's note ({n['kind']}): {n['title']}: {n['text']}" for n in ctx.get('notes') or []]
        return '\n'.join(lines)
    c = ctx['currency']
    head = [f"BUSINESS: {p['name']}, {p['niche']} ({p['industry']}); sells as {', '.join(p['types']) or 'not stated'}; "
            f"in {p['city']}, {p['country']}."]
    if p.get('description'):
        head.append(f"About: {p['description']}")
    if p['channels'] or p['customers']:
        head.append(f"Sales channels: {', '.join(p['channels']) or 'not stated'}. Customers: {', '.join(p['customers']) or 'not stated'}."
                    + (f" Branches: {p['branches']}." if p.get('branches') else ''))
    if p['goals']:
        head.append(f"Owner's goals: {', '.join(g.replace('_', ' ') for g in p['goals'])}.")
    if p.get('challenges'):
        head.append(f"Biggest challenge: {p['challenges']}")
    if p.get('monthly_revenue_minor') is not None:
        head.append(f"Owner's estimate: monthly sales {money(p['monthly_revenue_minor'], c)}, monthly expenses {money(p.get('monthly_expenses_minor'), c)}.")
    lines = head + [''] + lines
    positions = sorted(ctx['positions'], key=lambda x: -(x.get('exposure_minor') or 0))[:MAX_PRODUCTS]
    if positions:
        lines += ['', f"PRODUCTS ({len(ctx['positions'])} in total; most valuable first):"]
        for x in positions:
            ref = x.get('reference')
            qty = f"{x['quantity']} {x['unit']}" if x.get('quantity') else x['unit'] or '-'
            if x['status'] == 'incomplete':
                seen = (f"price today {money(ref['value_minor'], c)} ({'your own selling price' if ref['basis'] == 'own_price' else ref['source'] + ', ' + str(ref['observed_at'])[:10]}); "
                        if ref else '')
                own = f"your own price {money(x['price_minor'], c)}; " if x.get('price_minor') and ref and ref['basis'] == 'market' else ''
                lines.append(f"- {x['name']} ({qty}): cost {money(x['cost_minor'], c)}; {seen}{own}missing {', '.join(x['missing'])}, so profit is unknown.")
                continue
            source = 'your own selling price' if ref['basis'] == 'own_price' else f"{ref['source']}, {str(ref['observed_at'])[:10]}"
            lines.append(f"- {x['name']} ({qty}): cost {money(x['cost_minor'], c)}, price today {money(ref['value_minor'], c)} ({source}), "
                         f"selling costs {money(x['expense_minor'], c)}, net {money(x['net_minor'], c)} per unit, "
                         f"break-even {money(x['break_even_minor'], c)}, status {x['status']}"
                         + (f", your own price {money(x['price_minor'], c)}" if x.get('price_minor') and ref['basis'] == 'market' else '') + '.')
        t = ctx['totals']
        if t['counted']:
            lines.append(f"Stock: {money(t['cost_minor'], c)} at cost, {money(t['exposure_minor'], c)} at today's prices, net if sold today "
                         f"{money(t['net_minor'], c)} ({t['counted']} of {t['products']} products counted).")
    g = ctx['growth']
    lines += ['', 'GROWTH (cleared records):']
    if g['income_minor'] or g['expenses_minor']:
        lines.append(f"{g['month']}: income {money(g['income_minor'], c)}, expenses {money(g['expenses_minor'], c)}"
                     + (f", cash margin {g['cash_margin_bp'] / 100:.1f}%" if g['cash_margin_bp'] is not None else '') + '.')
        for key, label in (('mom', 'vs the month before'), ('yoy', 'vs the same month last year')):
            lines.append(f"Income {label}: {g[key]['pct_bp'] / 100:+.1f}%." if g[key]['pct_bp'] is not None else f"Income {label}: unknown ({g[key]['reason']})")
    else:
        lines.append(f"No cleared income or expenses recorded in {g['month']}.")
    cap = ctx['capability']
    if cap['score'] is not None:
        weak = [a['label'] for a in sorted((a for a in cap['areas'] if a['score'] is not None), key=lambda a: a['score'])][:2]
        lines.append(f"Capability score {cap['score']}/100 from {cap['coverage_pct']}% of areas"
                     + (' (preliminary)' if cap.get('preliminary') else '') + (f"; weakest: {', '.join(weak)}." if weak else '.'))
    prices = [m for m in ctx['market'] if m['latest']]
    if prices or ctx['market']:
        lines += ['', 'MARKET PRICES:']
        for m in ctx['market']:
            last = m['latest']
            lines.append(f"- {m['label']} {m['unit']}: " + (f"{money(last['value_minor'], c)} ({last['kind']}, {last['source']}, {str(last['observed_at'])[:10]})"
                                                             + (' — out of date' if m['status'] == 'stale' else '') if last else 'unavailable'))
    r = ctx.get('research')
    if r:
        lines += ['', 'MARKET RESEARCH (public web, with sources):']
        for f in r.get('findings', [])[:5]:
            lines.append(f"- {f['text']}" + (f" [{f['sources'][0]['title']}]" if f.get('sources') else ' [unverified]'))
        if r.get('competitors'):
            lines.append('Competitors found: ' + '; '.join(f"{x['name']} ({x['kind']}{', ' + x['area'] if x.get('area') else ''})"
                                                         for x in r['competitors'][:6]) + '.')
    if p['competitors']:
        lines.append(f"Competitors the owner named: {', '.join(p['competitors'])}.")
    if ctx['alerts']:
        lines += ['', 'RECENT ALERTS: ' + ' | '.join(ctx['alerts'])]
    if ctx['advice']:
        lines += ['', 'ADVISOR FINDINGS (from the owner\'s numbers):']
        for a in ctx['advice']:
            lines.append(f"- {a['title']}. {a['what']}" + (f" First step: {a['first']}" if a['first'] else ''))
    if ctx.get('notes'):
        lines += ['', 'BUSINESS DATA THE OWNER SUBMITTED (their own words):']
        lines += [f"- {n['kind']}: {n['title']}: {n['text']}" for n in ctx['notes']]
    if ctx['gaps']:
        lines.append(f"Missing information: {', '.join(ctx['gaps'])}.")
    return '\n'.join(lines)


def text(db, business, role='owner'):
    return render(gather(db, business, role))
