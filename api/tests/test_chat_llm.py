import json

import httpx
import pytest

from api.app.chat import llm


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    llm._cooldown.clear()
    monkeypatch.delenv('SAFELEDGER_LLM_API_KEY', raising=False)
    for p in llm.PROVIDERS:
        for field in ('URL', 'KEY', 'MODEL'):
            monkeypatch.delenv(f'CAPZORYX_CHAT_{p.name.upper()}_{field}', raising=False)
    yield
    llm._cooldown.clear()


def route(monkeypatch, answers):
    seen = []

    def fake(request):
        host = request.url.host
        body = json.loads(request.content)
        seen.append((host, body))
        status, text = answers[host]
        return httpx.Response(status, json={'choices': [{'message': {'content': text}}]})
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(fake))
    return seen


def test_own_model_first_then_free_tiers_with_cooldown(monkeypatch):
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'https://hisab.example.org/v1')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'g')
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'm')
    seen = route(monkeypatch, {'hisab.example.org': (503, ''), 'api.groq.com': (429, ''),
                               'generativelanguage.googleapis.com': (200, '<think>x</think>**Bhalo** achi, apni?')})
    answer, name = llm.reply('kemon acho', facts='Cash balance: 150,000.00 BDT.')
    assert (answer, name) == ('Bhalo achi, apni?', 'gemini')
    assert [h for h, _ in seen] == ['hisab.example.org', 'api.groq.com', 'generativelanguage.googleapis.com']
    # Facts go only to the team's own model unless the owner allowed cloud sharing.
    assert '150,000.00' in seen[0][1]['messages'][0]['content']
    assert '150,000.00' not in seen[2][1]['messages'][0]['content']
    seen.clear()
    llm.reply('again')
    assert [h for h, _ in seen] == ['generativelanguage.googleapis.com']  # failed providers rest for a while


def test_invented_amounts_are_rejected(monkeypatch):
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'g')
    monkeypatch.setenv('CAPZORYX_CHAT_CEREBRAS_KEY', 'c')
    route(monkeypatch, {'api.groq.com': (200, 'You have ৳৯,৯৯,৯৯৯ in cash.'),
                        'api.cerebras.ai': (200, 'You have 150,000 taka in cash.')})
    answer, name = llm.reply('how much cash', facts='Cash balance: 150,000.00 BDT.', share_facts=True)
    assert name == 'cerebras' and '150,000' in answer
    assert llm.grounded('Sell 3 items for 2,500 each', '', 'price is 2500?')
    assert not llm.grounded('You owe 4,000', 'Open bills 3,892.41', 'bills?')


def test_chat_reuses_the_apps_existing_gemini_key(monkeypatch):
    monkeypatch.delenv('SAFELEDGER_LLM_PROFILE', raising=False)
    monkeypatch.setenv('SAFELEDGER_LLM_API_KEY', 'existing')
    assert [p.name for p in llm.available()] == ['gemini']
    monkeypatch.setenv('SAFELEDGER_LLM_PROFILE', 'compatible')
    assert not llm.configured()


def test_own_model_url_must_be_https_or_loopback(monkeypatch):
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'http://evil.example/v1')
    assert not llm.configured()
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'http://127.0.0.1:8080/v1')
    assert llm.configured()


def test_brain_uses_chain_for_open_questions(world, monkeypatch):
    from api.tests.test_accounts import signup
    c, _ = world
    signup(c, 'llm_chat_owner')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'g')
    route(monkeypatch, {'api.groq.com': (200, 'Eid-er age stock barano bhalo idea, kintu cash-flow dekhe niben.')})
    reply = c.post('/api/chat/message', json={'text': 'eid er age ki stock barabo?'}).json()
    assert reply['understood'] and 'stock' in reply['text']


from api.tests.test_accounts import world  # noqa: E402,F401
