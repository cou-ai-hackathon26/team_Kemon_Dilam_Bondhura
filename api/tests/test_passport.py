import base64
import json
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from api.app import passport
from api.tests.test_accounts import signup, world


@pytest.fixture
def keyring(monkeypatch):
    key = Ed25519PrivateKey.generate()
    keys = {'test': base64.b64encode(key.public_key().public_bytes_raw()).decode()}
    monkeypatch.setattr(passport, 'public_keys', lambda: keys)
    monkeypatch.setenv('SAFELEDGER_PASSPORT_ACTIVE_KEY', 'test')
    monkeypatch.setenv('SAFELEDGER_PASSPORT_KEYS', json.dumps({'test':base64.b64encode(key.private_bytes_raw()).decode()}))
    return keys


@pytest.mark.parametrize('problem', ['tampered', 'expired', 'wrong-key', 'revoked'])
def test_passport_verification(world, keyring, problem):
    client, path = world
    signup(client, 'passport_owner')
    link = client.post('/api/passport/create', json={})
    assert link.status_code == 201, link.text
    pid = link.json()['id']
    url = '/api/public/passport/' + pid
    good = client.get(url).json()
    assert good['summary']['id'] == pid and 'business_id' not in good['summary']
    assert json.loads(good['canonical']) == good['summary']
    assert len(client.get('/api/passport/list').json()['passports']) == 1
    signup(client, 'other_passport_account')
    assert client.get('/api/passport/list').json()['passports'] == []
    assert client.post('/api/passport/revoke', json={'id': pid}).status_code == 404
    if problem == 'wrong-key':
        keyring['test'] = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    elif problem == 'revoked':
        from api.tests.test_accounts import PASSWORD
        client.post('/api/auth/login', json={'username':'passport_owner', 'password':PASSWORD})
        assert client.post('/api/passport/revoke', json={'id':pid}).status_code == 200
    else:
        with sqlite3.connect(path) as db:
            if problem == 'expired':
                db.execute('UPDATE passports SET expires_at=0')
            else:
                db.execute("UPDATE passports SET summary_json=replace(summary_json,'\"balance_minor\":0','\"balance_minor\":1')")
    client.cookies.clear()
    assert client.get(url).status_code in {404, 409}


def test_clear_revokes_public_link_even_after_restore(world,keyring):
    client,_=world
    owner=signup(client,'passport_clear')
    business=owner['business_id']
    identity=client.post('/api/passport/create',json={}).json()['id']
    endpoint='/api/public/passport/'+identity
    assert client.get(endpoint).status_code==200
    assert client.post('/api/account/workspaces/clear',json={'business_ids':[business],'erase_customer_details':True}).status_code==200
    assert client.get(endpoint).status_code==404
    assert client.post('/api/account/workspaces/restore',json={'business_id':business}).status_code==200
    assert client.get(endpoint).status_code==404
