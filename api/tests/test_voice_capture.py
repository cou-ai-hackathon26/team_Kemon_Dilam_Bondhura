"""A voice draft requires separate upload consent, approved routing and a matching measured release gate."""
import base64
import io
import json
import sqlite3
import wave

import httpx
import pytest

from api.app import voice
from api.app.agents.errors import ProviderError
from api.tests.test_accounts import signup, world


def fixture(frames=1600):
    data=io.BytesIO()
    with wave.open(data,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*frames)
    return base64.b64encode(data.getvalue()).decode()


def config(monkeypatch):
    base='http://127.0.0.1:8312/v1';model='synthetic-test-asr'
    monkeypatch.setenv('SAFELEDGER_LOCAL_ASR_URL',base);monkeypatch.setenv('SAFELEDGER_ASR_MODEL',model)
    report=dict(suite='voice',source='independent_speakers',provider_base=base,model=model,clips=30,distinct_speakers=3,
                amount_accuracy=.9,kind_accuracy=.95,party_accuracy=.85,p95_seconds=5.5)
    return report


def test_consent_scope_retention_and_releases(world,monkeypatch):
    c,path=world;signup(c,'voice_owner');report=config(monkeypatch)
    body=dict(consent=True,audio_b64=fixture(),language='bn',expected_host='127.0.0.1',expected_model=report['model'])
    sent=[]
    def transport(request):
        assert request.url.path=='/v1/audio/transcriptions'
        assert b'WAVEfmt ' in request.content and b'name="language"' in request.content
        sent.append(True)
        return httpx.Response(200,json={'text':'Synthetic ledger transcript'})
    monkeypatch.setattr(voice,'TRANSPORT',httpx.MockTransport(transport))
    assert c.post('/api/assist/voice',json=body).status_code==403
    assert c.post('/api/account/profile',json={'smart_assist':True}).status_code==200
    assert c.post('/api/assist/voice',json=dict(body,consent=False)).status_code==403
    assert c.post('/api/assist/voice',json=body).json()['error']['code']=='VOICE_QUALITY_GATE_PENDING'
    assert c.get('/api/account/profile').json()['speech_provider'] is None
    monkeypatch.setenv('SAFELEDGER_ASR_EVAL_JSON',json.dumps(report))
    assert c.get('/api/account/profile').json()['speech_provider']['model']==report['model']
    assert c.post('/api/assist/voice',json=dict(body,expected_host='changed.example')).status_code==409
    assert c.post('/api/assist/voice',json=dict(body,audio_b64='not-a-wave')).status_code==422
    assert not sent
    r=c.post('/api/assist/voice',json=body)
    assert r.status_code==200,r.text
    assert r.json()['transcript']=='Synthetic ledger transcript' and len(sent)==1
    assert c.get('/api/workspace').json()['snapshot']['income_minor']==0
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT task,data_class FROM model_calls").fetchone()==('voice','record_data')
        assert 'Synthetic ledger transcript' not in '\n'.join(db.iterdump())


def test_wav_duration_and_quality_bounds(world,monkeypatch):
    c,_=world
    assert len(voice.waveform(fixture(480000)))==960044
    from api.app.security import PolicyError
    with pytest.raises(PolicyError):voice.waveform(fixture(480001))
    assert c.post('/api/assist/voice',content=' '*((2<<20)+1),headers={'Content-Type':'application/json'}).status_code==413
    report=config(monkeypatch)
    for change in [dict(source='machine_voice'),dict(p95_seconds=6),dict(amount_accuracy=1.5),dict(kind_accuracy=True),dict(party_accuracy=float('inf')),dict(distinct_speakers=1)]:
        monkeypatch.setenv('SAFELEDGER_ASR_EVAL_JSON',json.dumps(dict(report,**change)))
        assert not voice.quality_ok(report['provider_base'],report['model'])
    monkeypatch.setenv('SAFELEDGER_LOCAL_ASR_URL','https://external.example/v1')
    with pytest.raises(ProviderError):voice.speech_provider()
