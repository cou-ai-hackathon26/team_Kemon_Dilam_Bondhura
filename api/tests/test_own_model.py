"""The own model: the ZeroGPU Space transport, and training data that uses production's own context and answers."""
import json
import random

import httpx

from api.app.chat import context, llm
from ml.hisab_chat import business_data


def test_zerogpu_space_is_called_first_and_its_reply_used(monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path, request.headers.get('authorization')))
        if request.method == 'POST':
            sent = json.loads(json.loads(request.content)['data'][0])
            assert sent[0]['role'] == 'system' and 'Facts' in sent[0]['content'] and sent[-1]['content'] == 'bajar kemon?'
            return httpx.Response(200, json={'event_id': 'abc'})
        return httpx.Response(200, text='event: complete\ndata: ["Ekhon bajar sthir.\\n- Saree: BDT 1,700.00"]\n\n')
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'https://owner-hisab-chat-zero.hf.space')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_MODE', 'zerogpu')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_KEY', 'hf_test')
    answer, provider = llm.reply('bajar kemon?', facts='Saree market price: BDT 1,700.00.', share_facts=False)
    assert provider == 'hisab' and answer == 'Ekhon bajar sthir.\n- Saree: BDT 1,700.00'
    assert calls == [('POST', '/gradio_api/call/chat', 'Bearer hf_test'), ('GET', '/gradio_api/call/chat/abc', 'Bearer hf_test')]


def test_space_quota_error_falls_back_and_rests(monkeypatch):
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(lambda r: httpx.Response(200, json={'event_id': 'x'}) if r.method == 'POST'
                                                              else httpx.Response(200, text='event: error\ndata: "quota"\n\n')))
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'https://owner-hisab-chat-zero.hf.space')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_MODE', 'zerogpu')
    for name in ('GROQ', 'GEMINI', 'CEREBRAS', 'OPENROUTER', 'MISTRAL', 'GITHUB'):
        monkeypatch.delenv(f'CAPZORYX_CHAT_{name}_KEY', raising=False)
    monkeypatch.delenv('SAFELEDGER_LLM_API_KEY', raising=False)
    llm._cooldown.clear()
    assert llm.reply('hello') == (None, None)
    assert 'hisab' in llm._cooldown  # resting, so the next questions go straight to the API chain
    llm._cooldown.clear()


def test_training_rows_use_the_production_format_for_any_industry():
    industries, kinds = set(), set()
    for seed in range(120):
        row = business_data.template_row(random.Random(seed))
        system, question, answer = (m['content'] for m in row['messages'])
        assert system.startswith(llm.SYSTEM + llm.FACTS_HEADER) and 'BUSINESS: ' in system and 'CASH AND BOOKS:' in system
        assert answer and question
        assert llm.grounded(answer, system, question), (question, answer)  # exact answers never use a figure not in the facts
        business_line = system.split('BUSINESS: ', 1)[1].split('\n', 1)[0]
        industries.add(business_line.split('(', 1)[1].split(')', 1)[0])
        kinds.add(answer.split(':', 1)[0])
    assert len(industries) >= 8 and len(kinds) >= 6
    ctx, _ = business_data.invent(random.Random(3))
    assert context.render(ctx).startswith('BUSINESS: ')


def test_free_cpu_copy_answers_when_gpu_quota_is_out(monkeypatch):
    seen = []

    def handle(request):
        if 'hf.space/gradio_api' in str(request.url):
            return httpx.Response(200, json={'event_id': 'q'}) if request.method == 'POST' else httpx.Response(200, text='event: error\ndata: "quota"\n\n')
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ekhon stock kom rakhun.\n- Cash: BDT 1,000.00'}}]})
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'https://owner-hisab-chat-zero.hf.space')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_MODE', 'zerogpu')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_CPU_URL', 'https://owner-hisab-chat.hf.space/v1')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_CPU_KEY', 'cpu-key')
    llm._cooldown.clear()
    facts = 'BUSINESS: Test shop.\nCash balance: BDT 1,000.00.\n' + ''.join(f'- Product {n}: cost BDT 1,{n:03d}.00\n' for n in range(200))
    answer, provider = llm.reply('ekhon ki korbo?', facts=facts)
    assert provider == 'hisab_cpu' and answer.startswith('Ekhon stock kom rakhun.')
    system = seen[0]['messages'][0]['content']
    assert 'BUSINESS: Test shop.' in system and 'More details exist' in system and len(system) < len(llm.SYSTEM) + 2100
    assert seen[0]['max_tokens'] == llm.CPU_MAX_TOKENS
    llm._cooldown.clear()


def test_own_model_reuses_the_hugging_face_token(monkeypatch):
    from api.app.chat import llm
    own = next(p for p in llm.PROVIDERS if p.name == 'hisab')
    monkeypatch.delenv('CAPZORYX_CHAT_HISAB_KEY', raising=False)
    monkeypatch.setenv('CAPZORYX_HISAB_HF_TOKEN', 'hf_test')
    assert own.key == 'hf_test'
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_KEY', 'hf_own')
    assert own.key == 'hf_own'


def _space_and_groq(monkeypatch, groq_text, seen):
    def handle(request):
        if request.url.host == 'api.groq.com':
            seen.append(json.loads(request.content)['messages'][1]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': groq_text}}]})
        if request.method == 'POST':
            return httpx.Response(200, json={'event_id': 'abc'})
        return httpx.Response(200, text='event: complete\ndata: ["Saree dam BDT 1,700.00. stock baran."]\n\n')
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_URL', 'https://owner-hisab-chat-zero.hf.space')
    monkeypatch.setenv('CAPZORYX_CHAT_HISAB_MODE', 'zerogpu')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_MODEL', 'llama-3.3-70b-versatile')
    llm._cooldown.clear()


def test_own_answer_is_polished_by_a_free_api_when_cloud_is_allowed(monkeypatch):
    seen = []
    _space_and_groq(monkeypatch, 'Ekhon saree-r dam BDT 1,700.00.\n- Stock ektu baran.', seen)
    answer, provider = llm.reply('bajar kemon?', facts='Saree market price: BDT 1,700.00.', share_facts=True)
    assert provider == 'hisab' and answer == 'Ekhon saree-r dam BDT 1,700.00.\n- Stock ektu baran.'
    assert 'Saree dam BDT 1,700.00' in seen[0]


def test_polish_that_invents_an_amount_keeps_the_draft(monkeypatch):
    seen = []
    _space_and_groq(monkeypatch, 'Saree-r dam BDT 2,500.00, stock baran.', seen)
    answer, _ = llm.reply('bajar kemon?', facts='Saree market price: BDT 1,700.00.', share_facts=True)
    assert answer == 'Saree dam BDT 1,700.00. stock baran.'


def test_owner_figures_never_reach_the_editor_without_cloud_consent(monkeypatch):
    seen = []
    _space_and_groq(monkeypatch, 'unused', seen)
    answer, _ = llm.reply('bajar kemon?', facts='Saree market price: BDT 1,700.00.', share_facts=False)
    assert seen == [] and answer == 'Saree dam BDT 1,700.00. stock baran.'
