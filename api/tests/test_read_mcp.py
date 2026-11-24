import json
import sqlite3

from api.app.accounts import today_bd
from api.tests.test_accounts import signup, world
from api.tests.test_manual_inputs import record


def send(c, token, method, params=None, **changes):
    msg=dict(jsonrpc='2.0', id=7,method=method,params=params or {})
    msg.update(changes)
    headers={} if token is None else {'Authorization':'Bearer '+token}
    return c.post('/api/mcp',json=msg,headers=headers)


def test_read_mcp_scope_revocation_and_source_wrapping(world,monkeypatch):
    monkeypatch.setenv('SAFELEDGER_CORS_ORIGINS','http://testserver')
    c,path=world
    owner=signup(c,'mcp_owner')
    result=record(c,amount_minor=23400)[0];assert result.status_code==201
    assert record(c,kind='invoice',vendor_id='supplier-a',invoice_number='BILL-7',amount_minor=900,due_on=str(today_bd()))[0].status_code==201
    before=c.get('/api/workspace').json()['dataset_revision']
    minted=c.post('/api/workspace/mcp-tokens',json={}).json()
    key=minted['token']
    assert key not in c.get('/api/workspace/mcp-tokens').text
    with sqlite3.connect(path) as db:
        assert key not in str(db.execute('SELECT * FROM api_tokens').fetchall())
    assert send(c,None,'tools/list').status_code==401
    assert send(c,key,'initialize',{'protocolVersion':'2025-06-18'}).json()['result']['protocolVersion']=='2025-06-18'
    tools=send(c,key,'tools/list').json()['result']['tools']
    assert {tool['name'] for tool in tools}=={'cash_balance','cash_forecast','open_supplier_bills'}
    assert all(tool['annotations']['readOnlyHint'] for tool in tools)
    def call(name,args={}):
        result=send(c,key,'tools/call',{'name':name,'arguments':args}).json()
        return json.loads(result['result']['content'][0]['text'])
    assert call('cash_balance')['balance_minor']==23400
    assert call('cash_forecast',{'horizon_days':7})['closing_balance_minor']==22500
    assert call('open_supplier_bills')['bills'][0]['number']=={'untrusted':True,'text':'BILL-7'}
    assert send(c,key,'tools/call',{'name':'cash_balance','arguments':{'business_id':'elsewhere'}}).json()['error']['code']==-32602
    assert send(c,key,'tools/call',{'name':'update_ledger'}).json()['error']['code']==-32602
    assert c.get('/api/workspace').json()['dataset_revision']==before
    revoke='/api/workspace/mcp-tokens/'+minted['id']+'/revoke'
    invite=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'mcp_other')
    assert call('cash_balance')['balance_minor']==23400 # uses token scope even alongside an unrelated cookie
    assert c.post(revoke,json={}).status_code==404
    assert c.post('/api/account/join',json={'code':invite}).status_code==200
    assert c.post('/api/workspace/mcp-tokens',json={}).status_code==403
    assert c.post(revoke,json={}).status_code==403
    c.post('/api/auth/logout',json={})
    from api.tests.test_accounts import PASSWORD
    assert c.post('/api/auth/login',json=dict(username='mcp_owner',password=PASSWORD)).status_code==200
    assert c.post(revoke,json={}).status_code==200
    assert send(c,key,'tools/list').status_code==401


def test_archiving_invalidates_mcp_and_notifications_return_202(world,monkeypatch):
    monkeypatch.setenv('SAFELEDGER_CORS_ORIGINS','http://testserver')
    c,_=world;owner=signup(c,'mcp_archive')
    key=c.post('/api/workspace/mcp-tokens',json={}).json()['token']
    headers={'Authorization':'Bearer '+key}
    assert c.post('/api/mcp',json={'jsonrpc':'2.0','method':'notifications/initialized'},headers=headers).status_code==202
    assert c.post('/api/mcp',json={'jsonrpc':'2.0','method':'ping','id':4},headers=dict(headers,Origin='https://example.invalid')).status_code==403
    assert c.post('/api/account/workspaces/clear',json={'business_ids':[owner['business_id']]}).status_code==200
    assert send(c,key,'tools/list').status_code==401
