"""Business-context conversations for Hisab-Chat, made with the code production uses.

Each example invents a small business from any industry (clothing, grocery, electronics, pharmacy, services...),
computes its real profit, break-even, growth, capability and advice with `api.app.insights` and `api.app.advisor`,
and renders the facts with `api.app.chat.context.render`, so the model sees exactly the block it gets live.
Answers come from `business_answers` and the advisor (exact, no network) or from teacher models for open questions,
kept only when every amount appears in the facts.
"""
import random
from datetime import date, timedelta
from types import SimpleNamespace

from api.app import advisor, insights
from api.app.business import gaps
from api.app.business_catalog import INDUSTRIES
from api.app.chat import business_answers, context, llm
from api.app.chat.understand import language

# (name, unit, cheapest cost, dearest cost) in taka; a few per industry, mixed with generic items.
CATALOG = {
    'apparel': [('Cotton saree', 'piece', 900, 2500), ('Three-piece', 'set', 700, 2200), ('Panjabi', 'piece', 600, 1800),
                ('Kids frock', 'piece', 250, 700), ('Jeans pant', 'piece', 450, 1100), ('Borka', 'piece', 800, 2000)],
    'grocery': [('Miniket rice', 'kg', 58, 72), ('Soybean oil', 'litre', 150, 175), ('Lentils (masoor)', 'kg', 95, 130),
                ('Sugar', 'kg', 115, 135), ('Onion', 'kg', 45, 110), ('Flour (atta)', 'kg', 48, 60)],
    'electronics': [('Smartphone (mid-range)', 'piece', 14000, 26000), ('Phone charger', 'piece', 180, 450),
                    ('Earbuds', 'piece', 450, 1800), ('LED bulb', 'piece', 90, 220), ('Power bank', 'piece', 900, 2200)],
    'pharmacy': [('Paracetamol strip', 'box', 8, 15), ('Antacid syrup', 'piece', 70, 110), ('Vitamin tablets', 'box', 180, 450),
                 ('Glucose meter strips', 'box', 650, 1100)],
    'restaurant': [('Chicken biryani', 'plate', 140, 220), ('Tea', 'cup', 6, 10), ('Burger', 'piece', 90, 160), ('Sweets (roshogolla)', 'kg', 220, 380)],
    'construction': [('Cement bag', 'bag', 480, 560), ('Rod (60 grade)', 'kg', 85, 105), ('Bricks', 'piece', 10, 14), ('Paint (4 litre)', 'piece', 1400, 2600)],
    'agriculture': [('Broiler chicken', 'kg', 140, 190), ('Eggs', 'dozen', 110, 150), ('Fish feed', 'bag', 1800, 2600), ('Cow milk', 'litre', 55, 80)],
    'cosmetics': [('Face cream', 'piece', 180, 650), ('Lipstick', 'piece', 150, 900), ('Shampoo', 'piece', 160, 420), ('Bridal makeup', 'service', 3000, 12000)],
    'jewelry': [('22K Gold ring', 'gram', 9500, 11500), ('Silver bangle', 'piece', 1200, 3500), ('Gold chain 21K', 'gram', 9000, 11000)],
    'ecommerce': [('Hijab', 'piece', 150, 450), ('Phone cover', 'piece', 60, 220), ('Handmade bag', 'piece', 350, 1200), ('Perfume', 'piece', 400, 1500)],
    'services': [('Tailoring (blouse)', 'service', 150, 400), ('AC servicing', 'service', 800, 1800), ('Event decoration', 'service', 15000, 60000)],
    'furniture': [('Wooden chair', 'piece', 2200, 5500), ('Steel almirah', 'piece', 9000, 18000), ('Plastic table', 'piece', 900, 1800)],
}
GENERIC = [('Packaging bags', 'piece', 2, 6), ('Delivery service', 'service', 50, 120)]
NAMES = ('Rahman', 'Bismillah', 'Shapla', 'Amin', 'Nabil', 'Mayer Doa', 'Sonar Bangla', 'Fatema', 'Karim', 'Hasan', 'Jamuna', 'Padma')
SUFFIX = ('Traders', 'Store', 'Enterprise', 'Fashion', 'Telecom', 'Bitan', 'House', 'Mart', 'Corner', 'Agency')
CITIES = ('Mirpur', 'Dhanmondi', 'Uttara', 'Old Dhaka', 'Chattogram', 'Sylhet', 'Khulna', 'Rajshahi', 'Bogura', 'Cumilla',
          'Narayanganj', 'Gazipur', 'Mymensingh', 'Rangpur', 'Barishal')
SOURCES = ('Shop next door', 'Kawran Bazar wholesale', 'Daraz listing', 'Dealer quote', 'Supplier price list', 'Facebook page listing')
COMPETITORS = ('Aarong', 'Shwapno', 'Agora', 'Daraz', 'Rokomari', 'Star Tech', 'Lazz Pharma', 'Pathao Food', 'Sailor', 'Yellow',
               'Bata', 'Apex', 'Walton', 'Meena Bazar', 'local market shops', 'a new shop across the road')
CHANNELS = ('shop', 'facebook', 'website', 'marketplace', 'whatsapp', 'phone', 'wholesale', 'field_sales')
GOALS = ('profit', 'sales', 'costs', 'compete', 'new_markets', 'inventory', 'protect_capital', 'retention', 'grow_lean')
QUESTIONS = {
    'profile': {'en': ['Tell me about my business', 'What is my business?', 'Summarise my business'],
                'bl': ['amar business sombondhe bolo', 'ami ki business kori?', 'amar business er summary dao'],
                'bn': ['আমার ব্যবসা সম্পর্কে বলো', 'আমি কী ব্যবসা করি?', 'আমার ব্যবসার সারাংশ দাও']},
    'advice': {'en': ['Give me ideas to grow my business', 'What should I do next?', 'Any advice for my shop?'],
               'bl': ['amar business er jonno idea dao', 'ekhon ki korbo?', 'bikri barate poramorsho dao'],
               'bn': ['আমার ব্যবসার জন্য পরামর্শ দাও', 'এখন কী করব?', 'বিক্রি বাড়াতে আইডিয়া দাও']},
    'market': {'en': ['How is the market?', 'What are market prices like?'], 'bl': ['bajar kemon?', 'market er dam kemon?'],
               'bn': ['বাজার কেমন?', 'বাজারদর কেমন?']},
    'competitors': {'en': ['Who are my competitors?'], 'bl': ['amar competitor ke ke?', 'protiyogi kara?'], 'bn': ['আমার প্রতিযোগী কারা?']},
    'products': {'en': ['Which product earns the most?', 'How are my products doing?'], 'bl': ['kon product e lav beshi?', 'amar product gula kemon cholche?'],
                 'bn': ['কোন পণ্যে লাভ বেশি?', 'আমার পণ্যগুলো কেমন চলছে?']},
    'risk': {'en': ['Any risk right now?', 'Show me my risks'], 'bl': ['kono jhuki ache?', 'risk ki ache ekhon?'], 'bn': ['কোনো ঝুঁকি আছে?']},
}
SCENARIO_Q = {'en': ['What if the {item} price drops {pct}%?', 'What happens if prices fall {pct}%?', 'What if the price rises {pct}%?'],
              'bl': ['{item} er dam {pct}% kome gele ki hobe?', 'dam {pct}% komle ki hobe?', 'dam {pct}% barle ki hobe?'],
              'bn': ['দাম {pct}% কমলে কী হবে?', 'দাম {pct}% বাড়লে কী হবে?']}
FLOOR_Q = {'en': ['What is my minimum selling price?', 'What is my break-even price?'], 'bl': ['minimum selling price koto?', 'break even dam koto?'],
           'bn': ['সর্বনিম্ন দাম কত হলে লস হবে না? minimum selling price']}
OPEN_Q = {'en': ['How can I get more customers before Eid?', 'Should I lower my prices to compete?', 'How do I sell more on Facebook?',
                 'Is it a good time to buy more stock?', 'How can I cut my costs?', 'Should I open a second branch?',
                 'How do I deal with a competitor who started a discount?', 'Which product should I focus on this month?'],
          'bl': ['Eid er age kivabe customer barabo?', 'competitor er sathe dam komabo naki?', 'facebook e kivabe bikri barabo?',
                 'ekhon ki stock barano thik hobe?', 'khoroch kivabe komabo?', 'notun branch khola thik hobe?',
                 'pasher dokan discount dicche, ami ki korbo?', 'ei mashe kon product e focus korbo?'],
          'bn': ['ঈদের আগে কীভাবে ক্রেতা বাড়াব?', 'প্রতিযোগীর সাথে দাম কমাব কি?', 'ফেসবুকে কীভাবে বিক্রি বাড়াব?',
                 'এখন কি স্টক বাড়ানো ঠিক হবে?', 'খরচ কীভাবে কমাব?', 'নতুন শাখা খোলা কি ঠিক হবে?']}


def taka(rng, low, high):
    return rng.randint(int(low * 100), int(high * 100)) // 100 * 100


def invent(rng, today=None):
    """A fictional business with real computed figures, in the shape `context.gather` returns."""
    today = today or date.today()
    industry = rng.choice([k for k in CATALOG])
    label, niches, _ = INDUSTRIES[industry]
    pool = CATALOG[industry] + GENERIC
    products, positions = [], []
    for n, (name, unit, low, high) in enumerate(rng.sample(pool, rng.randint(2, min(6, len(pool))))):
        cost = taka(rng, low, high)
        price = int(cost * rng.uniform(0.95, 1.45)) // 100 * 100 if rng.random() > 0.1 else None
        product = dict(id=f'p{n}', name=name, unit=unit, quantity=str(rng.choice((5, 10, 20, 40, 60, 100, 250))) if unit != 'service' else None,
                       cost_minor=None if rng.random() < 0.1 else cost, price_minor=price,
                       expense_minor=rng.choice((0, 0, cost // 50, cost // 20)), market_key=None)
        products.append(product)
        if rng.random() < 0.55:
            seen = today - timedelta(days=rng.randint(0, 10))
            ref = dict(value_minor=int((price or cost) * rng.uniform(0.8, 1.2)) // 100 * 100 or 100, basis='market',
                       source=rng.choice(SOURCES), kind=rng.choice(('owner', 'research')), observed_at=seen.isoformat() + 'T00:00:00+00:00',
                       indicator='p:' + product['id'])
        elif price is not None:
            ref = dict(value_minor=price, basis='own_price', source='Your selling price', kind='owner', observed_at=None, indicator=None)
        else:
            ref = None
        positions.append(insights.position(product, ref))
    months = rng.randint(0, 14)
    events = []
    base = rng.randint(50_000, 2_000_000) * 100
    for back in range(months):
        day = (today.replace(day=1) - timedelta(days=30 * back + 1)).replace(day=10)
        events.append(SimpleNamespace(effective_on=day, direction='inflow', amount_minor=int(base * rng.uniform(0.7, 1.3)), status='cleared'))
        events.append(SimpleNamespace(effective_on=day, direction='outflow', amount_minor=int(base * rng.uniform(0.6, 1.15)), status='cleared'))
    growth = insights.growth(SimpleNamespace(as_of=today, events=events))
    goals = rng.sample(GOALS, rng.randint(0, 3))
    channels = rng.sample(CHANNELS, rng.randint(1, 3))
    named = rng.sample(COMPETITORS, rng.randint(0, 2))
    raw = dict(identity=dict(industry=industry), operations=dict(channels=channels, competitors=named,
                                                                 expenses=[dict(name='Shop rent', monthly_minor=rng.randint(5, 60) * 100_000)]),
               goals=dict(goals=goals, growth_budget_minor=rng.choice((None, 2_000_000, 5_000_000, 20_000_000))))
    complete = [p for p in positions if p['status'] != 'incomplete' and p.get('exposure_minor') is not None]
    data = dict(currency='BDT', positions=positions, growth=growth,
                totals=dict(cost_minor=sum(p['total_cost_minor'] for p in complete), exposure_minor=sum(p['exposure_minor'] for p in complete),
                            net_minor=sum(p['total_net_minor'] for p in complete), counted=len(complete), products=len(positions)),
                scenarios=insights.scenarios(positions), capability=insights.capability(positions, growth, raw['goals']))
    found = advisor.find(data, raw)
    research = None
    if rng.random() < 0.4:
        research = dict(findings=[dict(text=f"Demand for {rng.choice(niches).lower()} usually rises before Eid and the wedding season.",
                                       sources=[dict(url='https://example.org/a', title=rng.choice(('prothomalo.com', 'thedailystar.net', 'tbsnews.net')))]),
                                  dict(text='Input costs rose in recent months, squeezing small retailers.', sources=[])],
                        competitors=[dict(name=c, kind=rng.choice(('direct', 'leader', 'indirect')), area=rng.choice(CITIES), why='Similar products nearby')
                                     for c in rng.sample(COMPETITORS, 2)])
    balance = rng.randint(-50_000, 5_000_000) * 100
    cash = (f'Currency: BDT. Snapshot date: {today}.\nCash balance: {money(balance)}.\n'
            f'Cleared income: {money(growth["income_minor"])}. Cleared expenses: {money(growth["expenses_minor"])}.\n'
            f'Open supplier invoices: {rng.randint(0, 6)}.')
    ctx = dict(owner=True, cash=cash, currency='BDT',
               profile=dict(name=f'{rng.choice(NAMES)} {rng.choice(SUFFIX)}', industry=label, niche=rng.choice(niches) if niches else label,
                            types=rng.sample(['retail', 'wholesale', 'online', 'service'], rng.randint(1, 2)), city=rng.choice(CITIES), country='Bangladesh',
                            description='', channels=channels, customers=rng.sample(['families', 'students', 'offices', 'shops', 'wedding buyers'], 2),
                            branches=rng.choice((None, 1, 2)), competitors=named, goals=goals, challenges='', monthly_revenue_minor=None, monthly_expenses_minor=None),
               positions=positions, totals=data['totals'], growth=growth, capability=data['capability'], scenarios=data['scenarios'],
               market=[], research=research, research_date=None,
               alerts=[f"CRITICAL: {p['name']} profitability at risk" for p in positions if p['status'] == 'loss'][:2],
               advice=[dict(title=i['title'], what=i['what'], why=i['why'], impact_minor=i['impact_minor'],
                            first=(i['strategies'][0]['title'] + ': ' + i['strategies'][0]['steps'][0]) if i['strategies'] else '') for i in found][:4],
               gaps=[g['field'] for g in gaps(raw, products)])
    return ctx, data


def money(minor):
    return context.money(minor, 'BDT')


def system(ctx):
    return llm.SYSTEM + llm.FACTS_HEADER + context.render(ctx)


def template_row(rng):
    """An exact answer from Capzoryx's own code: business questions, price scenarios and break-even."""
    ctx, data = invent(rng)
    lang = rng.choice(('bl', 'bl', 'bn', 'en'))
    # Price-move scenarios are left out on purpose: production answers them with code, and a model that learned to
    # produce new totals would be learning to do arithmetic. Every figure in these answers is already in the facts.
    if rng.random() < 0.85:
        intent = rng.choice(list(QUESTIONS))
        question = rng.choice(QUESTIONS[intent][lang])
        answer = business_answers.answer(intent, ctx, lang)
    else:
        question = rng.choice(FLOOR_Q[lang])
        answer = advisor.answer_from(data, ('break_even', None))
    return {'source': 'synthetic-business-template', 'messages': [
        {'role': 'system', 'content': system(ctx)}, {'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]}


def teacher_row(rng, teachers, deadline):
    """A natural answer to an open question about this business from a teacher model, kept only if grounded."""
    ctx, _ = invent(rng)
    lang = rng.choice(('bl', 'bl', 'bn', 'en'))
    question = rng.choice(OPEN_Q[lang])
    facts = context.render(ctx)
    answer, provider = teachers.complete([{'role': 'system', 'content': system(ctx)}, {'role': 'user', 'content': question}],
                                         deadline, prefer='local', max_tokens=450, temperature=0.6)
    answer = llm.clean(answer or '')
    if not answer or not llm.grounded(answer, facts, question) or (lang != 'en' and language(answer, answer.casefold().split()) != lang):
        return None
    return {'source': 'synthetic-business-teacher:' + provider, 'messages': [
        {'role': 'system', 'content': system(ctx)}, {'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]}
