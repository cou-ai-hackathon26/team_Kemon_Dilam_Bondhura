"""Challenge 1: a whole week of spending in one voice note, every entry its own checked draft."""
import base64
import json
from datetime import date

import httpx
import pytest

from api.app import voice_batch as vb
from api.app.chat import llm
from api.tests.test_accounts import signup, world  # noqa: F401

TODAY = date(2026, 11, 24)  # a Tuesday


@pytest.mark.parametrize('text,amount', [
    ('rent 5000 dilam', 5000), ('৫০০০ টাকা ভাড়া', 5000), ('dui hajar pachsho taka', 2500), ('দুই হাজার পাঁচশো টাকা', 2500),
    ('দেড় হাজার', 1500), ('arai hajar', 2500), ('sare tin hajar', 3500), ('2.5k', 2500), ('1,20,000 taka', 120000),
    ('5 hajar 200 taka', 5200), ('two thousand five hundred', 2500), ('pachsho', 500), ('ek lakh', 100000),
])
def test_amounts_said_in_bangla_banglish_and_english(text, amount):
    assert amount in vb.spoken_amounts(text)


@pytest.mark.parametrize('text,expected', [
    ('aj 200 taka', TODAY), ('gotokal 300', date(2026, 11, 23)), ('পরশু', date(2026, 11, 22)), ('3 din age', date(2026, 11, 21)),
    ('tin din age', date(2026, 11, 21)), ('shombar rent', date(2026, 11, 23)), ('শুক্রবার', date(2026, 11, 20)),
    ('on Sunday', date(2026, 11, 22)), ('tuesday', TODAY), ('kono din na', None),
])
def test_days_said_aloud(text, expected):
    assert vb.said_day(text, TODAY) == expected


WEEK = ('Shombar dokan bhara 5000 taka dilam. Tarpor transport e 300. Budhbar Rahim theke 12 hajar taka pelam, '
        'shukrobar biddut bill 1,850 taka. Aj chaa nasta 250.')


def test_rules_split_a_week_without_any_ai():
    drafts, dropped, method = vb.split_note(WEEK, TODAY, 'BDT', 2, use_ai=False)
    assert method == 'rules'
    got = [(d['amount_minor'], d['effective_on'], d.get('direction')) for d in drafts]
    assert got == [(500000, '2026-11-23', 'outflow'), (30000, '2026-11-23', 'outflow'),  # no day said: the day before it
                   (1200000, '2026-11-18', 'inflow'), (185000, '2026-11-20', 'outflow'), (25000, '2026-11-24', 'outflow')]


def _ai(monkeypatch, entries, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'entries': entries})}}]})
    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_MODEL', 'llama-3.3-70b-versatile')
    for name in ('GEMINI', 'CEREBRAS', 'OPENROUTER', 'MISTRAL', 'GITHUB', 'SAMBANOVA'):
        monkeypatch.delenv(f'CAPZORYX_CHAT_{name}_KEY', raising=False)
    monkeypatch.delenv('SAFELEDGER_LLM_API_KEY', raising=False)
    llm._cooldown.clear()


def test_ai_split_is_checked_against_what_was_said(monkeypatch):
    note = 'Shombar bhara 5000 r transport 300 dilam. Budhbar mal kinlam dui hajar pachsho taka, na na tin hajar.'
    _ai(monkeypatch, [
        {'evidence': 'Shombar bhara 5000', 'amount': 5000, 'type': 'expense', 'when': 'Shombar', 'category': 'rent'},
        {'evidence': 'transport 300 dilam', 'amount': 300, 'type': 'expense', 'category': 'transport'},
        {'evidence': 'Budhbar mal kinlam dui hajar pachsho taka, na na tin hajar', 'amount': 3000, 'type': 'expense', 'when': 'Budhbar'},
        {'evidence': 'Shombar bhara 5000', 'amount': 9000, 'type': 'expense'},          # an amount nobody said
        {'evidence': 'Paid the landlord 7000 on Friday', 'amount': 7000, 'type': 'expense'},  # not in the note
    ])
    drafts, dropped, method = vb.split_note(note, TODAY, 'BDT', 2)
    assert method == 'ai'
    assert [(d['amount_minor'], d['effective_on']) for d in drafts] == [(500000, '2026-11-23'), (30000, '2026-11-23'), (300000, '2026-11-18')]
    assert {d['reason'] for d in dropped} == {'AMOUNT_NOT_SAID', 'NOT_IN_NOTE'}
    assert any('Two numbers' in w for w in drafts[2]['warnings'])  # the correction is flagged for a second look


def test_ai_misses_an_entry_and_the_rule_reader_adds_it_for_checking(monkeypatch):
    _ai(monkeypatch, [{'evidence': 'Shombar dokan bhara 5000 taka dilam', 'amount': 5000, 'type': 'expense', 'when': 'Shombar'}])
    drafts, _, _ = vb.split_note(WEEK, TODAY, 'BDT', 2)
    assert len(drafts) == 5 and drafts[0]['confidence'] == 0.9
    assert all('backup reader' in ' '.join(d['warnings']) for d in drafts[1:])


def test_no_ai_answer_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(lambda r: httpx.Response(503)))
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    llm._cooldown.clear()
    drafts, _, method = vb.split_note(WEEK, TODAY, 'BDT', 2)
    assert method == 'rules' and len(drafts) == 5


def test_speech_uses_whisper_then_gemini(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.host + request.url.path)
        if 'groq' in request.url.host:
            return httpx.Response(429)
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'shombar bhara 5000 taka'}]}}]})
    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'gm_test')
    text, model = vb.transcribe(b'x' * 2000, 'audio/webm;codecs=opus', 'bn', 'taka')
    assert text == 'shombar bhara 5000 taka' and model.startswith('gemini')
    assert calls[:2] == ['api.groq.com/openai/v1/audio/transcriptions'] * 2  # large-v3, then turbo


def test_uncertain_bangla_audio_uses_a_second_provider_and_preserves_audio_format(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.host)
        if request.url.host == 'api.groq.com':
            assert b'note.wav' in request.content
            assert b'verbose_json' in request.content
            return httpx.Response(200, json={'text': 'ভুল অংশ', 'segments': [
                {'text': 'ভুল অংশ', 'avg_logprob': -1.8},
            ]})
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [
            {'text': 'রফিককে দুশো টাকা দিলাম।'},
        ]}}]})

    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'gm_test')
    assert vb.transcribe(b'x' * 2000, 'audio/wav', 'bn', 'বাংলা')[0] == 'রফিককে দুশো টাকা দিলাম।'
    assert calls == ['api.groq.com', 'generativelanguage.googleapis.com']
    calls.clear()
    assert vb.transcribe(b'x' * 2000, 'audio/wav', 'bn', 'বাংলা', alternate=True)[0] == 'রফিককে দুশো টাকা দিলাম।'
    assert calls == ['generativelanguage.googleapis.com']


@pytest.mark.parametrize('variant,expected', [
    ('আজ ভাড়া পাঁচশো টাকা দিলাম।', 'agree'),
    ('আজ ভাড়া ছয়শো টাকা দিলাম।', 'review'),
    ('আজ ভাড়ার কথা বললাম।', 'review'),
])
def test_voice_assistant_compares_grounded_drafts_across_transcribers(monkeypatch, variant, expected):
    def recognize(audio, mime, language, hint, deadline, models):
        text = 'আজ ভাড়া ৳৫০০ দিলাম।' if models == vb.WHISPER_MODELS[:1] else variant
        return text, models[0], False

    monkeypatch.setattr(vb, 'whisper', recognize)
    monkeypatch.setattr(vb, '_gemini_speech_key', lambda: '')
    text, model, alternatives, agreement = vb.compare_transcriptions(
        b'test audio', 'audio/wav', 'bn', 'বাংলা', 'BDT', TODAY
    )
    assert text == 'আজ ভাড়া ৳৫০০ দিলাম।'
    assert model == vb.WHISPER_MODELS[0]
    assert alternatives == [{'transcript': variant, 'model': vb.WHISPER_MODELS[1]}]
    assert agreement == expected


def test_dedicated_speech_capability_does_not_require_cloud_chat(world, monkeypatch):  # noqa: F811
    c, _ = world
    monkeypatch.setattr('api.app.gateway.disclosure', lambda: [])
    monkeypatch.setattr(vb, 'speech_available', lambda: True)
    capabilities = c.get('/api/auth/capabilities').json()
    assert capabilities['voice_note_speech'] is True
    assert capabilities['cloud_assist'] is False


def test_speech_fallback_stops_starting_requests_after_deadline(monkeypatch):
    calls = []
    clock = [0]

    def handle(request):
        calls.append(request.url.host)
        clock[0] = vb.TRANSCRIPTION_BUDGET_SECONDS
        return httpx.Response(503)

    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setattr(vb.time, 'monotonic', lambda: clock[0])
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    monkeypatch.setenv('CAPZORYX_CHAT_GEMINI_KEY', 'gm_test')
    monkeypatch.setenv('CAPZORYX_HISAB_HF_TOKEN', 'hf_test')
    assert vb.transcribe(b'x' * 2000, 'audio/webm', 'bn', 'taka') == (None, None)
    assert calls == ['api.groq.com']


def test_voice_gemini_uses_a_generic_gateway_key_only_for_the_gemini_host(monkeypatch):
    monkeypatch.delenv('CAPZORYX_CHAT_GEMINI_KEY', raising=False)
    monkeypatch.setenv('SAFELEDGER_LLM_API_KEY', 'test-gateway-key')
    monkeypatch.setenv('SAFELEDGER_LLM_BASE_URL', 'https://other.example.invalid/v1')
    assert not vb._gemini_speech_key()
    monkeypatch.setenv('SAFELEDGER_LLM_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
    assert vb._gemini_speech_key() == 'test-gateway-key'


def test_voice_note_routes_end_to_end(world, monkeypatch):  # noqa: F811
    c, _ = world
    signup(c, 'voice_owner')
    c.post('/api/account/profile', json={'smart_assist': True}).raise_for_status()

    def handle(request):
        if request.url.path.endswith('/audio/transcriptions'):
            assert b'whisper-large-v3' in request.content
            return httpx.Response(200, json={'text': WEEK})
        return httpx.Response(503)
    monkeypatch.setattr(vb, 'TRANSPORT', httpx.MockTransport(handle))
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    llm._cooldown.clear()
    assert c.get('/api/assist/voice-note').json()['speech'] is True
    audio = base64.b64encode(b'\x1aE\xdf\xa3' + b'0' * 3000).decode()
    refused = c.post('/api/assist/voice-note', json={'audio_b64': audio, 'mime': 'audio/webm;codecs=opus', 'language': 'bn', 'consent': False})
    assert refused.status_code == 403
    heard = c.post('/api/assist/voice-note', json={'audio_b64': audio, 'mime': 'audio/webm;codecs=opus', 'language': 'bn', 'consent': True})
    assert heard.status_code == 200 and heard.json()['transcript'] == WEEK
    split = c.post('/api/assist/batch-drafts', json={'transcript': WEEK, 'consent': True}).json()
    assert len(split['drafts']) == 5
    revision = c.get('/api/workspace').json()['dataset_revision']
    import uuid
    records = [dict(request_id=str(uuid.uuid4()), kind='cash', amount_minor=d['amount_minor'], effective_on=d['effective_on'],
                    direction=d['direction'], status='cleared', memo=d['memo']) for d in split['drafts']]
    saved = c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': records})
    assert saved.status_code == 201, saved.text
    assert saved.json()["saved"] == 5
    snap = c.get('/api/workspace').json()['snapshot']
    assert snap['income_minor'] == 1200000 and snap['expenses_minor'] == 500000 + 30000 + 185000 + 25000
    # The same batch again is a replay, not five more records; a stale revision is refused as a whole.
    again = c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': records})
    assert again.status_code == 201 and c.get('/api/workspace').json()['snapshot']['income_minor'] == 1200000
    fresh = [dict(r, request_id=str(uuid.uuid4())) for r in records]
    stale = c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': fresh})
    assert stale.status_code == 409 and c.get('/api/workspace').json()['snapshot']['income_minor'] == 1200000


# ---------- audit acceptance cases ----------
@pytest.mark.parametrize('note', ['Rahim 5000 taka dey nai', 'রহিম ৫০০০ টাকা দেয়নি', 'kal bhara 5000 dibo',
                                  'I did not pay the 3000 rent', 'koto dibo 500?'])
def test_not_paid_planned_or_asked_never_becomes_a_draft(note, monkeypatch):
    drafts, _, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert drafts == []
    _ai(monkeypatch, [{'evidence': note, 'amount': next(iter(vb.spoken_amounts(note))), 'type': 'expense'}])
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2)
    assert drafts == [] and dropped[0]['reason'] in ('NOT_PAID', 'PLANNED', 'QUESTION')


def test_ai_cannot_borrow_another_sentences_amount_day_or_type(monkeypatch):
    note = 'Shombar bhara 5000 dilam. Rahim theke 800 pelam.'
    _ai(monkeypatch, [{'evidence': 'Shombar bhara 5000 dilam', 'amount': 5000, 'type': 'expense'},
                      {'evidence': 'Rahim theke 800 pelam', 'amount': 5000, 'type': 'income', 'when': 'Shombar'},   # borrowed amount
                      {'evidence': 'Rahim theke', 'amount': 800, 'type': 'expense'}])                                 # no amount in quote
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2)
    amounts = [(d['amount_minor'], d['entry_type']) for d in drafts]
    assert (500000, 'expense') in amounts and all(a != (500000, 'income') for a in amounts)
    assert (80000, 'income') in amounts  # recovered by the rule reader, type from its own words
    assert {d['reason'] for d in dropped} == {'AMOUNT_NOT_SAID'}


def test_a_chosen_week_keeps_monday_to_sunday_in_order():
    note = 'Shombar bhara 100 dilam. Mongolbar 200 dilam. Budhbar 300 dilam. Brihospotibar 400 dilam. Shukrobar 500 dilam. Shonibar 600 dilam. Robibar 700 dilam.'
    drafts, _, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False, week_start=date(2026, 11, 16))
    assert [d['effective_on'] for d in drafts] == [f'2026-11-{d}' for d in range(16, 23)]
    loose, _, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)  # no week chosen: warn when days go backwards
    assert any('go backwards' in ' '.join(d['warnings']) for d in loose)


def test_two_equal_amounts_are_two_entries(monkeypatch):
    note = 'Shombar transport 500 dilam. Budhbar nasta 500 dilam.'
    _ai(monkeypatch, [{'evidence': 'Shombar transport 500 dilam', 'amount': 500, 'type': 'expense'}])
    drafts, _, _ = vb.split_note(note, TODAY, 'BDT', 2)
    assert [(d['amount_minor'], d['effective_on']) for d in drafts] == [(50000, '2026-11-23'), (50000, '2026-11-18')]


def test_rule_fallback_uses_corrected_amount_and_preserves_relative_dates():
    note = '3 din age transport 500 dilam. Yesterday rent 5000 taka dilam, na na 4500 taka.'
    drafts, dropped, method = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert [(d['amount_minor'], d['effective_on']) for d in drafts] == [(50000, '2026-11-21'), (450000, '2026-11-23')]
    assert drafts[1]['needs_review'] and not dropped and method == 'rules'


def test_model_quote_keeps_negation_and_rejects_foreign_currency(monkeypatch):
    note = 'I did not pay the 3000 rent. Aj software 10 USD dilam.'
    _ai(monkeypatch, [{'evidence': 'pay the 3000 rent', 'amount': 3000, 'type': 'expense'},
                      {'evidence': 'software 10', 'amount': 10, 'type': 'expense'}])
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2)
    assert not drafts
    assert {d['reason'] for d in dropped} == {'NOT_PAID', 'CURRENCY_MISMATCH'}


def test_rule_fallback_keeps_conjoined_and_repeated_payments():
    note = 'Aj dokan bhara 5000 taka dilam ar transport 300 taka dilam. Transport 300 taka dilam.'
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert not dropped
    assert [d['amount_minor'] for d in drafts] == [500000, 30000, 30000]


def test_synthetic_voice_bills_have_unique_numbers_even_in_separate_batches():
    def bill(amount):
        note = f'Aj stationery {amount} taka baki'
        return vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)[0][0]

    assert bill(600)['invoice_number'] != bill(650)['invoice_number']


def test_long_bangla_story_keeps_five_movements_without_registering_the_requested_loan_or_balance():
    note = (
        'আমি করিম থেকে পাঁচশো টাকা নিয়েছিলাম এরপর বাজারে যাওয়ার পরে আমার রফিকের সঙ্গে দেখা হয় '
        'ওকে আমি দুশো টাকা দিলাম ও তো আমার কাছে তিনশো টাকা ধার চেয়েছিল কিন্তু আমি দিতে পেরেছি ওকে দুইশো টাকা '
        'এরপর আমার কাছে যে তিনশো টাকা আছে ওইটা নিয়ে মাঠে যেখানে একশো টাকা দিয়ে ক্রিকেট খেললাম। '
        'তো ৳30 দিয়ে যখন আমি স্ন্যাক্স পেলাম তারপর আমি বাকিটাকে দিয়ে ভাড়া খরচ করে ৳৫০ ভাড়া খরচ করলাম। এরপর বাসায় গেলাম।'
    )
    drafts, dropped, method = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert method == 'rules'
    assert [(d['entry_type'], d['amount_minor']) for d in drafts] == [
        ('income', 50000), ('expense', 20000), ('expense', 10000), ('expense', 3000), ('expense', 5000),
    ]
    assert {d['reason'] for d in dropped} == {'REQUESTED_ONLY', 'BALANCE_ONLY'}
    assert sum(d['amount_minor'] for d in drafts if d['entry_type'] == 'expense') == 38000


def test_speech_draft_holds_unreadable_partial_amounts_for_manual_review():
    note = 'করিম থেকে একসো কুডি তাকা পেলাম। আশাকে দুশো তলিষ তাকা দিলাম। হাসান থেকে চারসো আসিতা কা পেলাম।'
    drafts, _, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False, source='speech')
    assert [draft['amount_minor'] for draft in drafts] == [12000, 0, 0]
    assert all(draft['needs_review'] for draft in drafts)
    assert 'Part of the amount is unclear. Enter the amount you heard.' in drafts[1]['warnings']


@pytest.mark.parametrize('note', ['রহিম ৫০০০ টাকা দেয় নাই', 'ওকে ২০০ টাকা দিতে পারিনি'])
def test_bangla_spaced_negations_remain_out_of_the_drafts(note):
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert not drafts and dropped[0]['reason'] == 'NOT_PAID'


def test_public_competition_demo_runs_engines_without_an_account(world):
    c, _ = world
    assert c.get('/api/session').status_code == 401
    response = c.get('/api/competition/demo')
    assert response.status_code == 200
    data = response.json()
    assert data['voice']['method'] == 'rules'
    assert [d['amount_minor'] for d in data['voice']['drafts']] == [500000, 30000, 1200000, 185000]
    assert [d['reason'] for d in data['voice']['dropped']] == ['NOT_PAID']
    assert data['radar']['habit']['level'] == 'alert'
    assert data['radar']['habit']['receivables'][0]['habit_days'] == 8
    assert data['radar']['habit']['outlooks']['on_time']['first_shortfall_on'] is None
    assert data['radar']['overdue']['level'] == 'watch'
    week = data['weekly_voice']['drafts']
    assert [d['category'] for d in week] == ['transport', 'stock', 'salary', 'utilities', 'delivery', 'rent', 'packaging']
    assert [d['amount_minor'] for d in week] == [120000, 500000, 350000, 210000, 90000, 800000, 125000]
    assert all(d['entry_type'] == 'expense' and not d['needs_review'] for d in week)
    assert len({d['effective_on'] for d in week}) == 7
    assert sum(d['amount_minor'] for d in week) == 2195000
    pasted = c.post('/api/competition/drafts', json={'transcript': 'আমি ৳৩০ দিয়ে নাস্তা কিনলাম।'})
    assert pasted.status_code == 200 and pasted.json()['drafts'][0]['amount_minor'] == 3000
    assert c.get('/api/session').status_code == 401


def test_too_many_entries_are_reported_not_silently_dropped():
    raw = [{'evidence': 'bhara 100 dilam', 'amount': 100, 'type': 'expense'}] * (vb.MAX_ENTRIES + 5)
    _, dropped = vb.checked_drafts(raw, 'bhara 100 dilam', TODAY, 'BDT', 2)
    assert dropped[0] == {'evidence': '', 'reason': 'TOO_MANY_ENTRIES', 'count': 5}


def test_batch_replay_with_changed_amounts_is_refused(world):  # noqa: F811
    import uuid
    c, _ = world
    signup(c, 'batch_replay')
    revision = c.get('/api/workspace').json()['dataset_revision']
    records = [dict(request_id=str(uuid.uuid4()), kind='cash', amount_minor=10000 * (i + 1), effective_on=str(TODAY - __import__('datetime').timedelta(days=0)),
                    direction='outflow', status='cleared', memo='') for i in range(2)]
    first = c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': records})
    assert first.status_code == 201
    same = c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': records})
    assert same.status_code == 201 and same.json()['replayed'] is True
    edited = [dict(records[0], amount_minor=99999), records[1]]
    assert c.post('/api/workspace/records/batch', json={'expected_revision': revision, 'records': edited}).status_code == 409


@pytest.mark.parametrize('note', ['amake sadia 500 tk dise and gotokal ami 600 tk paisi rahim theke',
                                  'আমাকে সাদিয়া ৫০০ টাকা দিসে আর গতকাল আমি ৬০০ টাকা পাইসি রহিম থেকে'])
def test_two_payments_in_one_sentence_need_no_full_stop(note):
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert dropped == []
    assert [(d['amount_minor'], d['effective_on'], d['entry_type']) for d in drafts] == [
        (50000, '2026-11-24', 'income'),   # "amake ... dise": someone gave money to the owner, today
        (60000, '2026-11-23', 'income')]   # "gotokal ... paisi": received yesterday


@pytest.mark.parametrize('note, reason', [
    ('500 taka dilam na', 'NOT_PAID'),              # "I did not give 500"
    ('500 টাকা দিলাম না', 'NOT_PAID'),
    ('rahim 300 taka dibe kal', 'PLANNED'),         # "Rahim will give 300 tomorrow"
    ('রহিম কাল ৩০০ টাকা দিবে', 'PLANNED'),
    ('বিকাশে ৫০০ টাকা পাবো', 'PLANNED')])
def test_not_paid_and_promised_money_never_become_drafts(note, reason):
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert drafts == [] and [d['reason'] for d in dropped] == [reason]


@pytest.mark.parametrize('note, entry_type, amount', [
    ('Karim 700 taka ferot dise', 'income', 70000),    # someone paid money back to the owner
    ('করিম ৭০০ টাকা ফেরত দিসে', 'income', 70000),
    ('ami ferot dilam 200 taka', 'expense', 20000),    # the owner paid money back
    ('500 na na 600 taka dilam', 'expense', 60000)])   # "na na" is a correction, not "did not"
def test_refunds_and_corrections_keep_the_right_direction(note, entry_type, amount):
    drafts, dropped, _ = vb.split_note(note, TODAY, 'BDT', 2, use_ai=False)
    assert dropped == [] and [(d['entry_type'], d['amount_minor']) for d in drafts] == [(entry_type, amount)]
