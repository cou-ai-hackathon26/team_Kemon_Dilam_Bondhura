"""Store cash notes outside the immutable audit payload; verify them on read."""
from cryptography.exceptions import InvalidTag
from api.app import security as sec
from api.app.services.receivables import workspace_seal, workspace_unseal


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS cash_notes(
      business_id TEXT NOT NULL, source_id TEXT NOT NULL, ciphertext TEXT NOT NULL,
      PRIMARY KEY(business_id,source_id),
      FOREIGN KEY(business_id,source_id) REFERENCES source_records(business_id,source_record_id))""")


def store(db, business, source, kind, note):
    if not note:
        return {}
    signature = sec.signed('private-record-note', [business,source,note])
    if kind == 'event':
        blob = workspace_seal('cash-note-encryption-v1',business,note.encode(),source)
        db.execute('INSERT INTO cash_notes VALUES (?,?,?)', (business,source,blob))
    return {'note_hash':signature}


def read(db, business, audit):
    entries = [event['payload'] for event in audit if event['kind']=='RECORD_ADDED']
    notes = {entry['source_id']:entry['note'] for entry in entries if entry.get('note')}
    expected = {entry['source_id']:entry['note_hash'] for entry in entries if entry.get('note_hash') and entry['kind']=='event'}
    if not expected:
        return notes
    for source, ciphertext in db.execute('SELECT source_id,ciphertext FROM cash_notes WHERE business_id=?',(business,)):
        try:
            note = workspace_unseal('cash-note-encryption-v1',business,ciphertext,source).decode()
            digest = expected.pop(source)
            if not sec.valid_signature(digest,'private-record-note',[business,source,note]):
                raise ValueError()
        except (InvalidTag, ValueError, KeyError, UnicodeError):
            sec.fail(409,'RECORD_NOTE_INTEGRITY_FAILURE')
        notes[source] = note
    if expected:
        sec.fail(409,'RECORD_NOTE_INTEGRITY_FAILURE')
    return notes
