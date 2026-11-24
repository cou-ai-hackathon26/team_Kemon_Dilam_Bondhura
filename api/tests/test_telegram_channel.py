import json
import sqlite3
import httpx
from api.app import security as sec
from api.app.notifications import telegram
from api.tests.test_accounts import signup,world
from api.tests.test_bill_email import bill
from api.app.ratelimit import limiter


def test_link_requires_user_confirmation_and_replays_never_duplicate(world,monkeypatch):
    c,path=world
    agent=signup(c,'channelowner');business=agent['business_id']
    base='/api/channels/telegram'
    assert c.get(base+'/link').json()['configured'] is False
    assert c.post(base+'/link-code',json={}).status_code==503
    secret='w'*40
    monkeypatch.setenv('SAFELEDGER_TELEGRAM_ENABLED','1')
    monkeypatch.setenv('SAFELEDGER_TELEGRAM_BOT_TOKEN','123456:'+'t'*32)
    monkeypatch.setenv('SAFELEDGER_TELEGRAM_WEBHOOK_SECRET',secret)
    monkeypatch.setenv('SAFELEDGER_TELEGRAM_BOT_USERNAME','test_bot')
    monkeypatch.setenv('SAFELEDGER_PUBLIC_URL','https://test.invalid')
    calls=[]
    def fake(request):
        calls.append(request.content.decode())
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(telegram,'TRANSPORT',httpx.MockTransport(fake))
    # Each action uses a local test client. No network call goes to Telegram.
    token=c.post(base+'/link-code',json={}).json()['code']
    event={'update_id':10,'message':{'text':'/start '+token,'date':sec.now(),'chat':{'id':12345,'type':'private'},
                                     'from':{'id':12345,'username':'test_member'}}}
    assert c.post(base,json=event).status_code==403
    headers={'X-Telegram-Bot-Api-Secret-Token':secret}
    for _ in range(2):
        assert c.post(base,json=event,headers=headers).status_code==200
    status=c.get(base+'/link').json()
    assert status['linked'] and status['chat_enabled'] and not status['records_enabled']
    assert len(calls)==1 and 'Connected' in calls[0]
    calls.clear()
    assert c.post(base+'/confirm',json={}).status_code==200  # idempotent for older pages
    c.post(base+'/chat',json={'enabled':False}).raise_for_status()  # reminders stay detail-free without book replies
    noon=int(telegram.datetime.now(telegram.BD).replace(hour=12,minute=0,second=0,microsecond=0).timestamp())
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM telegram_updates').fetchone()[0]==1
        for _ in range(2):
            with sec.transaction(db):telegram.queue(db,business,noon)
            telegram.deliver(db,business,noon)
        count=2 if telegram.datetime.fromtimestamp(noon,telegram.BD).weekday()==6 else 1
        assert len(calls)==count
        assert all('notification inbox' in call and 'balance' not in call for call in calls)
    bill(c).raise_for_status()
    with sqlite3.connect(path) as db:
        for _ in range(2):
            with sec.transaction(db):telegram.queue(db,business,noon,has_due_bills=True)
            telegram.deliver(db,business,noon)
        assert len(calls)==count+1
        assert 'A supplier bill is due soon' in calls[-1] and 'PRIVATE-BILL' not in calls[-1]
    event['update_id']=11;event['message']['text']='/stop'
    assert c.post(base,json=event,headers=headers).status_code==200
    assert not c.get(base+'/link').json()['linked']
    assert not c.get('/api/workspace').json()['events']


def test_private_book_replies_are_conversational_opt_in_and_never_write_without_save(world,monkeypatch):
    c,path=world
    signup(c,'telegram_books')
    base='/api/channels/telegram'
    secret='x'*40
    for suffix,value in {'ENABLED':'1','BOT_TOKEN':'123456:'+'t'*32,'WEBHOOK_SECRET':secret,'BOT_USERNAME':'books_test_bot'}.items():
        monkeypatch.setenv('SAFELEDGER_TELEGRAM_'+suffix,value)
    sent=[]
    def fake(request):
        body=json.loads(request.content)
        if 'text' in body:
            sent.append(body)
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(telegram,'TRANSPORT',httpx.MockTransport(fake))
    headers={'X-Telegram-Bot-Api-Secret-Token':secret}
    def incoming(update,text,chat=65432,kind='private'):
        message={'text':text,'chat':{'id':chat,'type':kind},'from':{'id':chat},'date':sec.now()}
        c.post(base,json={'update_id':update,'message':message},headers=headers).raise_for_status()
    def tap(update,data,chat=65432):
        query={'id':'cb'+str(update),'data':data,'from':{'id':chat},'message':{'chat':{'id':chat,'type':'private'}}}
        c.post(base,json={'update_id':update,'callback_query':query},headers=headers).raise_for_status()
    token=c.post(base+'/link-code',json={}).json()['code']
    incoming(101,'/start '+token)
    assert 'Connected' in sent[-1]['text'] and c.get(base+'/link').json()['chat_enabled']
    c.post(base+'/chat',json={'enabled':False}).raise_for_status()
    incoming(103,'/balance')
    assert 'book replies are off' in sent[-1]['text'].lower()
    incoming(1031,'hi')
    assert 'Cash in hand' not in sent[-1]['text']
    assert not c.get(base+'/link').json()['chat_enabled']
    c.post(base+'/chat',json={'enabled':True}).raise_for_status()
    assert c.get(base+'/link').json()['chat_enabled']
    bill(c).raise_for_status()
    incoming(104,'/bills')
    assert '৳3,892.41' in sent[-1]['text'] and 'owe suppliers' in sent[-1]['text']
    assert sent[-1]['chat_id']=='65432' and sent[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']=='__list_payables__'
    before=len(sent)
    incoming(104,'/bills')
    incoming(105,'/bills',chat=65433)
    incoming(106,'/bills',kind='group')
    assert len(sent)==before+1 and 'connect me to your books' in sent[-1]['text']
    incoming(112,'/start',chat=65433)
    assert 'Capzoryx' in sent[-1]['text'] and sent[-1]['chat_id']=='65433'
    incoming(107,'হাতে কত টাকা আছে?')
    assert sent[-1]['text'].startswith('এখন আপনার হাতে ৳')
    incoming(1071,'kemon acho')
    assert 'bhalo' in sent[-1]['text'].lower()
    incoming(1072,'hisab dekhao, business kemon cholche?')
    assert 'Hate cash' in sent[-1]['text']
    tap(1073,'__list_payables__')
    assert '৳3,892.41' in sent[-1]['text']
    limiter.reset()  # the per-chat message budget, not what is under test
    # A completed cash phrase becomes a draft, but nothing is written without the separate opt-in and a Save tap.
    incoming(108,'Rahim theke 500 taka pelam')
    assert 'chat entries' in sent[-1]['text'] and 'Rahim' in sent[-1]['text']
    assert c.post(base+'/records',json={'enabled':True}).status_code==200
    assert c.get(base+'/link').json()['records_enabled']
    incoming(109,'Rahim theke 500 taka pelam')
    assert 'save korbo?' in sent[-1]['text']
    tap(110,'__cancel__')
    assert 'save hoy ni' in sent[-1]['text'].lower()
    assert not c.get('/api/workspace').json()['events']
    incoming(111,'received 750 from Karim')
    tap(113,'__save__')
    assert 'Saved' in sent[-1]['text'] and '৳750' in sent[-1]['text']
    events=c.get('/api/workspace').json()['events']
    assert len(events)==1 and events[0]['amount_minor']==75000 and events[0]['direction']=='inflow'
    tap(114,'__save__')
    assert len(c.get('/api/workspace').json()['events'])==1 and "nothing waiting" in sent[-1]['text']
    c.post(base+'/chat',json={'enabled':False}).raise_for_status()
    incoming(115,'/balance')
    assert 'book replies are off' in sent[-1]['text'].lower()
    incoming(116,'/stop')
    assert 'Disconnected' in sent[-1]['text']
    incoming(117,'/balance')
    assert 'connect me to your books' in sent[-1]['text']
    assert not c.get(base+'/link').json()['linked']


def test_webhook_setup_registers_only_the_explicit_service_url(monkeypatch):
    inbound='https://api.synthetic.invalid/api/channels/telegram'
    secret='s'*40
    for suffix,value in {'ENABLED':'1','BOT_TOKEN':'123456:'+'t'*32,'WEBHOOK_SECRET':secret,
                         'BOT_USERNAME':'books_test_bot','WEBHOOK_URL':inbound}.items():
        monkeypatch.setenv('SAFELEDGER_TELEGRAM_'+suffix,value)
    calls=[]
    def fake(request):
        calls.append(json.loads(request.content))
        assert request.url.host=='api.telegram.org' and request.url.path.endswith('/setWebhook')
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(telegram,'TRANSPORT',httpx.MockTransport(fake))
    telegram.register_webhook()
    assert calls==[{'url':inbound,'secret_token':secret,'allowed_updates':['message','callback_query']}]
