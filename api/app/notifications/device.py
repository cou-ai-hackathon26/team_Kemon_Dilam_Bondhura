"""Non-authenticating browser marker for new-device sign-in notifications."""
import os
import re
import secrets

from api.app import security as sec
from api.app.notifications.service import enqueue


def observe(db, user, account, request, response):
    # Native sign-ins create a new refresh family and never issue a browser cookie.
    pair = getattr(request.state,'app_tokens',None)
    if pair:
        from api.app.app_sessions import digest, family_for
        marker = family_for(db,digest(pair['access_token']))
    else:
        marker = request.cookies.get('capzoryx_device', '')
    if not re.fullmatch(r'[a-f0-9]{32}', marker):
        marker = secrets.token_hex(16)
        secure = bool(os.getenv('RENDER') or os.getenv('VERCEL') or os.getenv('SAFELEDGER_COOKIE_SECURE')=='1')
        response.set_cookie('capzoryx_device',marker,max_age=31536000,httponly=True,secure=secure,samesite='lax',path='/')
    identity = sec.signature('notice-device', [account, marker])
    known = db.execute('SELECT 1 FROM known_devices WHERE account_id=? LIMIT 1',(account,)).fetchone()
    new = db.execute('INSERT OR IGNORE INTO known_devices VALUES (?,?,?) RETURNING device_hash',
                     (account, identity, sec.now())).fetchone()
    if known and new:
        business=db.execute('SELECT business_id FROM security_users WHERE user_id=?',(user,)).fetchone()[0]
        enqueue(db,business,'new_device',identity,recipients=[user])
