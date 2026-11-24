"""Offline records are scoped, replayable additions to the owner's current revision."""
import uuid

from api.app.accounts import today_bd
from api.tests.test_accounts import world, signup


def test_reconnect_replay_and_workspace_scope(world):
    c,_ = world
    owner = signup(c,'offline_owner')
    path='/api/workspace/offline-records'
    draft=dict(user_id=owner['user_id'],business_id=owner['business_id'],currency='BDT',request_id=str(uuid.uuid4()),
               amount_minor=12345,kind='cash',effective_on=today_bd().isoformat(),direction='inflow')
    result=c.post(path,json=draft)
    assert result.status_code==201,result.text
    assert c.post(path,json=dict(draft,request_id=str(uuid.uuid4()),amount_minor=678)).status_code==201
    assert c.post(path,json=draft).json()['replayed']
    assert c.post(path,json=dict(draft,amount_minor=32)).status_code==409
    assert c.post(path,json=dict(draft,request_id=str(uuid.uuid4()),currency='USD')).status_code==409
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==13023
    signup(c,'offline_other')
    assert c.post(path,json=draft).status_code==403
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0


def test_voice_headers_and_capture_manifest():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    manifest_path=root/'web/public/manifest.webmanifest'
    manifest=json.loads(manifest_path.read_text())
    assert manifest['share_target']=={'action':'/share','method':'POST','enctype':'application/x-www-form-urlencoded','params':{'text':'text'}}
    headers=json.loads((root/'web/vercel.json').read_text())['headers'][0]['headers']
    values={h['key']:h['value'] for h in headers}
    assert 'camera=(self), microphone=(self)' in values['Permissions-Policy']
    assert "media-src 'self' blob:" in values['Content-Security-Policy']
