from concurrent.futures import ThreadPoolExecutor

from api.app.accounts import today_bd
from api.tests.test_accounts import world,signup
from api.tests.test_receivables import issue


def row(owner,**changes):
    payload=dict(page_hash='b'*64,line=1,expected_business_id=owner['business_id'],expected_user_id=owner['user_id'],
                 checked=True,kind='inflow',currency='BDT',amount_minor=12000,day=str(today_bd()))
    return dict(payload,**changes)


def test_confirmed_lines_only_once_and_scope(world):
    c,_=world;owner=signup(c,'khata_owner')
    path='/api/workspace/khata/confirm'
    assert c.post(path,json=row(owner,checked=False)).status_code==422
    assert c.post(path,json=row(owner,expected_business_id='other')).status_code==409
    assert c.post(path,json=row(owner,kind='inflow',customer_id='somebody')).status_code==422
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post(path,json=row(owner)),range(2)))
    assert all(r.status_code==201 for r in responses)
    assert sorted(r.json()['replayed'] for r in responses)==[False,True]
    assert c.post(path,json=row(owner,amount_minor=300)).status_code==409
    assert c.post(path,json=row(owner,kind='outflow',line=2,amount_minor=2000)).status_code==201
    ds=c.get('/api/workspace').json()
    assert ds['snapshot']['balance_minor']==10000 and len(ds['events'])==2
    signup(c,'khata_other')
    assert c.post(path,json=row(owner)).status_code==409


def test_customer_credit_receipt_and_supplier_bill(world):
    c,_=world;owner=signup(c,'khata_credit')
    customer,debt,_=issue(c,4000)
    path='/api/workspace/khata/confirm'
    credit=row(owner,kind='customer_credit',customer_id=customer,due_on=str(today_bd()))
    assert c.post(path,json=credit).status_code==201
    assert c.post(path,json=credit).json()['replayed']
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0
    collected=row(owner,line=2,kind='customer_receipt',receivable_id=debt,amount_minor=1000)
    assert c.post(path,json=collected).status_code==201
    assert c.post(path,json=collected).json()['replayed']
    bill=row(owner,line=3,kind='supplier_bill',vendor_id='synthetic-supplier',number='S1',due_on=str(today_bd()))
    assert c.post(path,json=bill).status_code==201
    ds=c.get('/api/workspace').json()
    assert len(ds['invoices'])==1 and ds['snapshot']['balance_minor']==1000
    debts=c.get('/api/workspace/receivables').json()['receivables']
    assert sum(r['open_minor'] for r in debts)==15000
    inv=c.post('/api/account/invitations',json=dict(role='viewer')).json()['code']
    signup(c,'khata_view');c.post('/api/account/join',json=dict(code=inv))
    assert c.post(path,json=collected).status_code==403
