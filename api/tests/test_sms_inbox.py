import json

import httpx

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.notifications import telegram
from api.tests.test_accounts import signup, world  # noqa: F401


def test_forwarded_payment_sms_becomes_a_one_tap_draft(world, monkeypatch):  # noqa: F811
    c, _ = world
    signup(c, 'sms_owner')
    base = '/api/inbox/sms'
    secret = 'z' * 40
    for suffix, value in {'ENABLED': '1', 'BOT_TOKEN': '123456:' + 't' * 32, 'WEBHOOK_SECRET': secret,
                          'BOT_USERNAME': 'sms_test_bot'}.items():
        monkeypatch.setenv('SAFELEDGER_TELEGRAM_' + suffix, value)
    monkeypatch.setenv('SAFELEDGER_PUBLIC_URL', 'https://capzoryx.test')
    sent = []

    def fake(request):
        body = json.loads(request.content)
        if 'text' in body:
            sent.append(body)
        return httpx.Response(200, json={'ok': True})
    monkeypatch.setattr(telegram, 'TRANSPORT', httpx.MockTransport(fake))
    token = c.post('/api/channels/telegram/link-code', json={}).json()['code']
    start = {'text': '/start ' + token, 'chat': {'id': 777, 'type': 'private'}, 'from': {'id': 777}, 'date': sec.now()}
    c.post('/api/channels/telegram', json={'update_id': 1, 'message': start},
           headers={'X-Telegram-Bot-Api-Secret-Token': secret}).raise_for_status()

    assert not c.get(base).json()['enabled']
    url = c.post(base + '/link', json={}).json()['url']
    assert url.startswith('https://capzoryx.test/api/inbox/sms/hook/')
    hook = url.split('capzoryx.test', 1)[1]
    day = today_bd().strftime('%d/%m/%Y')
    sms = (f'You have received Tk 5,000.00 from 01712345678. Fee Tk 0.00. Balance Tk 15,500.00. '
           f'TrxID BK12345678 at {day} 10:15')
    assert c.post(hook, json={'from': 'bKash', 'text': sms}).json()['result'] == 'draft'
    assert c.post(hook, json={'from': 'bKash', 'text': sms}).json()['result'] == 'duplicate'
    assert c.post(hook, json={'from': 'bKash', 'text': 'Your bKash OTP is 123456. Never share it.'}).json()['result'] == 'ignored'
    assert c.post(hook, json={'from': 'Grameen', 'text': 'Eid offer! 30GB at Tk 299'}).json()['result'] == 'ignored'
    assert c.post('/api/inbox/sms/hook/' + 'x' * 43, json={'text': sms}).status_code == 404

    alert = sent[-1]
    assert 'bKash: ৳5,000 received from 01712345678' in alert['text']
    data = alert['reply_markup']['inline_keyboard'][0][0]['callback_data']
    pending = c.get(base).json()['pending']
    assert len(pending) == 1 and data == 'sms:save:' + pending[0]['id']
    assert not c.get('/api/workspace').json()['events']

    def tap(update, value):
        query = {'id': 'q' + str(update), 'data': value, 'from': {'id': 777}, 'message': {'chat': {'id': 777, 'type': 'private'}}}
        c.post('/api/channels/telegram', json={'update_id': update, 'callback_query': query},
               headers={'X-Telegram-Bot-Api-Secret-Token': secret}).raise_for_status()
    tap(2, data)
    tap(3, data)
    events = c.get('/api/workspace').json()['events']
    assert len(events) == 1 and events[0]['amount_minor'] == 500000 and events[0]['direction'] == 'inflow'
    assert 'Saved' in sent[-2]['text'] and 'already handled' in sent[-1]['text']
    assert not c.get(base).json()['pending']

    sms2 = f'You have sent Tk 1,200.00 to 01811111111. TrxID BK87654321 at {day}'
    c.post(hook, json={'from': 'bKash', 'text': sms2}).raise_for_status()
    pending = c.get(base).json()['pending']
    assert pending[0]['direction'] == 'outflow'
    assert c.post(f"{base}/drafts/{pending[0]['id']}/skip", json={}).status_code == 200
    assert not c.get(base).json()['pending'] and len(c.get('/api/workspace').json()['events']) == 1
    c.post(base + '/unlink', json={}).raise_for_status()
    assert c.post(hook, json={'text': sms2}).status_code == 404
