"""Synthetic members and fake mail transport only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

import pytest
from api.app import mailer,security as sec
from api.app.accounts import today_bd
from api.app.notifications import email_alerts as alerts
from api.app.notifications.scheduler import tick_business
from api.tests.test_accounts import signup,world
from api.tests.test_manual_inputs import record
from api.tests.test_notification_inbox import local

ROUTE='/api/notifications/bill-email'


@pytest.fixture
def transport(monkeypatch):
    sent=[]
    monkeypatch.setenv('SAFELEDGER_PUBLIC_URL','https://synthetic.example.invalid')
    monkeypatch.setattr(mailer,'provider',lambda:'relay')
    monkeypatch.setattr(mailer,'reserve',lambda target,*,bulk:bulk)
    monkeypatch.setattr(mailer,'send',lambda target,subject,text:sent.append((target,subject,text)) is None)
    return sent


def verify(db,actor,address):
    account=db.execute('SELECT account_id FROM account_members WHERE user_id=?',(actor['user_id'],)).fetchone()[0]
    db.execute('INSERT INTO account_emails VALUES(?,?,1)',(account,address))


def preference(actor,enabled=True):
    return dict(enabled=enabled,expected_business_id=actor['business_id'],expected_user_id=actor['user_id'])


def bill(c,days=0,number='PRIVATE-BILL'):
    return record(c,kind='invoice',effective_on=str(today_bd()),vendor_id='private-vendor',invoice_number=number,
                  due_on=str(today_bd()+timedelta(days=days)),amount_minor=389241)[0]


def test_opt_in_quiet_dedup_and_unsubscribe_without_login(world,transport):
    c,path=world;actor=signup(c,'mail_digest_owner')
    assert not c.get(ROUTE).json()['enabled']
    assert c.post(ROUTE,json=preference(actor)).status_code==403
    with sqlite3.connect(path) as db:
        verify(db,actor,'one@example.invalid')
    assert c.post(ROUTE,json=dict(preference(actor),expected_user_id='other')).status_code==409
    assert c.post(ROUTE,json=preference(actor)).json()['enabled']
    assert bill(c).status_code==201 and bill(c,1,'PRIVATE-BILL-TWO').status_code==201
    business=actor['business_id']
    with sqlite3.connect(path) as db:
        tick_business(db,business,local(today_bd(),5))
        assert not transport
        payload=db.execute("SELECT payload_json FROM notification_jobs WHERE channel='bill_email'").fetchone()[0]
        assert '389241' not in payload and 'PRIVATE' not in payload and '@' not in payload
    def duplicate_tick(_):
        with sqlite3.connect(path) as db:
            tick_business(db,business,local(today_bd(),8))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(duplicate_tick,range(2)))
    assert len(transport)==1
    target,subject,text=transport[0]
    assert target=='one@example.invalid' and '2 bills need' in text
    assert all(v not in subject+text for v in ('PRIVATE','389241','private-vendor',target))
    token=text.split('#')[-1]
    assert c.get(ROUTE).json()['enabled']
    c.post('/api/auth/logout',json={}).raise_for_status()
    assert c.post(ROUTE+'/unsubscribe',json=dict(token='a'*32+'.'+'0'*64)).status_code==404
    for _ in range(2):
        assert c.post(ROUTE+'/unsubscribe',json=dict(token=token)).status_code==200
    with sqlite3.connect(path) as db:
        tick_business(db,business,local(today_bd()+timedelta(days=1),9))
        assert not db.execute('SELECT 1 FROM bill_email_subscriptions').fetchone()
    assert len(transport)==1


def test_no_provider_or_verified_email_or_consent_means_no_delivery(world,transport,monkeypatch):
    c,path=world;actor=signup(c,'mail_consent')
    with sqlite3.connect(path) as db:verify(db,actor,'verified@example.invalid')
    with monkeypatch.context() as disabled:
        disabled.setattr(mailer,'provider',lambda:None)
        assert not c.get(ROUTE).json()['configured']
        assert c.post(ROUTE,json=preference(actor)).status_code==503
    assert bill(c).status_code==201
    business=actor['business_id']
    with sqlite3.connect(path) as db:tick_business(db,business,local(today_bd(),7))
    assert not transport
    c.post(ROUTE,json=preference(actor)).raise_for_status()
    with sqlite3.connect(path) as db:
        tick_business(db,business,local(today_bd(),7)+1800)
        db.execute("UPDATE account_emails SET email='replacement@example.invalid',verified=1")
    assert not c.get(ROUTE).json()['enabled']
    with sqlite3.connect(path) as db:
        tick_business(db,business,local(today_bd(),9))
        assert db.execute("SELECT status FROM notification_jobs WHERE channel='bill_email'").fetchone()[0]=='cancelled'
    assert not transport


def test_shared_budget_window_rechecked_and_uncertain_delivery_never_retried(world,transport,monkeypatch):
    c,path=world;actor=signup(c,'mail_budget')
    business=actor['business_id'];day=today_bd()
    with sqlite3.connect(path) as db:verify(db,actor,'budget@example.invalid')
    c.post(ROUTE,json=preference(actor)).raise_for_status()
    assert bill(c).status_code==201
    with sqlite3.connect(path) as db:
        with monkeypatch.context() as depleted:
            depleted.setattr(mailer,'reserve',lambda *args,**kwargs:False)
            tick_business(db,business,local(day,9))
            assert db.execute("SELECT status FROM notification_jobs WHERE channel='bill_email'").fetchone()[0]=='queued'
        assert not transport
        c.post('/api/notifications/preferences',json=dict(enabled=False,quiet_start_min=600,quiet_end_min=660)).raise_for_status()
        tick_business(db,business,local(day,10))
        assert not transport
        def uncertain(*args):
            transport.append(args)
            return False
        monkeypatch.setattr(mailer,'send',uncertain)
        tick_business(db,business,local(day,11))
        tick_business(db,business,local(day,12))
        assert len(transport)==1
        assert tuple(db.execute("SELECT status,attempts FROM notification_jobs WHERE channel='bill_email'").fetchone())==('failed',1)


def test_batch_limit_and_disabling_verified_members(world,transport):
    c,path=world;owner=signup(c,'mail_team_owner');business=owner['business_id']
    assert bill(c).status_code==201
    codes=[c.post('/api/account/invitations',json={'role':'viewer'}).json()['code'] for _ in range(2)]
    users=[owner]
    for i in range(3):
        if i:
            signup(c,'mail_team_member_'+str(i))
            c.post('/api/account/join',json={'code':codes[i-1]}).raise_for_status()
            users.append(c.get('/api/session').json())
        with sqlite3.connect(path) as db:verify(db,users[i],f'seat{i}@example.invalid')
        c.post(ROUTE,json=preference(users[i])).raise_for_status()
    at=local(today_bd(),9)
    with sqlite3.connect(path) as db:
        tick_business(db,business,at)
        assert len(transport)==alerts.BATCH
        pending=db.execute("SELECT recipient_user_id FROM notification_jobs WHERE channel='bill_email' AND status='queued'").fetchone()[0]
        db.execute('UPDATE security_users SET active=0 WHERE user_id=?',(pending,))
        db.commit()
        alerts.deliver(db,business,at)
        assert len(transport)==alerts.BATCH
        assert not db.execute('SELECT 1 FROM bill_email_subscriptions WHERE user_id=?',(pending,)).fetchone()


def test_paid_before_delivery_and_old_link_after_resubscription(world,transport):
    c,path=world;actor=signup(c,'mail_paid')
    business,user=actor['business_id'],actor['user_id']
    with sqlite3.connect(path) as db:verify(db,actor,'paid@example.invalid')
    c.post(ROUTE,json=preference(actor)).raise_for_status()
    with sqlite3.connect(path) as db:
        identity=db.execute('SELECT id FROM bill_email_subscriptions WHERE user_id=?',(user,)).fetchone()[0]
    old=identity+'.'+sec.signature('bill-email-unsubscribe',identity)
    c.post(ROUTE,json=preference(actor,False)).raise_for_status()
    c.post(ROUTE,json=preference(actor)).raise_for_status()
    c.post(ROUTE+'/unsubscribe',json=dict(token=old)).raise_for_status()
    assert c.get(ROUTE).json()['enabled']
    # Recompute due bills when a digest wakes up; no bill can be included after settlement.
    assert record(c,amount_minor=100000)[0].status_code==201
    bill_id=record(c,kind='invoice',vendor_id='shop',invoice_number='SYN',due_on=str(today_bd()),amount_minor=1000,trust_vendor=True)[0].json()['source_id']
    with sqlite3.connect(path) as db:tick_business(db,business,local(today_bd(),2))
    proposal=c.post('/api/proposals',json=dict(invoice_id=bill_id)).json()
    code=c.post('/api/account/invitations',json={'role':'approver'}).json()['code']
    signup(c,'mail_paid_approver');c.post('/api/account/join',json=dict(code=code)).raise_for_status()
    c.post('/api/proposals/'+proposal['proposal_id']+'/approve',json=dict(proposal_hash=proposal['action_hash'],decision='approve')).raise_for_status()
    with sqlite3.connect(path) as db:
        tick_business(db,business,local(today_bd(),9))
        assert not db.execute("SELECT 1 FROM notification_jobs WHERE channel='bill_email' AND status<>'cancelled'").fetchone()
    assert not transport
