"""Owner-managed merchant details, protected by account re-authentication."""
import binascii
import hashlib
import json
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator

from api.app import security as sec
from api.app.contracts import Contract
from api.app.notifications.service import enqueue
from api.app.profile import Exact, account_of, check_password, check_second_factor
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/payments')


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS payment_profiles(business_id TEXT PRIMARY KEY, qr_payload_hash TEXT,
      qr_image TEXT, merchant_numbers_json TEXT NOT NULL, key_tag TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL);
    """)


def tlv(raw):
    fields={}
    index=0
    while index<len(raw):
        header=raw[index:index+4]
        if len(header)!=4 or not header.isdigit():
            raise ValueError
        tag=header[:2].decode()
        end=index+4+int(header[2:])
        if end>len(raw) or tag in fields:
            raise ValueError
        fields[tag]=raw[index+4:end]
        index=end
    return fields


def decode_merchant(payload):
    """Read a supported static EMV merchant-presented QR in BDT, checking its CRC and nested merchant account ID."""
    try:
        raw=payload.encode()
        fields=tlv(raw)
        if fields.get('00')!=b'01' or fields.get('58')!=b'BD' or fields.get('53')!=b'050':
            raise ValueError
        if fields.get('01',b'11')!=b'11' or '54' in fields:
            raise ValueError
        if not raw.endswith(b'6304'+fields.get('63',b'')) or fields.get('63')!=f'{binascii.crc_hqx(raw[:-4],0xffff):04X}'.encode():
            raise ValueError
        accounts=[]
        for tag,value in fields.items():
            if '26'<=tag<='51':
                nested=tlv(value)
                identity=nested.get('01',b'').decode()
                if nested.get('00') and re.fullmatch(r'[A-Za-z0-9._-]{3,50}',identity):
                    accounts.append(identity)
        if len(set(accounts))!=1:
            raise ValueError
        return accounts[0]
    except (ValueError,UnicodeError):
        sec.fail(422,'UNSUPPORTED_MERCHANT_QR')


class QR(Contract):
    qr_payload: str = Field(min_length=1,max_length=1024)


class Merchant(Contract):
    provider: Literal['bkash','nagad','rocket']
    number: str = Field(pattern=r'^(?:\+?88)?01[3-9][0-9]{8}[0-9]?$')

    @model_validator(mode='after')
    def validate_wallet_length(self):
        national=re.sub(r'^\+?88','',self.number)
        required=12 if self.provider=='rocket' else 11
        if len(national)!=required:
            raise ValueError('Check the merchant number length for this wallet.')
        return self


class ProfileChange(Exact):
    password: str | None = Field(default=None,min_length=1,max_length=128)
    totp: str | None = Field(default=None,pattern=r'^[0-9]{6}$')
    merchants: list[Merchant] = Field(max_length=3)
    qr_payload: str | None = Field(default=None,min_length=1,max_length=1024)
    merchant_id: str | None = Field(default=None,min_length=3,max_length=50)
    expected_version: Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]


def version(row, business):
    return sec.signature('wallet-profile-version', [business, (row['merchant_numbers_json'] if row else None),
                                                     (row['qr_payload_hash'] if row else None)])


@router.post('/profile/preview-qr')
def preview(body: QR, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db,actor):
        return dict(merchant_id=decode_merchant(body.qr_payload),simulation_only=True)


@router.get('/profile')
def get_profile(actor=Depends(principal),db=Depends(db_connection)):
    with owner_transaction(db,actor) as current:
        business=current.business_id
        row=db.execute('SELECT * FROM payment_profiles WHERE business_id=?',(business,)).fetchone()
        profile={'merchants':[],'qr_payload':None,'merchant_id':None}
        key=customer_key(db,business) if row else None
        if key and key_tag(key)==row['key_tag']:
            profile['merchants']=json.loads(unseal(key,row['merchant_numbers_json'],business+'/merchant-numbers'))
            if row['qr_image']:
                payload=unseal(key,row['qr_image'],business+'/merchant-qr').decode()
                profile.update(qr_payload=payload,merchant_id=decode_merchant(payload))
        return dict(profile,version=version(row,business),simulation_only=True)


@router.post('/profile')
def change_profile(body: ProfileChange,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':
        sec.fail(403,'OWNER_REQUIRED')
    if bool(body.password)==bool(body.totp):
        sec.fail(422,'REAUTHENTICATION_REQUIRED')
    account=account_of(db,actor)
    if body.totp and not check_second_factor(db,account,body.totp):
        sec.fail(403,'INVALID_CODE')
    with owner_transaction(db,actor) as current:
        business=current.business_id
        if body.password:
            try:
                check_password(db,account,body.password)
            except sec.PolicyError as exc:
                if exc.code=='CURRENT_PASSWORD_INCORRECT':
                    sec.fail(403,exc.code)
                raise
        row=db.execute('SELECT * FROM payment_profiles WHERE business_id=?',(business,)).fetchone()
        if version(row,business)!=body.expected_version:
            sec.fail(409,'PAYMENT_PROFILE_CHANGED')
        merchants=[m.model_dump() for m in body.merchants]
        if len({m.provider for m in body.merchants})!=len(merchants):
            sec.fail(422,'DUPLICATE_WALLET_PROVIDER')
        if (decode_merchant(body.qr_payload) if body.qr_payload else None)!=body.merchant_id:
            sec.fail(422,'MERCHANT_ID_MISMATCH')
        key=customer_key(db,business,create=True)
        stamp=sec.now()
        qr=seal(key,body.qr_payload.encode(),business+'/merchant-qr') if body.qr_payload else None
        digest=hashlib.sha256(body.qr_payload.encode()).hexdigest() if qr else None
        numbers=seal(key,json.dumps(merchants).encode(),business+'/merchant-numbers')
        db.execute("""INSERT INTO payment_profiles VALUES(?,?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
                   qr_payload_hash=excluded.qr_payload_hash,qr_image=excluded.qr_image,merchant_numbers_json=excluded.merchant_numbers_json,
                   key_tag=excluded.key_tag,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                   (business,digest,qr,numbers,key_tag(key),current.user_id,stamp))
        notice='payprofile-'+uuid.uuid4().hex
        enqueue(db,business,'payment_profile_changed',notice)
        sec.audit(db,current,notice,'PAYMENT_PROFILE_CHANGED',dict(qr_payload_hash=digest,merchant_count=len(merchants)))
    return dict(simulation_only=True)
