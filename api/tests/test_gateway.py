"""Gateway contract tests use local SQLite and explicitly synthetic HTTP replies."""
from contextlib import closing
import json
import sqlite3

import httpx
import pytest

from api.app import cloud, gateway
from api.app.agents.errors import ProviderError
from api.app.agents.provider import ChatProvider
from api.app.outbound import verified_draft
from api.tests.test_accounts import world, signup
from api.tests.test_free_provider import configure

MESSAGES = [{'role':'user', 'content':'Synthetic question'}]


@pytest.fixture
def gateway_env(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('SAFELEDGER_SIGNING_KEY','2'*64)
    gateway.reset()
    seen = []
    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'Synthetic answer'}}],
                                        'usage':{'prompt_tokens':4,'completion_tokens':2}})
    monkeypatch.setattr(cloud, 'TRANSPORT', httpx.MockTransport(respond))
    with closing(sqlite3.connect(':memory:')) as db:
        gateway.schema(db)
        yield db, seen
    gateway.reset()


def answer(db, business='a', **kwargs):
    return gateway.complete('explain',MESSAGES,db=db,business_id=business,data_class='question',consent=True,**kwargs)


def test_workspace_cache_budget_and_redacted_accounting(gateway_env, monkeypatch):
    db, seen = gateway_env
    answer(db)
    answer(db)
    answer(db,'b')
    assert len(seen) == 2
    assert db.execute('SELECT business_id,tokens FROM ai_token_budgets ORDER BY business_id').fetchall() == [('a',6),('b',6)]
    rows = db.execute('SELECT business_hash,trace_id,provider,task,data_class,prompt_tokens,completion_tokens,ok FROM model_calls').fetchall()
    assert len(rows)==2 and rows[0][0]!=rows[1][0]
    assert rows[0][2:]==('gemini_free','explain','question',4,2,1)
    assert 'Synthetic question' not in repr(rows)
    monkeypatch.setenv('SAFELEDGER_AI_DAILY_TOKENS','0')
    with pytest.raises(ProviderError,match='DAILY_BUDGET'):
        answer(db,'c')
    assert len(seen)==2


def test_policy_and_no_network_for_records_without_contract(gateway_env):
    db, seen = gateway_env
    for data_class,task,consent,code in [('question','extract_text',True,'DATA_CLASS'),('question','explain',False,'CONSENT'),
                                        ('record_data','extract_text',True,'PRIVATE_RECORD'),('record_data','message',True,'OUTBOUND_ENGINE')]:
        with pytest.raises(ProviderError,match=code):
            gateway.complete(task,MESSAGES,db=db,business_id='a',data_class=data_class,consent=consent)
    assert seen==[] and db.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0]==0


def test_local_endpoint_and_task_gate(gateway_env,monkeypatch):
    db,seen = gateway_env
    monkeypatch.setenv('SAFELEDGER_LOCAL_LLM_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setenv('SAFELEDGER_LOCAL_LLM_TASKS','explain')
    answer(db)
    assert seen==['127.0.0.1']
    provider=ChatProvider(profile='hisab_lm')
    assert provider.headers()=={} and provider.profile=='hisab_lm'
    with pytest.raises(ProviderError,match='PRIVATE_RECORD'):
        gateway.complete('extract_text',MESSAGES,db=db,business_id='a',data_class='record_data',consent=True)
    monkeypatch.setenv('SAFELEDGER_LOCAL_LLM_URL','http://192.0.2.1:11434/v1')
    with pytest.raises(ProviderError,match='CONFIGURATION_INVALID'):
        ChatProvider(profile='hisab_lm')


def test_fallback_then_circuit_recovery(gateway_env,monkeypatch):
    db,_ = gateway_env
    monkeypatch.setenv('SAFELEDGER_LOCAL_LLM_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setenv('SAFELEDGER_LOCAL_LLM_TASKS','explain')
    calls=[]
    def respond(request):
        calls.append(request.url.host)
        if request.url.host=='127.0.0.1':
            return httpx.Response(503)
        return httpx.Response(200,json={'choices':[{'message':{'content':'Fallback'}}]})
    monkeypatch.setattr(cloud,'TRANSPORT',httpx.MockTransport(respond))
    for i in range(4):
        assert answer(db,str(i)) == 'Fallback'
    assert calls.count('127.0.0.1')==3 and calls.count('generativelanguage.googleapis.com')==4
    assert db.execute('SELECT COUNT(*) FROM model_calls WHERE ok=0').fetchone()[0]==3
    monkeypatch.setattr(gateway.time,'monotonic',lambda:gateway._open_until['hisab_lm']+1)
    assert answer(db,'new')=='Fallback'
    assert calls.count('127.0.0.1')==4


def test_read_only_freezes_mutations_allows_logout(world, monkeypatch):
    client,_ = world
    signup(client,'read_only_owner')
    monkeypatch.setenv('SAFELEDGER_READ_ONLY','1')
    for route in ('/api/assist/ask','/api/account/profile','/api/auth/signup','/api/internal/tick'):
        r=client.post(route,json={})
        assert r.status_code==503 and r.json()['error']['code']=='READ_ONLY_MODE'
        assert r.headers['Cache-Control']=='no-store'
    assert client.get('/api/workspace').status_code==200
    assert client.post('/api/auth/logout',json={}).status_code==200


def test_approved_outbound_copy_uses_engine_values_only():
    trusted = ('Your remaining balance is BDT 70.00.', 'Remaining balance: BDT 70.00.')
    assert verified_draft(trusted[1],approved=trusted)==trusted[1]
    for candidate in ('Your remaining balance is BDT 700.00.',trusted[0]+' Visit another website.',trusted[0]+'\u200b'):
        with pytest.raises(ProviderError,match='UNVERIFIED_OUTBOUND_MESSAGE'):
            verified_draft(candidate,approved=trusted)
