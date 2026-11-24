import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from api.app import cloud, hisab_hosted
from api.tests.test_accounts import signup, world


def test_source_requires_one_unambiguous_settled_amount():
    today = date(2026, 11, 22)
    assert hisab_hosted.facts("On 2026-11-21 I paid BDT 1,250.50 to Rahman.", today) == (
        Decimal("1250.50"), "outflow", date(2026, 11, 21)
    )
    assert hisab_hosted.facts("গতকাল রাহমান থেকে ৫০০ টাকা পেলাম।", today) == (
        Decimal("500"), "inflow", date(2026, 11, 21)
    )
    assert hisab_hosted.facts("i paid rahman 500", today) == (Decimal("500"), "outflow", today)
    assert hisab_hosted.facts("Mitu ke 1.5k disi kal", today) == (
        Decimal("1500"), "outflow", date(2026, 11, 21)
    )
    assert hisab_hosted.facts("Rahman paid me 1,25,000", today) == (Decimal("125000"), "inflow", today)
    assert hisab_hosted.facts("Got paid Tk 350.25 by Oak Shop.", today) == (Decimal("350.25"), "inflow", today)
    assert hisab_hosted.facts("ami 500 tk niyechi rahim theke", today) == (Decimal("500"), "inflow", today)
    for text in ("I paid 500 BDT and received 50 BDT", "I paid 5,00 BDT", "I paid 1e1 BDT",
                 "I didn't pay BDT 500", "I paid BDT -500", "I paid USD 12, equivalent to BDT 1500",
                 "I paid 50 lakh 2 hajar taka", "On 2026-12-01 I paid BDT 100", "Did I pay Rahman 500?",
                 "I paid 500.001", "I paid 500 CHF", "I paid −500", "I paid 500₴", "If I paid 500",
                 "ami 500 tk niyechi na rahim theke", "ami 500 tk niyechi"):
        assert hisab_hosted.facts(text, today) is None


def test_private_transport_preserves_data_boundary(monkeypatch):
    monkeypatch.setenv("CAPZORYX_HISAB_HF_TOKEN", "synthetic-read-token")
    calls = []
    real_client = httpx.Client
    event = "f" * 32
    prediction = {"kind": "cash", "direction": "outflow", "currency": "BDT", "amount": "1250"}

    def fake(request):
        assert request.headers["authorization"] == "Bearer synthetic-read-token"
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            assert json.loads(request.content) == {"data": ["I paid BDT 1250."]}
            return httpx.Response(200, json={"event_id": event})
        return httpx.Response(200, text="event: heartbeat\n\nevent: complete\ndata: " + json.dumps([prediction]) + "\n\n")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(fake), **kwargs))
    assert hisab_hosted.hosted_extract("I paid BDT 1250.") == prediction
    assert calls == [("POST", hisab_hosted.SPACE), ("GET", f"{hisab_hosted.SPACE}/{event}")]


def test_challenge_amount_and_direction_remain_available_without_any_model():
    fixture = Path(__file__).resolve().parents[2] / 'evals/fixtures/hisab_cash_challenge.jsonl'
    for line in fixture.read_bytes().splitlines():
        case = json.loads(line)
        source = hisab_hosted.facts(case['text'], date(2026, 11, 23))
        if 'amount' not in case:
            assert source is None, case['id']
            continue
        assert source[:2] == (Decimal(case['amount']), case['direction']), case['id']
        if case.get('date'):
            assert source[2].isoformat() == case['date'], case['id']


def test_hosted_draft_needs_consent_and_never_posts_a_record(world, monkeypatch):
    client, _ = world
    path = "/api/assist/hisab"
    sentence = "i paid rahman 500"
    assert client.post(path, json={"text": sentence, "consent": True}).status_code == 401
    signup(client, "hisab_owner")
    monkeypatch.setenv("CAPZORYX_HISAB_HF_TOKEN", "synthetic-read-token")
    baseline = client.get("/api/workspace").json()["dataset_revision"]
    prediction = {"kind": "cash", "direction": "outflow", "amount": "500", "currency": "BDT", "vendor": "Rahman"}
    monkeypatch.setattr(hisab_hosted, "hosted_extract", lambda _: prediction)
    assert client.post(path, json={"text": sentence, "consent": False}).status_code == 403
    reply = client.post(path, json={"text": sentence, "consent": True})
    assert reply.status_code == 200
    assert reply.json()["draft"]["amount_minor"] == 50000
    assert reply.json()["draft"]["vendor_name"] == "rahman"
    assert reply.json()["draft"]["memo"] == sentence
    assert reply.json()["source"] == "hisab_lm"
    prediction.update(direction="inflow", amount="500", vendor="Rahim")
    receipt = client.post(path, json={"text": "ami 500 tk niyechi rahim theke", "consent": True}).json()
    assert receipt["source"] == "hisab_lm"
    assert receipt["draft"]["amount_minor"] == 50000
    assert receipt["draft"]["direction"] == "inflow"
    assert receipt["draft"]["vendor_name"] == "rahim"
    prediction.update(direction="outflow", amount="1200")
    reply = client.post(path, json={"text": sentence, "consent": True}).json()
    assert reply["source"] == "verified" and reply["draft"]["amount_minor"] == 50000
    assert client.get("/api/workspace").json()["dataset_revision"] == baseline


def test_hosted_failure_uses_only_consented_approved_api_and_revalidates_reply(world, monkeypatch):
    client, _ = world
    signup(client, 'hisab_backup')
    monkeypatch.delenv('CAPZORYX_HISAB_HF_TOKEN', raising=False)
    monkeypatch.setenv('SAFELEDGER_LLM_API_KEY', 'synthetic-key')
    monkeypatch.setenv('SAFELEDGER_LLM_FREE_TIER_CONFIRMED', '1')
    monkeypatch.setenv('SAFELEDGER_LLM_PROFILE', 'gemini_free')
    requests = []
    prediction = {"kind": "cash", "direction": "inflow", "amount": "500", "currency": "BDT", "vendor": "Karim"}

    def reply(request):
        requests.append(json.loads(request.content)['messages'])
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(prediction)}}]})

    monkeypatch.setattr(cloud, 'TRANSPORT', httpx.MockTransport(reply))
    payload = {'text': 'I received 500 from Karim', 'consent': True, 'allow_api_fallback': True}
    path = '/api/assist/hisab'
    assert client.post(path, json=payload).json()['source'] == 'verified'
    assert client.post('/api/account/profile', json={'smart_assist': True}).status_code == 200
    assert client.get('/api/auth/capabilities').json()['record_api_fallback'] is False
    assert client.post(path, json=payload).json()['source'] == 'verified'  # free Gemini cannot read records
    assert not requests
    monkeypatch.setenv('SAFELEDGER_LLM_PROFILE', 'compatible')
    monkeypatch.setenv('SAFELEDGER_LLM_BASE_URL', 'https://api.openai.com/v1')
    monkeypatch.setenv('SAFELEDGER_LLM_MODEL', 'synthetic-record-model')
    monkeypatch.setenv('SAFELEDGER_LLM_ALLOWED_HOSTS', 'api.openai.com')
    monkeypatch.setenv('SAFELEDGER_RECORD_PROVIDER_NO_TRAINING', '1')
    assert client.get('/api/auth/capabilities').json()['record_api_fallback'] is True
    assert client.post(path, json={**payload, 'allow_api_fallback': False}).json()['source'] == 'verified'
    assert client.post(path, json={**payload, 'text': 'I received USD 500'}).status_code == 422
    assert not requests
    assert client.post(path, json=payload).json()['source'] == 'api_backup'
    assert requests[0][1:] == [{'role': 'user', 'content': payload['text']}]
    prediction['amount'] = '900'
    result = client.post(path, json=payload).json()
    assert len(requests) == 2 and result['source'] == 'verified' and result['draft']['amount_minor'] == 50000
