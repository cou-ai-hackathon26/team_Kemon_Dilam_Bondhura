"""Customer balances are separate from the immutable supplier and cash dataset."""
import base64
import os
from hashlib import sha256
from datetime import date
from typing import NamedTuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from api.app import keyring, security as sec


class ExpectedReceipt(NamedTuple):
    source_id: str
    due_on: date
    amount_minor: int


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS customer_keys(
      business_id TEXT PRIMARY KEY, wrapped_key TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS customers(
      id TEXT PRIMARY KEY, business_id TEXT NOT NULL, name_enc TEXT NOT NULL, key_tag TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(business_id,id));
    CREATE TABLE IF NOT EXISTS customer_contacts(
      customer_id TEXT PRIMARY KEY, business_id TEXT NOT NULL, phone_enc TEXT NOT NULL, updated_at INTEGER NOT NULL,
      FOREIGN KEY(business_id,customer_id) REFERENCES customers(business_id,id));
    CREATE TABLE IF NOT EXISTS receivables(
      id TEXT PRIMARY KEY, business_id TEXT NOT NULL, customer_id TEXT NOT NULL, number TEXT,
      amount_minor INTEGER NOT NULL CHECK(amount_minor>0), issued_on TEXT NOT NULL, due_on TEXT NOT NULL,
      created_at INTEGER NOT NULL, UNIQUE(business_id,id),
      FOREIGN KEY(business_id,customer_id) REFERENCES customers(business_id,id));
    CREATE TABLE IF NOT EXISTS receivable_settlements(
      id TEXT PRIMARY KEY, business_id TEXT NOT NULL, receivable_id TEXT NOT NULL, payment_event_id TEXT,
      amount_minor INTEGER NOT NULL CHECK(amount_minor>0), request_id TEXT NOT NULL, cash_record_id TEXT NOT NULL,
      created_at INTEGER NOT NULL, UNIQUE(business_id,request_id), UNIQUE(business_id,cash_record_id),
      FOREIGN KEY(business_id,receivable_id) REFERENCES receivables(business_id,id),
      FOREIGN KEY(business_id,cash_record_id) REFERENCES source_records(business_id,source_record_id));
    CREATE INDEX IF NOT EXISTS settlements_by_receivable ON receivable_settlements(business_id,receivable_id);
    CREATE TABLE IF NOT EXISTS collection_requests(
      business_id TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL, result_id TEXT NOT NULL,
      PRIMARY KEY(business_id,request_id));
    CREATE VIEW IF NOT EXISTS receivable_balances AS
      SELECT r.*, r.amount_minor - COALESCE((
        SELECT SUM(s.amount_minor) FROM receivable_settlements s
        WHERE s.business_id=r.business_id AND s.receivable_id=r.id),0) AS open_minor
      FROM receivables r;
    CREATE TRIGGER IF NOT EXISTS settlement_limit BEFORE INSERT ON receivable_settlements
      WHEN NEW.amount_minor > (SELECT open_minor FROM receivable_balances
        WHERE business_id=NEW.business_id AND id=NEW.receivable_id)
      BEGIN SELECT RAISE(ABORT,'settlement exceeds open balance'); END;
    """)
    for table in ('customers', 'receivables', 'receivable_settlements', 'collection_requests'):
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation} BEFORE {operation}
                ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END""")


def open_receipts(db, business):
    rows = db.execute("""SELECT id,due_on,open_minor FROM receivable_balances
                         WHERE business_id=? AND open_minor>0 ORDER BY due_on,id""", (business,))
    return [ExpectedReceipt(identity, date.fromisoformat(due), amount) for identity, due, amount in rows]


def seal(key, value, purpose):
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, value, purpose.encode())
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def unseal(key, encoded, purpose):
    data = base64.urlsafe_b64decode(encoded)
    return AESGCM(key).decrypt(data[:12], data[12:], purpose.encode())


def key_tag(key):
    return sha256(key).hexdigest()


def workspace_seal(label, business, plaintext, purpose):
    ring = keyring.get()
    raw, kid = ring.current()
    wrapping = keyring.mac(raw,kid,(label+':'+sec.canonical(business)).encode())
    return ring.label(seal(wrapping,plaintext,purpose))


def workspace_unseal(label, business, encoded, purpose):
    raw,kid,ciphertext = keyring.get().open(encoded)
    wrapping = keyring.mac(raw,kid,(label+':'+sec.canonical(business)).encode())
    return unseal(wrapping,ciphertext,purpose)


def customer_key(db, business, create=False):
    row = db.execute("SELECT wrapped_key FROM customer_keys WHERE business_id=?", (business,)).fetchone()
    if row:
        key = workspace_unseal('customer-key-wrap-v1',business,row[0],business)
        ring = keyring.get()
        if create and ring.versioned and ring.open(row[0])[1] != ring.active:
            wrapped = workspace_seal('customer-key-wrap-v1',business,key,business)
            db.execute('UPDATE customer_keys SET wrapped_key=? WHERE business_id=?',(wrapped,business))
        return key
    if create:
        key = AESGCM.generate_key(bit_length=256)
        wrapped = workspace_seal('customer-key-wrap-v1',business,key,business)
        db.execute('INSERT INTO customer_keys VALUES (?,?)', (business,wrapped))
        return key
    return None
