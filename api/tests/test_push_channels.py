"""All push tests use synthetic keys/tokens and in-process transports."""
import base64
import json
import sqlite3
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.notifications import push, push_transport as wire
from api.app.notifications.service import enqueue
from api.app.services.radar import should_alert
from api.tests.test_accounts import signup, world
from api.tests.test_notification_inbox import local


def decode(value):
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


@pytest.fixture
def vapid(monkeypatch):
    key=ec.generate_private_key(ec.SECP256R1())
    public=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    monkeypatch.setenv('SAFELEDGER_PUSH_ENABLED','1')
    monkeypatch.setenv('SAFELEDGER_VAPID_PRIVATE',wire.b64(key.private_numbers().private_value.to_bytes(32,'big')))
    monkeypatch.setenv('SAFELEDGER_VAPID_PUBLIC',wire.b64(public))
    monkeypatch.setenv('SAFELEDGER_VAPID_SUBJECT','mailto:test@example.invalid')
    return key.public_key()


@pytest.mark.parametrize('endpoint', [
    'https://updates.push.services.mozilla.com/wpush/v2/fixture-token',
    'https://wns2-test.notify.windows.com/w/?token=fixture-token',
])
def test_private_push_subscription_limits_quiet_and_revoke(world,vapid,monkeypatch,endpoint):
    c,path=world
    actor=signup(c,'push_owner')
    business=actor['business_id']
    route='/api/notifications/subscriptions'
    def sub(url=endpoint):
        return c.post(route,json={'kind':'webpush','endpoint':url})
    for bad in ['https://example.invalid/a','https://notnotify.windows.com/w/','https://fcm.googleapis.com:1234/a','https://xx@fcm.googleapis.com/a']:
        assert sub(bad).status_code==422
    ident=sub().json()['id']
    assert sub().json()['id']==ident
    delivered=[]
    def handle(request):
        assert str(request.url)==endpoint and request.content==b''
        jwt=request.headers['Authorization'].removeprefix('vapid t=').split(', k=')[0]
        a,b,sig=jwt.split('.')
        sig=decode(sig)
        vapid.verify(utils.encode_dss_signature(int.from_bytes(sig[:32],'big'),int.from_bytes(sig[32:],'big')),
                     (a+'.'+b).encode(),ec.ECDSA(hashes.SHA256()))
        assert json.loads(decode(b))['aud']=='https://'+urlsplit(endpoint).netloc
        delivered.append(True)
        return httpx.Response(201)
    monkeypatch.setattr(wire,'TRANSPORT',httpx.MockTransport(handle))
    at=local(today_bd(),23)
    morning=local(today_bd()+timedelta(days=1),8)
    with sqlite3.connect(path) as db:
        assert endpoint not in str(db.execute('SELECT * FROM push_subscriptions').fetchone())
        with sec.transaction(db):
            assert should_alert(db, business, dict(level='alert', breach_on=str(today_bd()), shortfall_minor=100), at)
            for ref in ['a','a','b','c','d','e']:
                enqueue(db,business,'cash_breach',ref,at=at)
        assert db.execute("SELECT count(*) FROM notification_jobs WHERE channel='webpush'").fetchone()[0]==3
        push.deliver(db,business,at)
        assert not delivered
        push.deliver(db,business,morning)
        push.deliver(db,business,morning)
        assert len(delivered)==3
        later=morning+86400
        with sec.transaction(db):
            enqueue(db,business,'cash_breach','stale',at=later)
        assert c.post(route+'/'+ident+'/revoke',json={}).status_code==200
        assert sub().json()['id']!=ident
        push.deliver(db,business,later)
        assert len(delivered)==3
    signup(c,'other_push_owner')
    assert c.post(route+'/'+ident+'/revoke',json={}).status_code==404


def test_mock_fcm_signs_scoped_assertion_and_handles_expired_token(monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    key_bytes=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    account=dict(project_id='sample-123',client_email='test@example.invalid',private_key=key_bytes.decode())
    monkeypatch.setenv('SAFELEDGER_FCM_SERVICE_ACCOUNT_JSON',base64.b64encode(json.dumps(account).encode()).decode())
    monkeypatch.delenv('SAFELEDGER_PUSH_ENABLED',raising=False)
    with pytest.raises(ValueError,match='disabled'):
        wire.deliver_to('fcm','synthetic-device-token')
    monkeypatch.setenv('SAFELEDGER_PUSH_ENABLED','1')
    calls=[]
    def handle(request):
        calls.append(str(request.url))
        if request.url.host=='oauth2.googleapis.com':
            assertion=parse_qs(request.content.decode())['assertion'][0]
            a,b,s=assertion.split('.')
            key.public_key().verify(decode(s),(a+'.'+b).encode(),padding.PKCS1v15(),hashes.SHA256())
            assert json.loads(decode(b))['scope']=='https://www.googleapis.com/auth/firebase.messaging'
            return httpx.Response(200,json={'access_token':'fixture-access-token','expires_in':3600})
        assert request.headers['Authorization']=='Bearer fixture-access-token'
        msg=json.loads(request.content)['message']
        assert msg=={'token':'synthetic-device-token','notification':{'title':'Capzoryx','body':'Open your workspace to review your updates.'}}
        return httpx.Response(404,json={'error':{'details':[{'errorCode':'UNREGISTERED'}]}})
    monkeypatch.setattr(wire,'TRANSPORT',httpx.MockTransport(handle))
    assert wire.deliver_to('fcm','synthetic-device-token')==404
    assert wire.deliver_to('fcm','synthetic-device-token')==404
    assert calls==['https://oauth2.googleapis.com/token']+['https://fcm.googleapis.com/v1/projects/sample-123/messages:send']*2
