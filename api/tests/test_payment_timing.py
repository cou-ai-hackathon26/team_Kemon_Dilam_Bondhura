from datetime import date,timedelta

from api.app.services.payment_timing import Paid,History,estimate
from api.tests.test_accounts import signup,world
from api.tests.test_receivables import issue,post
from api.app.accounts import today_bd
from evals.payment_timing import run


def test_shrinkage_and_conditioning():
    start=date(2026,1,1)
    tiny=History([Paid("a",start,start+timedelta(days=1),500)])
    all_rows=tiny.rows+[Paid("b",start,start+timedelta(days=25),500)]*3
    result=estimate(History(all_rows),tiny,0)
    assert result["within_days_pct"]=={"7":38,"14":38,"30":100}
    assert estimate(History(all_rows),tiny,26)["status"]=="insufficient_history"
    assert estimate(History(all_rows),tiny,10)["within_days_pct"]=={"7":0,"14":0,"30":100}
    assert run()["gate_passed"]


def test_timing_is_owner_only_and_scoped_to_workspace(world):
    c,_=world
    signup(c,'timing_first')
    _,settled,_=issue(c)
    assert post(c,f'receivables/{settled}/settlements',amount_minor=12000,received_on=str(today_bd())).status_code==201
    customer,_,fields=issue(c)
    new=post(c,"receivables",**dict(fields,customer_id=customer,due_on=str(today_bd()+timedelta(days=9)))).json()["id"]
    book=c.get('/api/workspace/receivables').json()
    assert settled not in book["collection_outlook"]
    assert book["collection_outlook"][new]["within_days_pct"]=={"7":100,"14":100,"30":100}
    viewer=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'timing_second')
    _,other,_=issue(c)
    other_book=c.get('/api/workspace/receivables').json()
    assert other_book["collection_outlook"][other]["status"]=="insufficient_history"
    assert c.post('/api/account/join',json={'code':viewer}).status_code==200
    assert c.get('/api/workspace/receivables').status_code==403
