"""Bill photos: read by a free vision model only after the owner agrees for that photo, then checked like any draft."""
import json

import httpx

from api.app import assist
from api.app.chat import llm
from api.tests.test_accounts import signup, world  # noqa: F401
from api.tests.test_fx_assist import PNG


def test_free_vision_reads_a_bill_only_with_consent(world, monkeypatch):  # noqa: F811
    client, _ = world
    signup(client, 'bill_photo_owner')
    assert client.post('/api/account/profile', json={'smart_assist': True}).status_code == 200
    monkeypatch.setenv('SAFELEDGER_LLM_PROFILE', 'gemini_free')
    monkeypatch.setenv('SAFELEDGER_LLM_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta/openai')
    monkeypatch.setenv('SAFELEDGER_LLM_ALLOWED_HOSTS', 'generativelanguage.googleapis.com')
    monkeypatch.setenv('SAFELEDGER_LLM_API_KEY', 'gm_test')
    monkeypatch.setenv('SAFELEDGER_LLM_FREE_TIER_CONFIRMED', '1')
    monkeypatch.setenv('SAFELEDGER_LLM_MODEL', 'gemini-2.5-flash-lite')
    monkeypatch.setenv('CAPZORYX_CHAT_GROQ_KEY', 'gsk_test')
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(body['model'])
        assert body['messages'][1]['content'][1]['image_url']['url'] == PNG  # the photo itself, nothing from the ledger
        reading = {'kind': 'invoice', 'vendor': 'Dhaka Packaging', 'invoice_number': 'DP-118', 'date': '2026-11-20',
                   'due_date': '2026-11-30', 'total': '12,500', 'currency': 'BDT',
                   'line_items': [{'name': 'Boxes', 'amount': '10000'}, {'name': 'Tape', 'amount': '2500'}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(reading)}}]})
    monkeypatch.setattr(assist, 'VISION_TRANSPORT', httpx.MockTransport(handle))
    llm._cooldown.clear()
    refused = client.post('/api/assist/scan', json={'image': PNG})
    assert refused.status_code >= 400 and sent == []  # no consent: refused as before, the photo goes nowhere
    read = client.post('/api/assist/scan', json={'image': PNG, 'free_vision_consent': True})
    assert read.status_code == 200, read.text
    draft = read.json()['draft']
    assert draft['ok'] and draft['amount_minor'] == 1_250_000 and draft['kind'] == 'invoice'
    assert sent[0] == 'meta-llama/llama-4-scout-17b-16e-instruct'
