"""Owner-issued, revocable aggregate reports. The public URL is a bearer capability."""
import base64
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
from typing import Annotated
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import APIRouter, Depends, Path as ApiPath, Request
from pydantic import Field

from api.app import security as sec
from api.app.contracts import Contract
from api.app.routes import db_connection, principal
from api.app.services.finance import snapshot

router = APIRouter(prefix='/api')
KEY_FILE = Path(__file__).resolve().parents[2] / 'web/public/.well-known/capzoryx-passport-key.json'
PassportId = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS passports(
            id TEXT PRIMARY KEY, business_id TEXT NOT NULL, period_days INTEGER NOT NULL,
            summary_json TEXT NOT NULL, chain_head TEXT NOT NULL, signature TEXT NOT NULL,
            key_id TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS passports_workspace ON passports(business_id, expires_at);
    """)


def public_keys():
    return json.loads(KEY_FILE.read_text(encoding="utf-8"))


def signer():
    try:
        key_id = os.environ['SAFELEDGER_PASSPORT_ACTIVE_KEY']
        raw = json.loads(os.environ['SAFELEDGER_PASSPORT_KEYS'])[key_id]
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
        if base64.b64encode(key.public_key().public_bytes_raw()).decode() != public_keys()[key_id]:
            raise ValueError('Key mismatch')
        return key_id, key
    except (KeyError, OSError, TypeError, ValueError):
        sec.fail(503, 'PASSPORT_SIGNER_UNAVAILABLE')


@router.post('/passport/create', status_code=201)
def create(actor=Depends(principal), db=Depends(db_connection)):
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role != 'owner':
            sec.fail(403, 'OWNER_REQUIRED')
        if db.execute('SELECT 1 FROM workspace_meta WHERE business_id=? AND archived_at IS NOT NULL',(current.business_id,)).fetchone():
            sec.fail(403,'WORKSPACE_CLEARED')
        sec.budget(db, 'passport-issue:' + current.user_id, 5)
        audit = sec.verified_audit(db, current.business_id)
        ds = sec.runtime_dataset(db, current.business_id)
        key_id, key = signer()
        issued = sec.now()
        start = max(ds.opening_on, ds.as_of - timedelta(days=29))
        movements = [e for e in ds.events if e.status == 'cleared' and start <= e.effective_on <= ds.as_of]
        report = dict(id=secrets.token_hex(16), format='capzoryx-passport-v1', key_id=key_id,
                      issued_at=issued, expires_at=issued + 90 * 86400, start=start.isoformat(),
                      as_of=ds.as_of.isoformat(), currency=ds.currency, balance_minor=snapshot(ds)['balance_minor'],
                      receipts_minor=sum(e.amount_minor for e in movements if e.direction == 'inflow'),
                      expenses_minor=sum(e.amount_minor for e in movements if e.direction == 'outflow'),
                      chain_head=audit[-1]['event_hash'] if audit else '0' * 64)
        encoded = sec.canonical(report)
        signature = base64.b64encode(key.sign(encoded.encode())).decode()
        db.execute('INSERT INTO passports VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                   (report['id'], current.business_id, (ds.as_of-start).days+1, encoded, report['chain_head'],
                    signature, key_id, issued, report['expires_at']))
        sec.audit(db, current, str(uuid.uuid4()), 'PASSPORT_ISSUED', {'expires_at': report['expires_at']})
    return {'id': report['id'], 'expires_at': report['expires_at'], 'simulation_only': True}


@router.get('/passport/list')
def active(actor=Depends(principal), db=Depends(db_connection)):
    if actor.role != 'owner':
        sec.fail(403, 'OWNER_REQUIRED')
    rows = db.execute('SELECT id, expires_at FROM passports WHERE business_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC',
                      (actor.business_id, sec.now()))
    return {'passports': [{'id': row[0], 'expires_at': row[1]} for row in rows], 'simulation_only': True}


class Revoke(Contract):
    id: PassportId


@router.post('/passport/revoke')
def revoke(body: Revoke, actor=Depends(principal), db=Depends(db_connection)):
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        if current.role != 'owner':
            sec.fail(403, 'OWNER_REQUIRED')
        sec.verified_audit(db, current.business_id)
        row = db.execute('SELECT revoked_at FROM passports WHERE business_id=? AND id=?',
                         (current.business_id, body.id)).fetchone()
        if row is None:
            sec.fail(404, 'PASSPORT_UNAVAILABLE')
        if row[0] is None:
            db.execute('UPDATE passports SET revoked_at=? WHERE business_id=? AND id=?', (sec.now(), current.business_id, body.id))
            sec.audit(db, current, str(uuid.uuid4()), 'PASSPORT_REVOKED', {})
    return {'revoked': True, 'simulation_only': True}


@router.get('/public/passport/{passport_id}')
def verify(request: Request, passport_id: Annotated[str, ApiPath(pattern=r'^[a-f0-9]{32}$')], db=Depends(db_connection)):
    claimed, edge = sec.client_keys(request)
    sec.budgets(db, [('passport-public:' + claimed, 30), ('passport-edge:' + edge, 120)])
    row = db.execute('SELECT summary_json, signature, key_id FROM passports WHERE id=? AND revoked_at IS NULL AND expires_at>?',
                     (passport_id, sec.now())).fetchone()
    if not row:
        sec.fail(404, 'PASSPORT_UNAVAILABLE')
    try:
        raw, signature, key_id = row
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_keys()[key_id], validate=True))
        key.verify(base64.b64decode(signature, validate=True), raw.encode())
        report = json.loads(raw)
        if set(report)!={'id','format','key_id','issued_at','expires_at','start','as_of','currency','balance_minor','receipts_minor','expenses_minor','chain_head'}:
            raise ValueError('Unexpected v1 fields')
        if (sec.canonical(report)!=raw or report['format']!='capzoryx-passport-v1' or report['id'] != passport_id
                or report['key_id'] != key_id or report['expires_at'] <= sec.now()):
            raise ValueError('Invalid binding')
    except (OSError, ValueError, KeyError, TypeError, InvalidSignature):
        sec.fail(409, 'PASSPORT_INVALID')
    return {'summary': report, 'canonical': raw, 'signature': signature}
