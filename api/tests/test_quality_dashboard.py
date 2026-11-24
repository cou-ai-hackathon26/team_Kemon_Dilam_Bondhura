import sqlite3
import uuid
from api.app import security as sec
from api.tests.test_accounts import world, signup


def insert_call(db, business, trace, ok):
    db.execute('INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex,trace,sec.signature('ai-tenant',business),'explain',
                'compatible','test','question',0,10,12,3,ok,sec.now()))


def test_owner_sees_only_current_workspace_and_admin_requires_its_own_secret(world,monkeypatch):
    c,path=world
    first=signup(c,'quality_owner')
    flow=uuid.uuid4().hex
    with sqlite3.connect(path) as db:
        insert_call(db,first['business_id'],flow,0)
        insert_call(db,first['business_id'],flow,1)
        insert_call(db,'unrelated',uuid.uuid4().hex,1)
    view=c.get('/api/admin/ai-quality').json()
    assert view['scope']=='workspace' and view['calls']==2 and view['requests']==1 and view['successful_calls']==1
    assert view['traces'][0]['trace_id']==flow
    assert all(row['source']=='synthetic' and row['passed']<=row['samples'] for row in view['evaluations'])
    assert 'unrelated' not in str(view)
    invite=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'quality_viewer')
    assert c.get('/api/admin/ai-quality').json()['calls']==0
    assert c.post('/api/account/join',json={'code':invite}).status_code==200
    assert c.get('/api/admin/ai-quality').status_code==403
    c.post('/api/auth/logout',json={})
    assert c.get('/api/admin/ai-quality').status_code==401
    assert c.get('/api/admin/ai-quality',headers={'X-Capzoryx-Admin-Token':'bad'}).status_code==403
    secret='synthetic-admin-secret-for-testing-only'
    monkeypatch.setenv('SAFELEDGER_ADMIN_TOKEN',secret)
    admin=c.get('/api/admin/ai-quality',headers={'X-Capzoryx-Admin-Token':secret}).json()
    assert admin['scope']=='all_workspaces' and admin['calls']==3 and admin['requests']==2 and secret not in str(admin)
