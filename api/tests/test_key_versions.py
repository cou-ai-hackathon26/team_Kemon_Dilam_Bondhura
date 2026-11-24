"""Rotation rehearsals use generated test keys and isolated workspaces."""
from contextlib import closing
import json
import secrets
import sqlite3
from api.app import keyring, mfa, profile, security as sec
from api.tests.test_accounts import world, signup
from api.tests.test_manual_inputs import record
from api.tests.test_receivables import issue
from api.tests.test_security_execution import rig, seed


def ring(monkeypatch):
    keys={name:secrets.token_hex(32) for name in ('one','two')}
    monkeypatch.setenv('SAFELEDGER_SIGNING_KEYS',json.dumps(keys))
    monkeypatch.setenv('SAFELEDGER_ACTIVE_SIGNING_KEY_ID','legacy')
    return lambda kid:monkeypatch.setenv('SAFELEDGER_ACTIVE_SIGNING_KEY_ID',kid)


def test_ledger_verifies_old_keys_while_only_new_key_signs(rig,monkeypatch):
    old = rig.proposal()
    activate = ring(monkeypatch)
    activate('one')
    assert rig.decision(old).status_code==200
    with closing(rig.db()) as db:
        assert db.execute('SELECT signature FROM approvals').fetchone()[0].startswith('k1.one.')
        assert db.execute('SELECT signature FROM postings').fetchone()[0].startswith('k1.one.')
    activate('two')
    assert rig.balance()==8500000
    assert rig.decision(old).status_code==200
    with closing(rig.db()) as db:
        assert db.execute('SELECT signature FROM audit_events ORDER BY event_id DESC').fetchone()[0].startswith('k1.two.')
        assert sec.verified_audit(db,'company-A')
    signed=sec.signed('audit','sample')
    assert not sec.valid_signature(signed.replace('k1.two.','k1.one.'),'audit','sample')
    assert not sec.valid_signature(signed.split('.')[-1],'audit','sample')


def test_rotation_preserves_personal_data_otp_and_one_use_codes(world,monkeypatch):
    client,path=world
    account=signup(client,'keyring_owner')
    business=account['business_id']
    otp=mfa.new_secret()
    legacy_secret, legacy_backup = mfa.seal(otp), mfa.hash_backup('abcd-efgh')
    first,_,_ = issue(client)
    assert record(client, amount_minor=3000,memo='legacy note')[0].status_code==201
    activate=ring(monkeypatch)
    with sqlite3.connect(path) as db:
        code=profile.issue_code(db,'test','unit')
    activate('one')
    sealed=mfa.seal(otp)
    backup=mfa.hash_backup('klmn-qrst')
    assert sealed.startswith('k1.one.') and backup.startswith('k1.one.')
    assert record(client,amount_minor=800,memo='version one note')[0].status_code==201
    issue(client)
    with sqlite3.connect(path) as db:
        wrapped=db.execute('SELECT wrapped_key FROM customer_keys WHERE business_id=?',(business,)).fetchone()[0]
        assert wrapped.startswith('k1.one.')
        new_code=profile.issue_code(db,'other','subject')
    activate('two')
    assert mfa.unseal(legacy_secret)==otp and mfa.unseal(sealed)==otp
    assert mfa.matches_backup(legacy_backup,'abcd-efgh') and mfa.matches_backup(backup,'klmn-qrst')
    assert not mfa.matches_backup(backup,'zzzz-yyyy')
    with sqlite3.connect(path) as db:
        assert profile.check_code(db,'test','unit',code)
        assert profile.check_code(db,'other','subject',new_code)
        assert not profile.check_code(db,'other','subject',new_code)
    report=client.get('/api/workspace/export').json()
    assert set(report['cash_notes'].values())=={'legacy note','version one note'}
    assert next(c['name'] for c in report['customers'] if c['id']==first)=='মেঘনা'


def test_invalid_config_fails_closed_without_replacing_legacy_key(world,monkeypatch):
    from pytest import raises
    secret=secrets.token_hex(32)
    monkeypatch.setenv('SAFELEDGER_SIGNING_KEYS',json.dumps({'alpha':secret,'beta':secret}))
    monkeypatch.setenv('SAFELEDGER_ACTIVE_SIGNING_KEY_ID','alpha')
    with raises(keyring.KeyUnavailable):
        keyring.get()
