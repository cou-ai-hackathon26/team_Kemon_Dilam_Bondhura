import json

import httpx
import pytest

from api.app.chat import llm
from ml.hisab_chat import self_play, teachers


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    llm._cooldown.clear()
    for p in llm.PROVIDERS:
        for field in ('URL', 'KEY', 'MODEL'):
            monkeypatch.delenv(f'CAPZORYX_CHAT_{p.name.upper()}_{field}', raising=False)
    monkeypatch.delenv('SAFELEDGER_LLM_API_KEY', raising=False)
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'g')
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'm')
    for name in ('GROQ', 'GEMINI'):
        monkeypatch.setenv('CAPZORYX_TEACHER_RPM_' + name, '6000')


def test_teacher_grades_the_student_and_keeps_only_grounded_rewrites(monkeypatch, tmp_path):
    hosts = []

    def fake(request):
        hosts.append(request.url.host)
        body = json.loads(request.content)
        prompt = body['messages'][-1]['content']
        if request.url.host == 'student.local':
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ami jani na'}}]})
        if 'Write 8 different messages' in prompt:
            content = json.dumps({'messages': ['cash kto ase vai', 'বিক্রি কত হলো?']})
        elif 'cash kto' in prompt:
            balance = prompt.split('Cash balance: ')[1].split('.00')[0]
            content = json.dumps({'score': 2, 'better': f'Ekhon hate {balance} taka ache, vai.'})
        else:  # an invented amount must be dropped by the number check
            content = json.dumps({'score': 1, 'better': 'এই মাসে বিক্রি ৯৯,৯৯৯ টাকা।'})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    monkeypatch.setattr(self_play, 'TRANSPORT', httpx.MockTransport(fake))
    monkeypatch.setattr(teachers, 'TRANSPORT', httpx.MockTransport(fake))
    pool = teachers.Teachers(usage_file=tmp_path / 'usage.json')
    rows, report = self_play.run('http://student.local/v1', 'hisab-chat', '', examples=4, keep_below=4, workers=2,
                                 teachers=pool)
    assert {'api.groq.com', 'generativelanguage.googleapis.com'} <= set(hosts)  # the work is spread over teachers
    assert sum(report['api_calls_today'].values()) == len([h for h in hosts if h != 'student.local'])
    assert report['graded'] == 4 and report['mean_score'] == 1.5 and report['share_good'] == 0
    assert report['new_training_rows'] == 2 and report['rejected_by_checker'] == 2
    assert all(r['source'] == 'synthetic-selfplay' and 'hate' in r['messages'][-1]['content'] for r in rows)


def test_daily_budget_keeps_a_reserve_and_hands_over_to_the_gpu_teacher(monkeypatch, tmp_path):
    monkeypatch.setenv('CAPZORYX_TEACHER_DAILY_GROQ', '10')
    monkeypatch.setenv('CAPZORYX_TEACHER_DAILY_GEMINI', '0')
    used = []

    def fake(request):
        used.append(request.url.host)
        return httpx.Response(200, json={'choices': [{'message': {'content': '<think>x</think>{"ok": 1}'}}]})
    monkeypatch.setattr(teachers, 'TRANSPORT', httpx.MockTransport(fake))
    pool = teachers.Teachers('http://gpu.local/v1', usage_file=tmp_path / 'usage.json')
    far = 10**12
    for _ in range(10):
        assert pool.ask_json('grade', far, prefer='remote') == {'ok': 1}
    # 30% of Groq's 10 daily calls stay for the live bot, then the GPU teacher takes over.
    assert used.count('api.groq.com') == 7 and used.count('gpu.local') == 3
    assert teachers.Teachers(usage_file=tmp_path / 'usage.json').usage['groq'] == 7  # remembered across runs
    used.clear()
    pool.ask_json('write questions', far, prefer='local')
    assert used == ['gpu.local']


def test_training_labels_only_assistant_replies_for_any_chat_template():
    from ml.hisab_chat.train import encode

    class CharTokenizer:  # one token per character, like offsets from a fast tokenizer
        def apply_chat_template(self, messages, tokenize=False):
            return ''.join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)

        def __call__(self, text, add_special_tokens=False, return_offsets_mapping=True):
            return {'input_ids': [ord(c) for c in text], 'offset_mapping': [(i, i + 1) for i in range(len(text))]}

    messages = [{'role': 'system', 'content': 'facts'}, {'role': 'user', 'content': 'cash koto?'},
                {'role': 'assistant', 'content': 'Ekhon ৳500.'}, {'role': 'user', 'content': 'ar?'},
                {'role': 'assistant', 'content': 'Bas.'}]
    row = encode(CharTokenizer(), messages)
    learned = ''.join(chr(t) for t in row['labels'] if t != -100)
    assert learned == 'Ekhon ৳500.<|im_end|>Bas.<|im_end|>'


def test_retired_model_names_are_replaced_from_the_live_model_list(monkeypatch):
    calls = []

    def fake(request):
        calls.append((request.method, request.url.path))
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'openai/gpt-oss-120b'}, {'id': 'llama-3.1-8b-instant'}]})
        body = json.loads(request.content)
        if body['model'] == 'llama-3.3-70b-versatile':
            return httpx.Response(404, json={'error': 'model_decommissioned'})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Bhalo achi!'}}]})
    monkeypatch.setattr(llm, 'TRANSPORT', httpx.MockTransport(fake))
    monkeypatch.setattr(llm, '_models', {})
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', '')
    assert llm.reply('kemon acho') == ('Bhalo achi!', 'groq')
    assert next(p for p in llm.PROVIDERS if p.name == 'groq').model_name == 'openai/gpt-oss-120b' and ('GET', '/openai/v1/models') in calls
