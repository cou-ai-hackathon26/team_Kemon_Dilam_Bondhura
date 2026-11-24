"""Time-based one-time passwords (RFC 6238, SHA-1, 6 digits, 30 s) with sealed secrets and one-use backup codes."""
import base64
import hashlib
import hmac
from api.app import keyring
import secrets
import struct
import time

STEP_SECONDS = 30
ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _stream(key, nonce, length):
    out, counter = b'', 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def seal(secret):
    """Encrypt-then-MAC with a key derived from the server signing key; the database alone cannot reveal secrets."""
    ring = keyring.get()
    secret_key, kid = ring.current()
    key = keyring.mac(secret_key,kid,b'safeledger-mfa-seal-v1')
    nonce, plain = secrets.token_bytes(12), secret.encode()
    cipher = bytes(a ^ b for a, b in zip(plain, _stream(key, nonce, len(plain))))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    return ring.label((nonce + cipher + tag).hex())


def unseal(blob):
    secret_key, kid, encoded = keyring.get().open(blob)
    raw = bytes.fromhex(encoded)
    nonce, cipher, tag = raw[:12], raw[12:-16], raw[-16:]
    key = keyring.mac(secret_key,kid,b'safeledger-mfa-seal-v1')
    if not hmac.compare_digest(tag, hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]):raise ValueError('Sealed secret failed verification')
    return bytes(a ^ b for a, b in zip(cipher, _stream(key, nonce, len(cipher)))).decode()


def code_at(secret, step):
    padded = secret + '=' * (-len(secret) % 8)
    digest = hmac.new(base64.b32decode(padded, casefold=True), struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return '%06d' % ((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6)


def verify(secret, code, last_step=0, now=None):
    """Return the matched time step, or None. Accepts one step of clock drift; a step can be used only once."""
    code = ''.join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:return None
    current = int((now if now is not None else time.time()) // STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):return step
    return None


def provisioning_uri(secret, username):
    return f'otpauth://totp/Capzoryx%20Copilot:{username}?secret={secret}&issuer=Capzoryx%20Copilot&algorithm=SHA1&digits=6&period={STEP_SECONDS}'


def new_backup_codes(count=10):
    return [''.join(secrets.choice(ALPHABET) for _ in range(4)) + '-' + ''.join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(count)]


def _backup_digest(key,kid,code):
    normal = ''.join(ch for ch in code.lower() if ch.isalnum())
    material = keyring.mac(key,kid,b'safeledger-mfa-backup-v1')
    return hmac.new(material, normal.encode(), hashlib.sha256).hexdigest()


def hash_backup(code):
    ring = keyring.get()
    key,kid = ring.current()
    return ring.label(_backup_digest(key,kid,code))


def matches_backup(stored, code):
    try:
        key,kid,value = keyring.get().open(stored)
        return value.isascii() and hmac.compare_digest(value, _backup_digest(key,kid,code))
    except keyring.KeyUnavailable:
        return False
