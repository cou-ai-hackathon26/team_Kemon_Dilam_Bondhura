import sqlite3

from api.app.feature_flags import set_flag
from api.tests.test_accounts import signup, world
from api.tests.test_passport import keyring


def test_workspace_pause_and_operator_precedence(world, keyring):
    client, path = world
    url = '/api/workspace/features/passport_issue'
    assert client.post(url, json={'enabled': False}).status_code == 401
    signup(client, 'flag_owner')
    pid = client.post('/api/passport/create', json={}).json()['id']
    assert client.post(url, json={'enabled': False}).status_code == 200
    assert client.post('/api/passport/create', json={}).status_code == 503
    assert client.get('/api/public/passport/' + pid).status_code == 200
    assert client.post('/api/passport/revoke', json={'id': pid}).status_code == 200
    signup(client, 'flag_other')
    assert client.post('/api/passport/create', json={}).status_code == 201
    with sqlite3.connect(path) as db:
        set_flag(db, 'passport_issue', 'global', False)
    flags = client.post(url, json={'enabled': True}).json()['features']
    assert next(f for f in flags if f['name'] == 'passport_issue')['enabled'] is False
    assert client.post('/api/passport/create', json={}).status_code == 503
    with sqlite3.connect(path) as db:
        set_flag(db, 'passport_issue', 'global', True)
        db.execute("UPDATE security_users SET role='viewer'")
    assert client.post(url, json={'enabled': False}).status_code == 403
    assert client.get('/api/workspace/features').status_code == 403
