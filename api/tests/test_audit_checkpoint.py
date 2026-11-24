"""Existing signatures survive checkpoint creation and newly separated cash notes."""
from contextlib import closing
import sqlite3
import pytest
from api.app import security as sec
from api.tests.test_accounts import world, signup
from api.tests.test_manual_inputs import record


def test_cash_notes_remain_visible_but_leave_no_plaintext_in_new_audit(world):
    client,path = world
    signup(client,'private_notes')
    note='Synthetic cash note'
    result,_ = record(client,amount_minor=3400,memo=note)
    source = result.json()['source_id']
    assert client.get('/api/workspace').json()['events'][0]['note']==note
    report = client.get('/api/workspace/export').json()
    assert report['cash_notes'][source]==note
    assert note not in str(report['audit_events'])
    with closing(sqlite3.connect(path)) as db:
        assert note not in db.execute('SELECT ciphertext FROM cash_notes').fetchone()[0]
        assert 'note_hash' in db.execute("SELECT payload FROM audit_events WHERE kind='RECORD_ADDED'").fetchone()[0]
    signup(client,'other_private_notes')
    assert client.get('/api/workspace/export').json()['cash_notes']=={}


def test_tail_verification_uses_signed_workspace_checkpoint(world,monkeypatch):
    client,path=world
    business=signup(client,'checkpoint_owner')['business_id']
    owner=sec.Principal('owner',business,'owner','test')
    with closing(sqlite3.connect(path)) as db:
        with sec.transaction(db):
            for i in range(33):
                sec.audit(db,owner,'test','SYNTHETIC',{'sequence':i})
            sec.verify_audit_head(db,business)
            anchor=db.execute('SELECT event_id FROM audit_checkpoints WHERE business_id=?',(business,)).fetchone()[0]
            sec.audit(db,owner,'test','SYNTHETIC',{'sequence':33})
            seen=[]
            original=sec.verify_rows
            def count_rows(rows,previous):
                materialized=list(rows)
                seen.extend(row['event_id'] for row in materialized)
                return original(materialized,previous)
            monkeypatch.setattr(sec,'verify_rows',count_rows)
            sec.verify_audit_head(db,business)
            assert seen==[anchor+1]
            assert len(sec.verified_audit(db,business))==35
        # Signed history edited by an offline repair operation cannot silently keep its old checkpoint.
        with db:
            db.execute('DROP TRIGGER immutable_audit_update')
            db.execute("UPDATE audit_events SET payload='{}' WHERE event_id=?",(anchor-1,))
        with pytest.raises(sec.PolicyError,match='AUDIT_INTEGRITY_FAILURE'),sec.transaction(db):
            sec.verify_audit_head(db,business)
