from api.tests.test_accounts import PASSWORD, world
from api.app import app_sessions as sessions
from api.app import security as sec
import sqlite3

APP={'X-SafeLedger-Client':'app','Origin':sessions.ORIGIN}

def headers(pair):
    return dict(APP,Authorization='Bearer '+pair['access_token'])

def create(c):
    r=c.post('/api/auth/signup',headers=APP,json={'username':'app_user','password':PASSWORD})
    assert r.status_code==201, r.text
    assert 'set-cookie' not in r.headers
    return r.json()

def renew(c,pair):
    return c.post('/api/auth/refresh',headers=APP,json={'refresh_token':pair['refresh_token']})

def test_native_session_rotation_replay_and_origin(world):
    c,path=world
    pair=create(c)
    with sqlite3.connect(path) as db:
        expiry=db.execute('SELECT expires_at FROM security_sessions WHERE token_hash=?',(sessions.digest(pair['access_token']),)).fetchone()[0]
        assert 897 <= expiry-sec.now() <= 900
        assert db.execute('SELECT token_hash FROM app_refresh_tokens').fetchone()[0]==sessions.digest(pair['refresh_token'])
    assert c.get('/api/session',headers=headers(pair)).status_code==200
    assert c.get('/api/session',headers=dict(headers(pair),Origin='http://testserver')).status_code==403
    rotated=renew(c,pair)
    assert rotated.status_code==200
    assert renew(c,pair).status_code==401 and renew(c,rotated.json()).status_code==401
    assert c.get('/api/session',headers=headers(rotated.json())).status_code==401

def test_switch_revoke_and_cookie_isolation(world):
    c,_=world
    first=create(c)
    other=c.post('/api/auth/login',headers=APP,json={'username':'app_user','password':PASSWORD}).json()
    web=c.post('/api/auth/login',json={'username':'app_user','password':PASSWORD})
    assert 'set-cookie' in web.headers and 'access_token' not in web.json()
    assert c.post('/api/auth/logout',json={}).status_code==200
    new=c.post('/api/account/workspaces',headers=headers(first),json={'kind':'personal','name':'Demo shop','currency':'BDT'})
    assert new.status_code==201,new.text
    pair=new.json()
    rotated=renew(c,pair).json()
    assert c.get('/api/session',headers=headers(rotated)).json()['business_id']==pair['business_id']
    listing=c.get('/api/account/app-devices',headers=headers(rotated)).json()['devices']
    assert len(listing)==2
    target=next(row['family_id'] for row in listing if not row['current'])
    assert c.post(f'/api/account/app-devices/{target}/revoke',headers=headers(rotated),json={}).status_code==200
    assert renew(c,other).status_code==401
    assert c.post('/api/auth/logout',headers=headers(rotated),json={}).status_code==200
    assert renew(c,rotated).status_code==401

def test_password_preserves_only_current_native_family(world):
    c,_=world
    first=create(c)
    second=c.post('/api/auth/login',headers=APP,json={'username':'app_user','password':PASSWORD}).json()
    assert c.post('/api/account/password',headers=headers(first),json={'current_password':PASSWORD,'new_password':'A different long phrase 1234'}).status_code==200
    assert renew(c,second).status_code==401
    assert renew(c,first).status_code==200
