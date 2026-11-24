"""Native device session families. Access tokens use the existing session verifier."""
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from api.app import security as sec
from api.app.contracts import Contract
from api.app.routes import db_connection, principal

ORIGIN = 'https://app.capzoryx.local'
ACCESS_TTL = 900
REFRESH_TTL = 30 * 86400
router = APIRouter(prefix='/api')


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_refresh_tokens(
          token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL, account_id TEXT NOT NULL,
          seat_user_id TEXT NOT NULL, device_label TEXT NOT NULL, expires_at INTEGER NOT NULL,
          used_at INTEGER, revoked_at INTEGER);
        CREATE INDEX IF NOT EXISTS app_refresh_family ON app_refresh_tokens(family_id);
        CREATE INDEX IF NOT EXISTS app_refresh_account ON app_refresh_tokens(account_id);
        CREATE TABLE IF NOT EXISTS app_access_devices(
          token_hash TEXT PRIMARY KEY REFERENCES security_sessions(token_hash) ON DELETE CASCADE,
          family_id TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS app_access_family ON app_access_devices(family_id);
    """)


def native(request):
    if request.headers.get('X-SafeLedger-Client') != 'app':
        return False
    if request.headers.get('origin') != ORIGIN:
        sec.fail(403, 'UNTRUSTED_APP_ORIGIN')
    return True


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def revoke(db, family):
    db.execute('UPDATE app_refresh_tokens SET revoked_at=? WHERE family_id=? AND revoked_at IS NULL',
               (sec.now(),family))
    db.execute('DELETE FROM security_sessions WHERE token_hash IN (SELECT token_hash FROM app_access_devices WHERE family_id=?)',
               (family,))


def family_for(db, access_hash):
    row = db.execute('SELECT family_id FROM app_access_devices WHERE token_hash=?',(access_hash,)).fetchone()
    return row[0] if row else None


def revoke_account(db, account, keep=None):
    families = db.execute('SELECT DISTINCT family_id FROM app_refresh_tokens WHERE account_id=? AND revoked_at IS NULL',
                          (account,)).fetchall()
    for (family,) in families:
        if family != keep:
            revoke(db, family)


def revoke_member(db, seat):
    account = db.execute('SELECT account_id FROM account_members WHERE user_id=?',(seat,)).fetchone()
    if account:
        revoke_account(db,account[0])


def mint(db, seat, account, family, expiry):
    from api.app.accounts import issue
    refresh = secrets.token_urlsafe(48)
    access = issue(db,seat,ACCESS_TTL)
    db.execute('INSERT INTO app_access_devices VALUES (?,?)',(digest(access),family))
    db.execute('INSERT INTO app_refresh_tokens VALUES (?,?,?,?,?,?,NULL,NULL)',
               (digest(refresh),family,account,seat,'Android',expiry))
    return dict(access_token=access,refresh_token=refresh)


def issue_client(db, request, response, seat, ttl, *, switch_device=False):
    from api.app.accounts import issue, set_cookie
    if not native(request):
        token = issue(db,seat,ttl)
        set_cookie(response,token,ttl)
        return token
    account = db.execute('SELECT account_id FROM account_members WHERE user_id=?',(seat,)).fetchone()[0]
    family = None
    if switch_device:
        supplied = request.headers.get('authorization','').split()
        if len(supplied)==2 and supplied[0].lower()=='bearer':
            family = family_for(db,digest(supplied[1]))
    expiry = sec.now()+REFRESH_TTL
    if family:
        row = db.execute('SELECT expires_at FROM app_refresh_tokens WHERE family_id=? AND account_id=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at>?',
                         (family,account,sec.now())).fetchone()
        if not row:sec.fail(401,'REFRESH_EXPIRED')
        expiry = row[0]
        # Retire the previous seat and its refresh credential in this same transaction.
        db.execute('UPDATE app_refresh_tokens SET used_at=? WHERE family_id=? AND used_at IS NULL',
                   (sec.now(),family))
        db.execute('DELETE FROM security_sessions WHERE token_hash IN (SELECT token_hash FROM app_access_devices WHERE family_id=?)',
                   (family,))
    pair = mint(db,seat,account,family or uuid.uuid4().hex,expiry)
    request.state.app_tokens=pair
    return pair['access_token']


class Refresh(Contract):
    refresh_token: str = Field(min_length=32,max_length=128)


@router.post('/auth/refresh')
def refresh(body: Refresh, request: Request, db=Depends(db_connection)):
    if not native(request):
        sec.fail(403,'APP_CLIENT_REQUIRED')
    sec.budget(db,'app-refresh:'+sec.client_keys(request)[1],120)
    with sec.transaction(db):
        row = db.execute('SELECT family_id,account_id,seat_user_id,expires_at,used_at,revoked_at FROM app_refresh_tokens WHERE token_hash=?',
                         (digest(body.refresh_token),)).fetchone()
        if not row or row[5] is not None or row[3] <= sec.now():
            sec.fail(401,'REFRESH_EXPIRED')
        family, account, seat, expiry, used_at, _ = row
        if used_at is not None:
            # Commit revocation before returning a replay error.
            revoke(db,family)
        else:
            active = db.execute("""SELECT 1 FROM account_members m JOIN security_users u ON u.user_id=m.user_id
              JOIN workspace_meta w ON w.business_id=m.business_id WHERE m.account_id=? AND m.user_id=?
              AND u.active=1 AND w.kind='personal' AND w.archived_at IS NULL""",(account,seat)).fetchone()
            if not active:
                sec.fail(401,'WORKSPACE_ACCESS_CHANGED')
            db.execute('UPDATE app_refresh_tokens SET used_at=? WHERE token_hash=?',(sec.now(),digest(body.refresh_token)))
            return dict(mint(db,seat,account,family,expiry),simulation_only=True)
    sec.fail(401,'REFRESH_ALREADY_USED')


class Logout(Contract):
    refresh_token: str | None = Field(default=None,min_length=32,max_length=128)


def logout(db, request, body):
    access = request.headers.get('authorization','').split()
    with sec.transaction(db):
        family = family_for(db,digest(access[1])) if len(access)==2 and access[0].lower()=='bearer' else None
        if body and body.refresh_token:
            row = db.execute('SELECT family_id FROM app_refresh_tokens WHERE token_hash=?',(digest(body.refresh_token),)).fetchone()
            if row:
                if family and family != row[0]:sec.fail(409,'DEVICE_MISMATCH')
                family = row[0]
        if family:revoke(db,family)


@router.get('/account/app-devices')
def devices(actor=Depends(principal), db=Depends(db_connection)):
    from api.app.profile import account_of
    account=account_of(db,actor)
    rows=db.execute('SELECT family_id,device_label,expires_at FROM app_refresh_tokens WHERE account_id=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at>? ORDER BY expires_at DESC',
                    (account,sec.now())).fetchall()
    current=family_for(db,actor.token_hash)
    return dict(devices=[dict(family_id=f,device_label=l,expires_at=e,current=f==current) for f,l,e in rows],simulation_only=True)


@router.post('/account/app-devices/{family_id}/revoke')
def revoke_device(family_id: str, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.profile import account_of
    with sec.transaction(db):
        account=account_of(db,sec.recheck(db,actor))
        if not db.execute('SELECT 1 FROM app_refresh_tokens WHERE account_id=? AND family_id=?',(account,family_id)).fetchone():
            sec.fail(404,'DEVICE_NOT_FOUND')
        revoke(db,family_id)
    return dict(simulation_only=True)
