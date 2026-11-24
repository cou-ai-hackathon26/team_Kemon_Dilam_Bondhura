"""Structured answers about the owner's own business, without any AI service.

"What is my business?", "give me ideas", "how is the market?", "who are my competitors?", "which product earns
most?", "any risk?" are answered from `context.gather`, in Bangla, Banglish or English headings, with every figure
taken from the owner's data. Open-ended questions still go to the model, which reads `context.render`.
"""
import re

from api.app.chat.context import money

INTENTS = (
    ('advice', re.compile(r'\b(idea|ideas|suggest|suggestion|advice|advise|tips?|grow|improve|strategy|what should i do|ki korbo|ki kora uchit|'
                          r'poramorsho|unnoti)\b|পরামর্শ|আইডিয়া|কী করব|কি করব|উন্নতি', re.I)),
    ('competitors', re.compile(r'\b(competitors?|rivals?|protidwondi|protiyogi|protijogi)\b|প্রতিযোগী|প্রতিদ্বন্দ্বী', re.I)),
    ('market', re.compile(r'\b(market|bajar|bazar)\b|বাজার', re.I)),
    ('risk', re.compile(r'\b(alerts?|risks?|jhuki|jhuki ache|danger|sotorkota)\b|ঝুঁকি|সতর্ক', re.I)),
    ('products', re.compile(r'\b(products?|ponno|ponyo|item|items)\b|পণ্য|প্রোডাক্ট', re.I)),
    ('profile', re.compile(r'\b(my business|amar business|amar byabsa|amar bebsa|about my business|business sombondhe|ki business|kon business)\b|আমার ব্যবসা|কী ব্যবসা|কি ব্যবসা', re.I)),
)
HEAD = {
    'profile': {'en': 'Your business', 'bl': 'Apnar business', 'bn': 'আপনার ব্যবসা'},
    'advice': {'en': 'What I would do next', 'bl': 'Ekhon ja kora uchit', 'bn': 'এখন যা করা উচিত'},
    'market': {'en': 'Your market', 'bl': 'Apnar bajar', 'bn': 'আপনার বাজার'},
    'competitors': {'en': 'Your competitors', 'bl': 'Apnar protiyogi', 'bn': 'আপনার প্রতিযোগী'},
    'products': {'en': 'Your products', 'bl': 'Apnar product', 'bn': 'আপনার পণ্য'},
    'risk': {'en': 'Risks right now', 'bl': 'Ekhonkar jhuki', 'bn': 'এখনকার ঝুঁকি'},
}
NOT_SET = {'en': 'Your business profile is not set up yet. Add it on the My business page (about 3 minutes) and I can answer this.',
           'bl': 'Apnar business profile ekhono deya hoyni. My business page-e diye din (3 minute), tarpor ami eta bolte parbo.',
           'bn': 'আপনার ব্যবসার প্রোফাইল এখনও দেওয়া হয়নি। "আমার ব্যবসা" পাতায় দিন (৩ মিনিট), তারপর আমি এটা বলতে পারব।'}
OWNER_ONLY = {'en': 'Only the owner of this workspace can see the business profile, products and advice.',
              'bl': 'Business profile, product ar poramorsho shudhu ei workspace-er owner dekhte paren.',
              'bn': 'ব্যবসার প্রোফাইল, পণ্য ও পরামর্শ শুধু এই ওয়ার্কস্পেসের মালিক দেখতে পারেন।'}


def detect(text):
    for name, pattern in INTENTS:
        if pattern.search(text):
            return name
    return None


def answer(intent, ctx, lang='en'):
    if not ctx.get('owner'):
        return OWNER_ONLY[lang]
    p = ctx.get('profile')
    if not p:
        return NOT_SET[lang]
    c = ctx['currency']
    out = [HEAD[intent][lang] + ':']
    if intent == 'profile':
        out.append(f"• {p['name']}: {p['niche']} ({p['industry']}), {', '.join(p['types'])}, {p['city']}, {p['country']}.")
        if p['channels']:
            out.append(f"• Sales channels: {', '.join(p['channels'])}.")
        if p['customers']:
            out.append(f"• Customers: {', '.join(p['customers'])}.")
        t = ctx['totals']
        out.append(f"• Products: {t['products']}" + (f"; stock worth {money(t['exposure_minor'], c)} at today's prices, net if sold today {money(t['net_minor'], c)}." if t['counted'] else '.'))
        if p['goals']:
            out.append(f"• Your goals: {', '.join(g.replace('_', ' ') for g in p['goals'])}.")
        if ctx['capability']['score'] is not None:
            out.append(f"• Capability score: {ctx['capability']['score']}/100" + (' (preliminary)' if ctx['capability'].get('preliminary') else '') + '.')
    elif intent == 'advice':
        if ctx['advice']:
            for n, a in enumerate(ctx['advice'][:3], start=1):
                out.append(f"{n}. {a['title']}. {a['why']}" + (f"\n   First step: {a['first']}" if a['first'] else ''))
            out.append('All options with cost and risk: Overview → What to do next.')
        else:
            weak = [a for a in ctx['capability']['areas'] if a['score'] is None and a['missing']][:2]
            out.append('• Nothing urgent in your numbers right now.')
            out += [f"• To get sharper advice: {a['missing']}" for a in weak]
            if p['goals'] and 'sales' in p['goals'] and 'facebook' not in p['channels']:
                out.append('• Your goal is more sales and you have no Facebook page listed: a free page with prices is the cheapest next step.')
    elif intent == 'market':
        prices = [m for m in ctx['market'] if m['latest']]
        for m in prices[:6]:
            last = m['latest']
            out.append(f"• {m['label']} {m['unit']}: {money(last['value_minor'], c)} ({last['source']}, {str(last['observed_at'])[:10]})")
        for x in ctx['positions']:
            ref = x.get('reference') or {}
            if ref.get('basis') == 'market' and str(ref.get('indicator', '')).startswith('p:'):
                out.append(f"• {x['name']}: market {money(ref['value_minor'], c)} ({ref['source']}, {str(ref['observed_at'])[:10]})"
                           + (f", you sell at {money(x['price_minor'], c)}" if x.get('price_minor') else ''))
        r = ctx.get('research')
        if r:
            out += [f"• {f['text']}" + (f" [{f['sources'][0]['title']}]" if f.get('sources') else '') for f in r.get('findings', [])[:3]]
        if len(out) == 1:
            out.append('• No market prices yet. Add prices you see on Overview → Your market, or run research there.')
    elif intent == 'competitors':
        r = ctx.get('research') or {}
        for x in (r.get('competitors') or [])[:6]:
            out.append(f"• {x['name']} ({x['kind']}{', ' + x['area'] if x.get('area') else ''}): {x.get('why') or ''}".rstrip(': '))
        if p['competitors']:
            out.append(f"• You named: {', '.join(p['competitors'])}.")
        if len(out) == 1:
            out.append('• None known yet. Name a few on the My business page, or run research there.')
    elif intent == 'products':
        ranked = sorted((x for x in ctx['positions'] if x['status'] != 'incomplete'), key=lambda x: -(x.get('margin_bp') or 0))
        for x in ranked[:5]:
            out.append(f"• {x['name']}: net {money(x['net_minor'], c)} per unit ({(x['margin_bp'] or 0) / 100:.0f}% over cost), break-even {money(x['break_even_minor'], c)}.")
        missing = [x['name'] for x in ctx['positions'] if x['status'] == 'incomplete']
        if missing:
            out.append(f"• No profit figure yet (cost or price missing): {', '.join(missing[:5])}.")
        if len(out) == 1:
            out.append('• No products yet. Add them on the My business page.')
    elif intent == 'risk':
        risky = [x for x in ctx['positions'] if x['status'] in ('loss', 'thin')]
        for x in risky[:4]:
            out.append(f"• {x['name']}: {'below' if x['status'] == 'loss' else 'close to'} break-even {money(x['break_even_minor'], c)}, net {money(x['net_minor'], c)} per unit.")
        out += [f"• Alert: {a}" for a in ctx['alerts'][:3]]
        g = ctx['growth']
        if g['cash_margin_bp'] is not None and g['cash_margin_bp'] < 0:
            out.append(f"• You spent more than you earned in {g['month']}.")
        if len(out) == 1:
            out.append('• No risk found in your numbers right now.')
    return '\n'.join(out)
