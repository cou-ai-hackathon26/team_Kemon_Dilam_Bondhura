"""Versioned MACs and sealed fields, with exact verification of legacy records."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac
import json
import os
import re

KEY_ID = re.compile(r'[A-Za-z0-9_-]{1,32}\Z')
HEX_KEY = re.compile(r'[0-9a-f]{64}\Z')
LEGACY = 'legacy'


class KeyUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Ring:
    active: str
    keys: dict[str,bytes]
    versioned: bool

    def open(self, field):
        if not isinstance(field,str):
            raise KeyUnavailable('Invalid stored key version')
        version, sep, rest = field.partition('.')
        if not sep:
            return self.keys[LEGACY], None, field
        kid, sep, value = rest.partition('.')
        if version!='k1' or not sep or kid not in self.keys or not value:
            raise KeyUnavailable('Unrecognized stored key version')
        return self.keys[kid], kid, value

    def label(self, value):
        return 'k1.'+self.active+'.'+value if self.versioned else value

    def current(self):
        return self.keys[self.active], self.active if self.versioned else None


@lru_cache(maxsize=1)
def _load(legacy, active, encoded):
    try:
        if not HEX_KEY.fullmatch(legacy):
            raise KeyUnavailable()
        keys = {LEGACY:bytes.fromhex(legacy)}
        if not active and not encoded:
            return Ring(LEGACY,keys,False)
        supplied = json.loads(encoded)
        if (not isinstance(supplied,dict) or not (1<=len(supplied)<=32)
                or any(not KEY_ID.fullmatch(k) or k==LEGACY or not isinstance(v,str) or not HEX_KEY.fullmatch(v)
                       for k,v in supplied.items())):
            raise KeyUnavailable()
        keys.update({k:bytes.fromhex(v) for k,v in supplied.items()})
        if active not in keys or len(set(keys.values()))!=len(keys):
            raise KeyUnavailable()
        return Ring(active,keys,active!=LEGACY)
    except (ValueError,TypeError,AttributeError):
        raise KeyUnavailable('Signing key configuration is invalid') from None


def get():
    return _load(os.getenv('SAFELEDGER_SIGNING_KEY',''),
                 os.getenv('SAFELEDGER_ACTIVE_SIGNING_KEY_ID',''),
                 os.getenv('SAFELEDGER_SIGNING_KEYS',''))


def domain(message, kid):
    return message if kid is None else b'capzoryx-key-v1:'+kid.encode()+b':'+message


def mac(key, kid, message):
    return hmac.new(key,domain(message,kid),hashlib.sha256).digest()


def sign(message):
    ring = get()
    key, kid = ring.current()
    return ring.label(mac(key,kid,message).hex())


def verify(stored, message):
    ring = get()
    try:
        key,kid,value = ring.open(stored)
        return value.isascii() and hmac.compare_digest(value,mac(key,kid,message).hex())
    except KeyUnavailable:
        return False
