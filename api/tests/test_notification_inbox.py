"""In-app scheduling, notification ownership, and the Turso claim result contract."""
from datetime import date, datetime, time, timedelta
import json
import sqlite3

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.notifications.routes import signature
from api.app.notifications.scheduler import tick_business
from api.app.notifications.service import BD, claim, deliver_after, dispatch, enqueue
from api.app.services.radar import should_alert
from api.tests.test_accounts import PASSWORD, signup, world
from api.tests.test_manual_inputs import record
from api.tests.test_receivables import post
from api.tests.test_remote_database import client, ok


def local(day, hour):
    return int(datetime.combine(day,time(hour),BD).timestamp())


def test_one_month_no_duplicates_quiet_delivery_and_stop_after_payment(world):
    c, path = world
    owner = signup(c,'notify_owner')
    today = today_bd()
    customer = post(c,'customers',name='Fictional customer').json()['id']
    for i in range(2):
        assert post(c,'receivables',customer_id=customer,currency='BDT',amount_minor=3000,
                    issued_on=str(today),due_on=str(today),number=str(i)).status_code == 201
    assert record(c,kind='invoice',vendor_id='sample-vendor',invoice_number='INV-1',due_on=str(today+timedelta(days=10)))[0].status_code == 201
    with sqlite3.connect(path) as db:
        for day_offset in range(30):
            day = today + timedelta(days=day_offset)
            # Two ticks land in quiet hours. The 08:00 tick must deliver at most once.
            for hour in (1,8,9,23):
                at=local(day,hour)
                assert tick_business(db, owner['business_id'], at)
                assert not tick_business(db, owner['business_id'], at)
            if day_offset == 10:
                for debt in c.get('/api/workspace/receivables').json()['receivables']:
                    assert post(c,'receivables/'+debt['id']+'/settlements',received_on=str(today),amount_minor=3000).status_code == 201
        rows = db.execute("SELECT payload_json,sent_at FROM notification_jobs WHERE status='sent'").fetchall()
        received={}
        for raw, at in rows:
            when=datetime.fromtimestamp(at,BD)
            assert 8 <= when.hour < 21
            kind=json.loads(raw)['kind']
            received.setdefault(kind,[]).append((when.date()-today).days)
            assert 'Fictional' not in raw and '3000' not in raw
        assert sorted(received['customer_overdue']) == [1,4,7,10]
        assert sorted(received['collections_review']) == list(range(1,11))
        assert sorted(received['bill_due']) == [3,7,9,10]
        assert sorted(received['morning_briefing']) == list(range(30))


def test_preferences_revocation_and_private_inbox(world):
    c,path=world
    owner=signup(c,'private_notify')
    user,business=owner['user_id'],owner['business_id']
    assert c.post('/api/notifications/preferences',json=dict(enabled=True,quiet_start_min=1260,quiet_end_min=480)).status_code == 200
    with sqlite3.connect(path) as db:
        at=local(today_bd(),23)
        with sec.transaction(db):
            enqueue(db,business,'payment_shared','ref',at=at)
            enqueue(db,business,'new_device','device',at=at,recipients=[user])
        assert db.execute("SELECT count(*) FROM notification_jobs WHERE status='queued'").fetchone()[0] == 1
    items=c.get('/api/notifications').json()['items']
    assert len(items)==1 and items[0]['kind']=='new_device' and 'record' not in items[0]
    endpoint='/api/notifications/'+items[0]['id']+'/read'
    assert c.post(endpoint,json={}).status_code == 200
    assert c.get('/api/notifications').json()['items'][0]['read']
    assert c.post('/api/notifications/preferences',json=dict(enabled=False,quiet_start_min=1260,quiet_end_min=480)).status_code == 200
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM notification_jobs WHERE status='cancelled'").fetchone()[0]==1
    signup(c,'other_notify')
    assert c.get('/api/notifications').json()['items'] == []
    assert c.post(endpoint,json={}).status_code == 404


def test_resolved_cash_breach_is_not_delivered_after_quiet_hours(world):
    c, path = world
    business = signup(c, 'resolved_radar')['business_id']
    today = today_bd()
    at = local(today, 23)
    morning = local(today + timedelta(days=1), 8)
    with sqlite3.connect(path) as db, sec.transaction(db):
        assert should_alert(db, business, dict(level='alert', breach_on=str(today), shortfall_minor=100), at)
        enqueue(db, business, 'cash_breach', str(today), at=at)
        job = db.execute("SELECT id FROM notification_jobs WHERE status='queued' AND channel='inapp'").fetchone()[0]
        assert not should_alert(db, business, dict(level='clear', breach_on=None, shortfall_minor=0), morning)
        dispatch(db, job, morning)
        assert db.execute('SELECT status FROM notification_jobs WHERE id=?', (job,)).fetchone()[0] == 'cancelled'


def test_cash_radar_sends_one_severity_update_on_the_same_day(world):
    c, path = world
    business = signup(c, 'severity_update')['business_id']
    today = today_bd()
    due = (today + timedelta(days=2)).isoformat()
    with sqlite3.connect(path) as db:
        for number, minor, hour in [('FIRST', 10000, 9), ('SECOND', 5000, 10)]:
            response, _ = record(c, kind='invoice', vendor_id='fixture-supplier', invoice_number=number,
                                 due_on=due, amount_minor=minor)
            assert response.status_code == 201, response.text
            assert tick_business(db, business, local(today, hour))
        assert tick_business(db, business, local(today, 11))
        alerts = db.execute("""SELECT status FROM notification_jobs WHERE business_id=? AND channel='inapp'
                              AND json_extract(payload_json,'$.kind')='cash_breach'""", (business,)).fetchall()
    assert [row[0] for row in alerts] == ['sent', 'sent']

def test_remote_claim_uses_returning_and_quiet_window_wrap():
    seen=[]
    def handle(request):
        sql=json.loads(request.content)['requests'][0].get('stmt',{}).get('sql','')
        if 'RETURNING' in sql:
            seen.append(sql)
            return ok(request,rows=[[{'type':'text','value':'job'}]] if len(seen)==1 else [])
        return ok(request)
    with client(handle) as db:
        assert claim(db,'job',1000)
        assert not claim(db,'job',1001)
    assert len(seen)==2
    day=today_bd()
    assert deliver_after(local(day,23),1260,480)==local(day+timedelta(days=1),8)
    assert deliver_after(local(day,5),1260,480)==local(day,8)
    assert deliver_after(local(day,10),540,720)==local(day,12)
    assert deliver_after(local(day,10),600,600)==local(day,10)


def test_authenticated_scheduler_and_new_browser_notice(world,monkeypatch):
    c,_=world
    signup(c,'scheduler_notify')
    secret='s'*40
    monkeypatch.setenv('CAPZORYX_TICK_SECRET', secret)
    tick='/api/internal/tick'
    assert c.post(tick,json={}).status_code==401
    for age,status in [(301,401),(0,200)]:
        ts=str(sec.now()-age)
        result=c.post(tick,json={},headers={'X-Capzoryx-Timestamp':ts,'X-Capzoryx-Signature':signature(secret,ts)})
        assert result.status_code==status
    # A freshly authenticated browser is an in-app security alert, including during quiet hours.
    c.cookies.clear()
    assert c.post('/api/auth/login',json={'username':'scheduler_notify','password':PASSWORD}).status_code==200
    assert [item['kind'] for item in c.get('/api/notifications').json()['items']]==['new_device']
    assert c.post('/api/auth/logout',json={}).status_code==200
    assert c.post('/api/auth/login',json={'username':'scheduler_notify','password':PASSWORD}).status_code==200
    assert len(c.get('/api/notifications').json()['items'])==1

def test_season_planning_uses_dhaka_dates_and_one_alert_per_event(world,monkeypatch):
    c,path=world
    owner=signup(c,'season_owner')
    assert record(c)[0].status_code==201
    monkeypatch.setenv('CAPZORYX_EID_PLANNING_DATES','2030-04-20')
    with sqlite3.connect(path) as db:
        for day in (date(2030,3,15),date(2030,3,21)):
            for hour in (9,12):
                tick_business(db,owner['business_id'],local(day,hour))
        alerts=[json.loads(raw)['kind'] for raw, in db.execute(
            "SELECT payload_json FROM notification_jobs WHERE channel='inapp' AND status='sent'")]
    assert alerts.count('boishakh_planning')==1
    assert alerts.count('eid_planning')==1
