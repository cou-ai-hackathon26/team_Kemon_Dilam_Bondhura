"""One central AI: web chat and Telegram answer about the whole business from the same context."""
from api.app.chat import brain, context, llm
from api.app.ratelimit import limiter
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_any_business import clothing_shop, market_price


def ask(c, text):
    limiter.reset()
    return c.post('/api/chat/message', json={'text': text}).json()


def test_business_questions_get_structured_answers_without_any_ai(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    monkeypatch.setattr(brain, 'llm_available', lambda db, business: False)  # prove these need no API
    items = clothing_shop(c, 'central_shop')
    c.post('/api/business/step/operations', json={'competitors': ['Aarong'], 'channels': ['shop']}).raise_for_status()
    c.post('/api/business/step/goals', json={'goals': ['sales']}).raise_for_status()
    market_price(c, items['Cotton saree'], 170_000)

    profile = ask(c, 'amar business sombondhe bolo')['text']
    assert profile.startswith('Apnar business:') and "Rahman Fashion: Women's clothing shop" in profile and 'Mirpur' in profile
    ideas = ask(c, 'amar business er jonno kichu idea dao')['text']
    assert ideas.startswith('Ekhon ja kora uchit:') and '1. Cotton saree is priced 18% above the market' in ideas and 'First step:' in ideas
    english = ask(c, 'Give me advice to grow my business')['text']
    assert english.startswith('What I would do next:')
    assert 'Aarong' in ask(c, 'amar competitor ke ke?')['text']
    market = ask(c, 'bajar kemon?')['text']
    assert 'Cotton saree: market BDT 1,700.00 (Shop next door' in market and 'you sell at BDT 2,000.00' in market
    products = ask(c, 'kon product e lav beshi?')['text']
    assert products.index('Three-piece') < products.index('Cotton saree')  # 20% over cost beats a net of 100 on 1,500
    assert 'Risks right now' in ask(c, 'any risk right now?')['text']
    bangla = ask(c, 'আমার ব্যবসা সম্পর্কে বলো')['text']
    assert bangla.startswith('আপনার ব্যবসা:')


def test_open_questions_reach_the_model_with_the_whole_business(world, monkeypatch):  # noqa: F811
    c, _ = world
    seen = {}
    monkeypatch.setattr('api.app.market.try_fetch', lambda *a: 'offline in tests')
    monkeypatch.setattr(brain, 'llm_available', lambda db, business: True)

    def fake_reply(question, history=(), facts='', share_facts=False):
        seen['facts'] = facts
        return 'One line.\n- First point\n- Second point\nNext step: do it.', 'hisab'
    monkeypatch.setattr(llm, 'reply', fake_reply)
    clothing_shop(c, 'central_llm')
    answer = ask(c, 'Eid season er jonno ki prostuti nite pari?')
    assert answer['text'] == 'One line.\n- First point\n- Second point\nNext step: do it.'  # line breaks survive
    facts = seen['facts']
    for expected in ("BUSINESS: Rahman Fashion, Women's clothing shop", 'PRODUCTS (2 in total', 'Cotton saree (40 piece): cost BDT 1,500.00',
                     'CASH AND BOOKS:', 'GROWTH (cleared records):'):
        assert expected in facts, facts


def test_render_without_profile_and_for_non_owners():
    cash = 'Cash balance: BDT 10.00.'
    assert context.render({'cash': cash, 'owner': False}) == 'CASH AND BOOKS:\n' + cash
    assert context.render({'cash': cash, 'owner': True, 'profile': None}).startswith('BUSINESS: not set up yet')
    assert llm.clean('**Answer**\n\n\n- one  two\n- three') == 'Answer\n\n- one two\n- three'
