"""Synthetic wallet examples, ranking evaluation, accounting invariants and owner confirmations."""
from datetime import date, timedelta
import binascii
import sqlite3
import string

from hypothesis import given, settings, strategies as st

from api.app.accounts import today_bd
from api.app.ratelimit import limiter
from api.app.services.wallet import parse_payment, rank_matches
from api.tests.test_accounts import PASSWORD, signup, world
from api.tests.test_receivables import issue


# Ten synthetic message shapes cover Bangla and English, incoming and outgoing,
# balances, fees, transaction IDs and dates. Vary the values independently of parser configuration.
FORMATS = [
    ('bKash','bKash: You have received Tk {v} from Faria. TrxID {tx} on {day}. Balance Tk 3,500.', 'in'),
    ('Nagad','Nagad Cash In BDT {v} from Shop. Txn ID: {tx}. On {day}.', 'in'),
    ('Rocket','Rocket: Cash-in Tk. {v} from Customer, Trans ID {tx}. {day}', 'in'),
    ('বিকাশ','বিকাশ: {v} টাকা পেয়েছেন। ট্রানজেকশন আইডি: {tx}। {day}', 'in'),
    ('Nagad','Nagad: BDT {v} credited. Ref A1. Transaction No {tx}, Date {day}.','in'),
    ('Rocket','Rocket received ৳{v} from Shop; Trx ID {tx}; {day}. Fee Tk 0.','in'),
    ('বিকাশ','বিকাশ: {v} টাকা পাঠিয়েছেন। TrxID {tx}, {day}', 'out'),
    ('নগদ','নগদ: আপনার হিসাবে {v} টাকা জমা হয়েছে। Txn ID {tx}। {day}', 'in'),
    ('Rocket','Rocket: previous balance Tk 0. Received Tk {v}. New Balance Tk {v}. TrxID {tx} {day}', 'in'),
    ('bKash','bKash: You have sent BDT {v}. Fee BDT 0.00. Transaction ID {tx}. {day}', 'out'),
]


def test_40_synthetic_wallet_messages():
    when=date(2026,11,21)
    bn=str.maketrans('0123456789','০১২৩৪৫৬৭৮৯')
    for i,(name,template,direction) in enumerate(FORMATS):
        for j,v in enumerate(['7.00','128.36','1,501.70','50,000.01']):
            day=when-timedelta(days=i+j)
            txn=(f'20{i:04d}{j:04d}' if name=='Rocket' else f'XY{i:04d}{j:04d}')
            is_bn=name in {'বিকাশ','নগদ'}
            text=template.format(v=v.translate(bn) if is_bn else v,tx=txn,day=day.strftime('%d/%m/%Y') if j%2 else str(day))
            result=parse_payment(text,when)
            expected={'বিকাশ':'bkash','নগদ':'nagad'}.get(name,name.lower())
            assert (result.provider,result.txn_id,result.direction,result.occurred_on)==(expected,txn,direction,day)
            assert result.amount_minor==int(v.replace(',','').replace('.',''))
            assert not result.flags


def test_100_synthetic_rankings_with_similar_candidates():
    day=date(2026,11,21)
    for trial in range(100):
        customers=[];debts=[]
        for i in range(7):
            name=f'Bazar {string.ascii_uppercase[(trial+i)%26]} Corner'
            customers.append(dict(id=str(i),name=name,phone=f'+8801700{trial:03d}{i:03d}'))
            debts.append(dict(id=f'r{i}',customer_id=str(i),number=f'N{i}',issued_on='2026-10-01',
                              open_minor=11000+(i%3)*500,due_on=str(day+timedelta(days=(trial+i)%9))))
        target=(trial*3)%7
        party=customers[target]['phone'] if trial%3==0 else customers[target]['name']
        amount=debts[target]['open_minor'] if trial%2 else 3500
        matches=rank_matches({'customers':customers,'receivables':debts},party,amount,day)
        assert matches[0]['receivable_id']==f'r{target}'


def share(c, text, **fields):
    return c.post('/api/payments/share',json={'text':text,**fields})


def message(amount, ref):
    return f'bKash: You received Tk {amount} from Meghna. TrxID {ref}. {today_bd()}'


def test_wallet_review_and_atomic_confirmation(world):
    c,path=world;owner=signup(c,'wallet_owner')
    _,debt,_=issue(c)
    raw=message('47.00','XABC123456')
    posted=share(c,raw)
    assert posted.status_code==201,posted.text
    event=posted.json()
    assert event['status']=='new' and event['candidates'][0]['receivable_id']==debt
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0
    assert share(c,raw).status_code==409
    assert share(c,raw.replace('received','received ')).status_code==409
    assert share(c,raw.replace('47.00','23.00')).status_code==409
    assert share(c,message('52.00','TR00000003'),expected_business_id='changed').status_code==409
    assert share(c,message('52.00','TR00000003'),expected_user_id='changed').status_code==409
    route=f"/api/payments/{event['id']}/confirm"
    decision=dict(receivable_id=debt,checked_wallet=True)
    assert c.post(route,json=dict(decision,checked_wallet=False)).status_code==422
    saved=c.post(route,json=decision)
    assert saved.status_code==200,saved.text
    assert c.post(route,json=decision).json()['replayed']
    assert c.post(route,json=dict(decision,receivable_id=None)).status_code==409
    bundle=c.get('/api/workspace').json()
    assert bundle['snapshot']['balance_minor']==4700 and bundle['month']['closing_balance_minor']==12000
    assert c.get('/api/payments/feed').json()['events'][0]['status']=='confirmed'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT cash_record_id,payment_event_id FROM receivable_settlements').fetchone()==(saved.json()['cash_record_id'],event['id'])
        assert db.execute('SELECT count(*) FROM postings').fetchone()[0]==0
        assert raw not in str(db.execute('SELECT * FROM payment_events').fetchall())
        assert 'Meghna' not in str(db.execute('SELECT * FROM payment_events').fetchall())
    unmatched=share(c,message('12.00','FALLBACK01')).json()
    assert c.post('/api/payments/'+unmatched['id']+'/confirm',json=dict(checked_wallet=True)).status_code==200
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==5900
    assert share(c,'OTP 123456. Received Tk 12').status_code==422
    invite=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'wallet_other')
    assert c.get('/api/payments/feed').json()['events']==[]
    assert c.post(route,json=decision).status_code==404
    assert c.post('/api/account/join',json={'code':invite}).status_code==200
    assert c.get('/api/payments/feed').status_code==403
    assert share(c,raw).status_code==403 and c.post(route,json=decision).status_code==403


def test_balance_flags_and_outgoing(world):
    c,_=world;signup(c,'wallet_flags')
    for raw in ['Rocket: received Tk 87. Old balance Tk 100. New balance Tk 10. TrxID BK473AE0',
                'bKash: sent Tk 50. TrxID DGGA8532',
                'Nagad: Request pending. Received Tk 48. TrxID TXA93866',
                'bKash: Tk 58 was not received. TrxID BK473AE001',
                "Nagad: You haven’t received Tk 30. TrxID HELLO123",
                'Rocket: attempted payment. Received Tk 45? TrxID 1234567890']:
        shared=share(c,raw)
        assert shared.status_code==201,shared.text
        key=shared.json()['id'];url=f'/api/payments/{key}'
        assert c.post(url+'/confirm',json=dict(checked_wallet=True)).status_code==409
        assert c.post(url+'/dismiss',json={}).status_code==200
    assert share(c,'বিকাশ: আপনি ১৬ টাকা পাননি।').status_code==422
    uncertain=share(c,'You received Tk 15.').json()
    assert uncertain['provider']=='unknown' and 'UNKNOWN_PROVIDER' in uncertain['flags']
    invalid_id=share(c,'Rocket: received Tk 70. TxnID ABABC').json()
    assert 'CHECK_TRANSACTION_ID' in invalid_id['flags']
    assert share(c,'Rocket: your balance is Tk 900.').status_code==422
    assert share(c,'bKash: received cash. Balance Tk 900.').status_code==422
    assert share(c,'bKash received Tk 50 and Tk 98').status_code==422


def test_receipt_plus_outstanding_is_invariant(world):
    c,_=world;signup(c,'wallet_properties')
    @settings(max_examples=25,deadline=None,derandomize=True)
    @given(st.integers(min_value=1,max_value=1000000),st.integers(min_value=1,max_value=1000000))
    def verify(one,two):
        limiter.reset()
        _,debt,_=issue(c,one+two)
        data=c.get('/api/workspace').json()
        def total(data):
            open_minor=sum(r['open_minor'] for r in c.get('/api/workspace/receivables').json()['receivables'])
            return data['snapshot']['balance_minor']+open_minor
        old=total(data)
        cents=f'{one//100}.{one%100:02d}'
        event=share(c,message(cents,debt[-12:])).json()
        response=c.post('/api/payments/'+event['id']+'/confirm',json=dict(receivable_id=debt,checked_wallet=True))
        assert response.status_code==200,response.text
        assert total(c.get('/api/workspace').json())==old
    verify()


def test_reauthenticated_profile_and_member_notice(world):
    c,path=world;owner=signup(c,'wallet_profile')
    qr='00020101021126160003TST0105M123453030505802BD6304'
    qr+=f'{binascii.crc_hqx(qr.encode(),0xffff):04X}'
    assert c.post('/api/payments/profile/preview-qr',json=dict(qr_payload=qr)).json()['merchant_id']=='M1234'
    invite=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'wallet_profile_view')
    assert c.post('/api/account/join',json={'code':invite}).status_code==200
    c.post('/api/auth/login',json={'username':'wallet_profile','password':PASSWORD})
    params=dict(merchants=[dict(provider='bkash',number='01700000000')],qr_payload=qr,merchant_id='M1234',expected_version=c.get('/api/payments/profile').json()['version'])
    assert c.post('/api/payments/profile',json=dict(params,password='invalid')).status_code==403
    assert c.post('/api/payments/profile',json=dict(params,password=PASSWORD,merchant_id='OTHER')).status_code==422
    for provider,number in [('bkash','017000000000'),('nagad','017000000000'),('rocket','01700000000')]:
        assert c.post('/api/payments/profile',json=dict(params,password=PASSWORD,merchants=[dict(provider=provider,number=number)])).status_code==422
    saved=c.post('/api/payments/profile',json=dict(params,password=PASSWORD))
    assert saved.status_code==200,saved.text
    assert c.post('/api/payments/profile',json=dict(params,password=PASSWORD)).status_code==409
    assert c.get('/api/payments/profile').json()['merchants']==params['merchants']
    c.post('/api/auth/login',json={'username':'wallet_profile_view','password':PASSWORD})
    assert c.post('/api/account/switch',json=dict(business_id=owner['business_id'])).status_code==200
    assert c.get('/api/payments/profile').status_code==403
    with sqlite3.connect(path) as db:
        rows=db.execute('SELECT payload_json FROM notification_jobs WHERE business_id=?',(owner['business_id'],)).fetchall()
        notices=[raw for raw, in rows if 'payment_profile_changed' in raw]
        assert len(notices)==2
        assert all('01700000000' not in raw and qr not in raw for raw in notices)


def test_android_app_marks_payments_it_read_from_sms(world):
    c, _ = world
    signup(c, 'sms_reader')
    text = f'bKash: You received Tk 750 from Meghna. TrxID SMS9A1B2C3. {today_bd()}'
    assert c.post('/api/payments/share', json={'text': text, 'source': 'telepathy'}).status_code == 422
    added = c.post('/api/payments/share', json={'text': text, 'source': 'sms'})
    assert added.status_code == 201 and added.json()['amount_minor'] == 75_000
    assert c.post('/api/payments/share', json={'text': text, 'source': 'sms'}).status_code == 409  # read again: skipped
    from contextlib import closing
    from api.app.database import connect
    with closing(connect()) as db:
        assert db.execute("SELECT source FROM payment_events WHERE txn_id='SMS9A1B2C3'").fetchone()[0] == 'sms'
