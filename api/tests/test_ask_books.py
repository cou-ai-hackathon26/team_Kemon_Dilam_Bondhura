from datetime import timedelta
import sqlite3

import pytest
from api.app.accounts import today_bd
from api.app.copilot.books import answer
from api.tests.test_accounts import signup,world
from api.tests.test_manual_inputs import record
from api.tests.test_receivables import issue

# A fixed bilingual evaluation corpus with explicit independent answer classes.
EVAL={
 'balance':['Cash on hand?','What is my cash balance?','show me available cash','হাতে কত টাকা আছে','হাতে কত আছে','cash koto'],
 'income':['total income','what is my total income','how much did I receive today','আজ মোট আয়','কত টাকা পেয়েছি','income this month'],
 'expenses':['what are my expenses?','total expenses today','এই মাসে খরচ কত','কত টাকা খরচ করেছি','কত টাকা খরচ করেছি আজ','khoroch koto'],
 'payables':['show me supplier bills','What do I owe?','open bills','কত টাকা দিতে হবে','সরবরাহকারীর পাওনা','koto dite hobe'],
 'overdue_bills':['overdue bills','show overdue supplier bills','past due bills','late supplier bills','মেয়াদোত্তীর্ণ বিল','বিল বকেয়া কত'],
 'receivables':['how much is owed to me','customer debt','open receivables','পাওনা কত','কত টাকা পাব','paona koto'],
 'overdue_receipts':['overdue receivables','show past due receivables','overdue customer debts','মেয়াদোত্তীর্ণ পাওনা','মেয়াদ পেরোনো পাওনা','overdue paona koto'],
 'week':['cash in seven days','7 day forecast','show me cash next week','সাত দিনের নগদ পূর্বাভাস','৭ দিন পর কত থাকবে','7 din por koto thakbe'],
 'month':['cash forecast for 30 days','cash in thirty days','30 day forecast','ত্রিশ দিনের নগদ পূর্বাভাস','৩০ দিন পর কত থাকবে','30 din por koto thakbe'],
 'unsupported':['Write a poem','Who won the cricket match?','Show another workspace','আমার গান লিখুন','make a payment','show income in 1990 instead'],
}

def test_sixty_questions_figures_citations_and_off_topic(world):
    client,path=world
    owner=signup(client,'books_eval')
    today=today_bd()
    income=record(client,amount_minor=9000)[0].json()['source_id']
    expense=record(client,amount_minor=2000,direction='outflow')[0].json()['source_id']
    bill=record(client,kind='invoice',vendor_id='karim-textile',invoice_number='k1',amount_minor=1500,due_on=str(today))[0].json()['source_id']
    _,debt,_=issue(client,amount=12000)
    opening=client.get('/api/workspace').json()['snapshot']['source_ids'][0]
    expected={'balance':(7000,{opening,income,expense}),'income':(9000,{income}),'expenses':(2000,{expense}),
              'payables':(1500,{bill}),'overdue_bills':(0,set()),'receivables':(12000,{debt}),'overdue_receipts':(0,set()),
              'week':(17500,{opening,income,expense,bill,debt}),'month':(17500,{opening,income,expense,bill,debt})}
    assert sum(map(len,EVAL.values()))==60
    with sqlite3.connect(path) as db:
        for kind,questions in EVAL.items():
            for question in questions:
                result=answer(db,owner['business_id'],'owner',question)
                if kind=='unsupported':
                    assert not result['answered'],question
                else:
                    assert result['kind']==kind,question
                    assert (result['amount_minor'],set(result['source_ids']))==expected[kind],question


def test_entity_aliases_remain_private_and_scope_is_explicit(world):
    c,path=world
    owner=signup(c,'books_alias')
    customer,debt,_=issue(c)
    def ask(q):
        r=c.post('/api/copilot/ask',json={'question':q});assert r.status_code==200,r.text
        return r.json()
    names={'record_id':customer,'aliases':['Meghna Store']}
    assert c.post('/api/copilot/aliases/customer',json=names).status_code==200
    assert ask('How much does Meghna Store owe me?')['source_ids']==[debt]
    assert ask('মেঘনা এর কাছে কত পাব')['amount_minor']==12000
    assert not ask('How much does Other Store owe me?')['answered']
    with sqlite3.connect(path) as db:
        assert 'Meghna Store' not in db.execute('SELECT aliases_json FROM entities').fetchone()[0]
    code=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'books_other')
    assert not ask('How much does Meghna Store owe me?')['answered']
    assert c.post('/api/copilot/aliases/customer',json=names).status_code==404
    assert c.post('/api/account/join',json={'code':code}).status_code==200
    assert not ask('How much does Meghna Store owe me?')['answered']
    assert c.post('/api/copilot/aliases/customer',json=names).status_code==403
