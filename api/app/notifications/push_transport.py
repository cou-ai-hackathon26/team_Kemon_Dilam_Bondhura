"""Payload-free Web Push (VAPID) and private FCM HTTP v1 display messages."""
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, utils
import httpx

TRANSPORT=None
TOKEN_LOCK=threading.Lock()
TOKENS={}


def enabled(kind):
    if os.getenv('SAFELEDGER_PUSH_ENABLED')!='1':
        return False
    return bool(os.getenv('SAFELEDGER_VAPID_PUBLIC') and os.getenv('SAFELEDGER_VAPID_PRIVATE')) if kind=='webpush' else bool(os.getenv('SAFELEDGER_FCM_SERVICE_ACCOUNT_JSON'))


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def push_endpoint(value):
    uri=urlsplit(value)
    host=uri.hostname or ''
    allowed = host in ('fcm.googleapis.com', 'push.services.mozilla.com', 'web.push.apple.com', 'notify.windows.com') or host.endswith((
        '.push.services.mozilla.com', '.push.apple.com', '.notify.windows.com',
    ))
    return (allowed and uri.scheme=='https' and uri.port in (443,None) and not uri.username and not uri.password
            and uri.path not in ('','/') and not uri.fragment and len(value)<=2048)


def jwt(data,algorithm,sign):
    head=b64(json.dumps({'typ':'JWT','alg':algorithm},separators=(',',':')).encode())
    plain=(head+'.'+b64(json.dumps(data,separators=(',',':')).encode())).encode()
    return plain.decode()+'.'+b64(sign(plain))


def vapid(endpoint):
    raw=os.environ['SAFELEDGER_VAPID_PRIVATE']
    scalar=int.from_bytes(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)),'big')
    private=ec.derive_private_key(scalar,ec.SECP256R1())
    public=b64(private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))
    if not hmac.compare_digest(public,os.environ['SAFELEDGER_VAPID_PUBLIC']):
        raise ValueError('Invalid VAPID configuration')
    subject=os.getenv('SAFELEDGER_VAPID_SUBJECT') or os.getenv('SAFELEDGER_PUBLIC_URL','')
    if not subject.startswith(('mailto:','https://')):
        raise ValueError('Invalid VAPID subject')
    def sign(data):
        r,s=utils.decode_dss_signature(private.sign(data,ec.ECDSA(hashes.SHA256())))
        return r.to_bytes(32,'big')+s.to_bytes(32,'big')
    token=jwt(dict(aud='https://'+urlsplit(endpoint).netloc,exp=int(time.time())+3600,sub=subject),'ES256',sign)
    return 'vapid t='+token+', k='+public


def send(transport,request):
    # Subscription URLs contain secret identifiers. Use the transport directly
    # so HTTP client logs never contain those URLs, and never follow redirects.
    request.extensions['timeout']=dict.fromkeys(('connect','read','write','pool'),8)
    response=transport.handle_request(request)
    try:
        raw=b''
        for chunk in response.iter_bytes():
            if len(raw)+len(chunk)>65536:
                raise ValueError('Push provider response too large')
            raw+=chunk
        return response.status_code,raw
    finally:
        response.close()


def fcm_access(transport,account):
    key_id=hashlib.sha256(account['private_key'].encode()).hexdigest()
    with TOKEN_LOCK:
        cached=TOKENS.get(key_id)
        if cached and cached[1]>time.time()+60:
            return cached[0]
        private=serialization.load_pem_private_key(account['private_key'].encode(),password=None)
        issued=int(time.time())
        uri='https://oauth2.googleapis.com/token'
        assertion=jwt(dict(iss=account['client_email'],scope='https://www.googleapis.com/auth/firebase.messaging',aud=uri,iat=issued,exp=issued+3600),
                      'RS256',lambda data:private.sign(data,padding.PKCS1v15(),hashes.SHA256()))
        status,raw=send(transport,httpx.Request('POST',uri,data={'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion}))
        if status!=200:
            raise ValueError('FCM credentials unavailable')
        value=json.loads(raw)
        token=value['access_token']
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_.~-]{10,4096}',token):
            raise ValueError('Invalid FCM authorization')
        TOKENS.clear()
        TOKENS[key_id]=(token,issued+min(int(value['expires_in']),3600))
        return token


def deliver_to(kind,address):
    if kind not in ('webpush','fcm') or not enabled(kind):
        raise ValueError('Push delivery disabled')
    with (TRANSPORT or httpx.HTTPTransport(trust_env=False)) as transport:
        if kind=='webpush':
            if not push_endpoint(address):
                raise ValueError('Unknown push service')
            request=httpx.Request('POST',address,headers={'TTL':'1800','Authorization':vapid(address)},content=b'')
        else:
            cfg=json.loads(base64.b64decode(os.environ['SAFELEDGER_FCM_SERVICE_ACCOUNT_JSON'],validate=True))
            project=cfg['project_id']
            if not isinstance(project,str) or not re.fullmatch(r'[a-z][a-z0-9-]{4,60}',project):
                raise ValueError('Invalid FCM project')
            auth=fcm_access(transport,cfg)
            notice={'title':'Capzoryx','body':'Open your workspace to review your updates.'}
            request=httpx.Request('POST','https://fcm.googleapis.com/v1/projects/'+project+'/messages:send',
                                  headers={'Authorization':'Bearer '+auth},json={'message':{'token':address,'notification':notice}})
        status,raw=send(transport,request)
        if kind=='fcm' and status==404:
            details=json.loads(raw).get('error',{}).get('details',[])
            if not any(isinstance(entry,dict) and entry.get('errorCode')=='UNREGISTERED' for entry in details):
                return 503
        return status
