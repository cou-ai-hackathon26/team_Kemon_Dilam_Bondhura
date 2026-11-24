import json

import httpx
import pytest

from api.app import market
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_business import IDENTITY

BASE = '/api/market'
RATES = {'date': '2026-11-23', 'usd': {'bdt': 122.0, 'xau': 0.00025, 'xag': 0.02, 'eur': 0.9}}
ANSWER = ('FINDING: Gold demand in Dhaka rises before the wedding season (Nov-Feb 2026).\n'
          'FINDING: An unsupported claim with no citation.\n'
          'COMPETITOR: Diamond World | leader | Dhaka | Large national jewellery chain\n'
          'COMPETITOR: Apan Jewellers | direct | Gulshan | Similar gold retail\n'
          'PRICE: gold_22k_local | 2,33,000 | 2026-11-22\n'
          'PRICE: gold_21k_local | 222000 | 2026-11-22\n')


def grounded(text=ANSWER):
    first, comp, price = text.splitlines()[0], text.splitlines()[2], text.splitlines()[4]
    return {'candidates': [{'content': {'parts': [{'text': text}]}, 'groundingMetadata': {
        'webSearchQueries': ['gold price dhaka'],
        'groundingChunks': [{'web': {'uri': 'https://example.org/gold-news', 'title': 'example.org'}},
                            {'web': {'uri': 'https://bajus.example/price', 'title': 'bajus.example'}},
                            {'web': {'uri': 'javascript:alert(1)', 'title': 'bad'}}],
        'groundingSupports': [{'segment': {'text': first.split(': ', 1)[1]}, 'groundingChunkIndices': [0]},
                              {'segment': {'text': comp.split(': ', 1)[1]}, 'groundingChunkIndices': [0]},
                              {'segment': {'text': price.split(': ', 1)[1]}, 'groundingChunkIndices': [1]}]}}]}


@pytest.fixture
def net(monkeypatch):
    state = {'rates': True, 'gemini': [], 'answer': grounded()}

    def handle(request):
        if 'currency-api' in str(request.url):
            return httpx.Response(200, json=RATES) if state['rates'] else httpx.Response(503)
        if 'generativelanguage' in str(request.url):
            body = json.loads(request.content)
            state['gemini'].append(body['contents'][0]['parts'][0]['text'])
            assert body['tools'] == [{'google_search': {}}]
            return httpx.Response(200, json=state['answer'])
        return httpx.Response(404)
    monkeypatch.setattr(market, 'TRANSPORT', httpx.MockTransport(handle))
    market._failures.clear()
    return state


def setup_gold(c, research=True):
    c.post('/api/business/step/identity', json=IDENTITY).raise_for_status()
    c.post('/api/business/products', json={'name': '22K Gold', 'category': 'Gold', 'unit': 'bhori', 'quantity': '5',
                                           'cost_minor': 19_400_000, 'price_minor': 22_000_000}).raise_for_status()
    c.post('/api/business/step/consent', json={'external_research': research, 'alerts': True}).raise_for_status()


def test_market_follows_products_and_labels_every_figure(world, net):  # noqa: F811
    c, _ = world
    signup(c, 'market_gold')
    assert c.get(BASE).json()['indicators'] == []  # before setup nothing is followed
    setup_gold(c)
    view = c.get(BASE).json()
    keys = [i['key'] for i in view['indicators']]
    assert keys == ['fx_usd', 'gold_intl', 'silver_intl', 'gold_22k_local']
    by = {i['key']: i for i in view['indicators']}
    assert by['fx_usd']['latest']['value_minor'] == 12_200 and by['fx_usd']['latest']['kind'] == 'delayed'
    # 1/0.00025 = 4000 USD/oz -> x122 x 11.664/31.1034768 = BDT 183,003.08 per bhori
    gold = by['gold_intl']
    assert gold['unit'] == 'per bhori' and gold['latest']['kind'] == 'derived' and gold['latest']['value_minor'] == 18_300_308
    assert by['gold_22k_local']['status'] == 'needs_quote' and by['gold_22k_local']['latest'] is None

    # F: a new product changes what is watched; removing it takes it away again.
    c.post('/api/business/products', json={'name': 'Silver bangle', 'category': 'Silver', 'unit': 'bhori'}).raise_for_status()
    assert 'silver_local' in [i['key'] for i in c.get(BASE).json()['indicators']]
    silver = next(p for p in c.get('/api/business').json()['products'] if p['name'] == 'Silver bangle')
    c.post(f"/api/business/products/{silver['id']}/delete", json={}).raise_for_status()
    assert 'silver_local' not in [i['key'] for i in c.get(BASE).json()['indicators']]

    # The owner's own quote is labelled as theirs; future dates and unwatched indicators are refused.
    assert c.post(BASE + '/quotes', json={'indicator': 'gold_22k_local', 'value_minor': 1, 'observed_on': '2999-01-01', 'source': 'BAJUS'}).status_code == 422
    assert c.post(BASE + '/quotes', json={'indicator': 'silver_local', 'value_minor': 1, 'observed_on': '2026-11-20', 'source': 'BAJUS'}).status_code == 404
    quoted = c.post(BASE + '/quotes', json={'indicator': 'gold_22k_local', 'value_minor': 20_000_000, 'observed_on': '2026-11-20', 'source': 'BAJUS website'}).json()
    local = next(i for i in quoted['indicators'] if i['key'] == 'gold_22k_local')
    assert local['status'] == 'ok' and local['latest']['kind'] == 'owner' and local['latest']['value_minor'] == 20_000_000


def test_failed_source_is_shown_as_unavailable_never_invented(world, net):  # noqa: F811
    c, _ = world
    net['rates'] = False
    signup(c, 'market_down')
    setup_gold(c)
    view = c.get(BASE).json()
    for item in view['indicators']:
        assert item['latest'] is None and item['status'] in ('unavailable', 'needs_quote')
    assert 'not reachable' in next(i for i in view['indicators'] if i['key'] == 'gold_intl')['reason']


def test_research_is_cited_consented_and_honest_when_off(world, net, monkeypatch):  # noqa: F811
    c, _ = world
    signup(c, 'market_research')
    setup_gold(c, research=False)
    assert c.post(BASE + '/research', json={}).status_code == 409  # owner did not allow it
    c.post('/api/business/step/consent', json={'external_research': True}).raise_for_status()

    monkeypatch.delenv('SAFELEDGER_LLM_API_KEY', raising=False)
    monkeypatch.delenv('CAPZORYX_CHAT_GEMINI_KEY', raising=False)
    off = c.post(BASE + '/research', json={}).json()['research']
    assert off['status'] == 'unavailable' and 'No web research service' in off['reason'] and net['gemini'] == []

    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'test-key')
    c.post(BASE + '/research', json={}).raise_for_status()
    prompt = net['gemini'][0]
    assert 'Gold trading' in prompt and 'Dhaka' in prompt and '22K Gold' in prompt
    assert '194' not in prompt and '220' not in prompt  # costs and prices are never sent
    research = c.get(BASE).json()['research']
    assert research['status'] == 'done'
    findings = research['result']['findings']
    assert findings[0]['sources'][0]['url'] == 'https://example.org/gold-news' and findings[1]['sources'] == []
    assert [x['name'] for x in research['result']['competitors']] == ['Diamond World', 'Apan Jewellers']
    assert research['result']['competitors'][0]['kind'] == 'leader'
    local = next(i for i in c.get(BASE).json()['indicators'] if i['key'] == 'gold_22k_local')
    # Only the cited 22K price became an observation; the uncited 21K line did not.
    assert local['latest']['kind'] == 'research' and local['latest']['value_minor'] == 23_300_000
    assert local['latest']['url'] == 'https://bajus.example/price'
    assert all(s['url'].startswith('https://') for s in research['result']['sources'])  # a javascript: link is dropped
    assert c.post(BASE + '/research', json={}).status_code == 429  # just researched and nothing changed

    c.post('/api/auth/logout', json={})
    signup(c, 'market_other')
    other = c.get(BASE).json()
    assert other['research'] is None and all(i['key'] != 'gold_22k_local' for i in other['indicators'])


def test_research_starts_by_itself_after_setup_and_refreshes_in_the_background(world, net, monkeypatch):  # noqa: F811
    from contextlib import closing
    from api.app import monitor
    from api.app.database import connect
    c, _ = world
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'test-key')
    signup(c, 'market_auto')
    setup_gold(c)
    done = c.post('/api/business/complete', json={}).json()
    assert done['research_started'] and len(net['gemini']) == 1  # no button press needed
    assert c.get(BASE).json()['research']['status'] == 'done'
    with closing(connect()) as db:
        assert monitor.research_due(db) == 0  # fresh, nothing changed
    c.post('/api/business/products', json={'name': 'Silver bangle', 'category': 'Silver', 'unit': 'bhori'}).raise_for_status()
    with closing(connect()) as db:
        assert monitor.research_due(db) == 1  # products changed, so the background pass researches again
    assert len(net['gemini']) == 2


def test_research_falls_back_to_groq_web_search_when_gemini_is_busy(world, monkeypatch):  # noqa: F811
    calls = []

    def handle(request):
        url = str(request.url)
        if 'currency-api' in url:
            return httpx.Response(200, json=RATES)
        if 'generativelanguage' in url:
            calls.append('gemini')
            return httpx.Response(503, json={'error': {'message': 'high demand'}})
        if url.endswith('/openai/v1/models'):
            return httpx.Response(200, json={'data': [{'id': 'openai/gpt-oss-120b'}, {'id': 'llama-3.3-70b-versatile'}]})
        if 'api.groq.com' in url:
            body = json.loads(request.content)
            calls.append(body['model'])
            assert body.get('tools') == [{'type': 'browser_search'}]  # no compound model on this account
            text = ('FINDING: Biryani demand in Mirpur peaks on Fridays and during Ramadan evenings.\n'
                    'FINDING: Something the pages do not support at all.\n'
                    'COMPETITOR: Kacchi Bhai | leader | Mirpur | Popular biryani chain\n'
                    'COMPETITOR: Imaginary Kitchen | direct | Mirpur | Not on any page\n'
                    'PRICE: 1 | 260 | 2026-11-20\n')
            pages = [{'title': 'Kacchi Bhai menu', 'url': 'https://example.org/menu',
                      'content': 'Kacchi Bhai Mirpur: chicken biryani plate Tk 260. Biryani demand peaks Fridays Ramadan evenings Mirpur.'}]
            return httpx.Response(200, json={'choices': [{'message': {'content': text, 'executed_tools': [
                {'type': 'search', 'search_results': {'results': pages}}]}}]})
        return httpx.Response(404)
    monkeypatch.setattr(market, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'g')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'q')
    c, _ = world
    signup(c, 'market_groq')
    c.post('/api/business/step/identity', json=dict(IDENTITY, industry='restaurant', niche='Restaurant', city='Mirpur')).raise_for_status()
    c.post('/api/business/products', json={'name': 'Chicken biryani', 'unit': 'plate', 'cost_minor': 15_000, 'price_minor': 22_000}).raise_for_status()
    c.post('/api/business/step/consent', json={'external_research': True}).raise_for_status()
    c.post(BASE + '/research', json={}).raise_for_status()
    assert calls[0] == 'gemini' and calls[-1] == 'openai/gpt-oss-120b' and not any('compound' in c for c in calls)
    research = c.get(BASE).json()['research']
    assert research['status'] == 'done'
    findings, competitors = research['result']['findings'], {x['name']: x for x in research['result']['competitors']}
    assert findings[0]['sources'][0]['url'] == 'https://example.org/menu' and findings[1]['sources'] == []
    assert competitors['Kacchi Bhai']['sources'] and competitors['Imaginary Kitchen']['sources'] == []
    biryani = next(p for p in c.get(BASE).json()['products'] if p['name'] == 'Chicken biryani')
    assert biryani['latest']['value_minor'] == 26_000 and biryani['latest']['kind'] == 'research'


def test_failed_research_shows_a_plain_message(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market, 'TRANSPORT', httpx.MockTransport(lambda r: httpx.Response(503)))
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'g')
    monkeypatch.delenv('CAPZORYX_CHAT_GROQ_KEY', raising=False)
    c, _ = world
    signup(c, 'market_busy')
    setup_gold(c)
    c.post(BASE + '/research', json={}).raise_for_status()
    research = c.get(BASE).json()['research']
    assert research['status'] == 'failed' and 'busy' in research['reason'] and '503' not in research['reason']


def test_interrupted_research_stops_spinning(world, net, monkeypatch):  # noqa: F811
    from contextlib import closing
    from api.app.database import connect
    c, _ = world
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'test-key')
    signup(c, 'market_stuck')
    setup_gold(c)
    business = c.get('/api/session').json()['business_id']
    with closing(connect()) as db, db:
        db.execute("INSERT INTO research_runs(id,business_id,status,created_at,inputs) VALUES ('stuck',?, 'running', ?, '')",
                   (business, 1_000))  # started long ago; the server restarted before it finished
    research = c.get(BASE).json()['research']
    assert research['status'] == 'failed' and 'interrupted' in research['reason']
    again = c.post(BASE + '/research', json={})
    assert again.status_code == 202 and c.get(BASE).json()['research']['status'] == 'done'
