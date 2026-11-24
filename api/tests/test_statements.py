"""Synthetic statement readers, preview authorization, duplicate protection and cash conservation."""
import base64
import io

from openpyxl import Workbook
import pytest

from api.app.services.statements import parse_statement
from api.app.statements import Upload,decode
from api.tests.test_accounts import signup,world


def basic_pdf(lines):
    """One synthetic, borderless, horizontally aligned text table. No fixture contains a real account."""
    content='BT /F1 10 Tf '
    for i,row in enumerate(lines):
        for j,value in enumerate(row):
            content+=f'1 0 0 1 {40+j*100} {750-i*16} Tm ({value}) Tj\n'
    content+='ET'
    data=content.encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 640 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(data)).encode()+b' >>\nstream\n'+data+b'\nendstream']
    pdf=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(pdf));pdf+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(pdf);pdf+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:pdf+=f'{offset:010} 00000 n \n'.encode()
    return pdf+f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


def pack(kind,rows,delimiter=','):
    if kind=='xlsx':
        f=io.BytesIO();book=Workbook()
        for row in rows:book.active.append(row)
        book.save(f);book.close();return f.getvalue()
    if kind=='pdf':return basic_pdf(rows)
    return ('\n'.join(delimiter.join(map(str,row)) for row in rows)+'\n').encode()


@pytest.mark.parametrize('layout,headers,delimiter,descending',[
    ('csv',['Date','Reference','Amount','Balance'],',',False),('csv',['Posting Date','Ref','Net amount','Balance'],';',True),
    ('csv',['তারিখ','রেফারেন্স','পরিমাণ','স্থিতি'],'\t',False),('csv',['Value Date','Transaction ID','Net amount','Account balance'],',',True),
    ('xlsx',['Date','Ref','Amount','Balance'],',',False),('xlsx',['Txn Date','Reference','Amount','Balance'],',',True),
    ('xlsx',['তারিখ','রেফারেন্স','পরিমাণ','ব্যালেন্স'],',',False),('pdf',['Date','Ref','Amount','Balance'],',',False),
    ('pdf',['Date','Ref','Amount','Balance'],',',True),('pdf',['Date','Reference','Amount','Balance'],',',False),
])
def test_ten_synthetic_statements(layout,headers,delimiter,descending):
    balance=1800;rows=[];expected=[]
    for i in range(1,13):
        value=i*5*(-1 if i%3 else 1);balance+=value
        rows.append([f'2026-11-{i:02d}',f'SYN{i:02d}',str(value),str(balance)])
        expected.append(value*100)
    if descending:rows.reverse();expected.reverse()
    result=decode(Upload(data=base64.b64encode(pack(layout,[headers]+rows,delimiter)).decode(),layout=layout,
                         currency='BDT',account_label='Synthetic current account',expected_business_id='test',expected_user_id='test'))[1]
    assert [r['signed'] for r in result]==expected
    assert not any('reason' in r for r in result)


def upload(c,raw,**overrides):
    actor=c.get('/api/session').json()
    body=dict(expected_business_id=actor['business_id'],expected_user_id=actor['user_id'],data=base64.b64encode(raw).decode(),layout='csv',currency='BDT',account_label='Current',date_order='DMY')
    return c.post('/api/statements/preview',json=dict(body,**overrides))


def decision(preview,index=0,verify=False):
    row=preview['rows'][index]
    return dict(import_id=preview['import_id'],file=preview['file'],row=row['review'],signature=row['signature'],verified_distinct=verify)


def test_review_replay_overlap_and_tenant_scope(world):
    c,_=world;signup(c,'statement_owner')
    raw=b'Date,Ref,Debit,Credit,Balance\n2026-11-01,A001,,120,120\n2026-11-02,A002,50,,70\n'
    assert upload(c,raw,expected_business_id='previous').status_code==409
    preview=upload(c,raw)
    assert preview.status_code==200,preview.text
    preview=preview.json();assert all(r['status']=='new' for r in preview['rows'])
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0
    payload=decision(preview)
    invalid=dict(payload,row=dict(payload['row'],signed=999))
    assert c.post('/api/statements/confirm',json=invalid).status_code==409
    assert c.post('/api/statements/confirm',json=payload).status_code==200
    assert c.post('/api/statements/confirm',json=payload).json()['replayed']
    assert c.post('/api/statements/confirm',json=decision(preview,1)).status_code==200
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==7000
    overlapping=b'Date,Ref,Amount,Balance\n2026-11-02,A002,-50,70\n2026-11-03,A003,25,95\n'
    second=upload(c,overlapping).json();assert [r['status'] for r in second['rows']]==['duplicate','new']
    assert upload(c,raw,account_label='Different').status_code==409
    reused=overlapping.replace(b'-50,70',b'-40,70')
    assert upload(c,reused).json()['rows'][0]['reason']=='REFERENCE_CONFLICT'
    signup(c,'other_statement')
    assert c.post('/api/statements/confirm',json=payload).status_code==409
    assert upload(c,raw).json()['file']['rows_duplicate']==0


def test_review_ambiguous_amounts_roles_and_limits(world):
    c,_=world;signup(c,'statement_review')
    raw=b'Date,Ref,Amount,Balance\n2026-11-01,A1,40,40\n2026-11-01,A2,40,80\n'
    preview=upload(c,raw).json();assert [r['status'] for r in preview['rows']]==['review','review']
    assert c.post('/api/statements/confirm',json=decision(preview)).status_code==409
    assert c.post('/api/statements/confirm',json=decision(preview,verify=True)).status_code==200
    verify=upload(c,b'Date,Ref,Amount,Balance\n2026-11-01,A1,40,40\n2026-11-02,A3,25,82\n').json()
    assert verify['rows'][1]['reason']=='BALANCE_DISCONTINUITY' and verify['rows'][1]['review'] is None
    mismatch=upload(c,raw.replace(b'A2',b'A1')).json();assert all(r['status']!='new' for r in mismatch['rows'])
    assert upload(c,b'not a statement').status_code==422
    assert c.post('/api/statements/preview',content=b' '*(4*1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413
    invite=c.post('/api/account/invitations',json={'role':'viewer'}).json()['code']
    signup(c,'statement_view');c.post('/api/account/join',json={'code':invite})
    assert upload(c,raw).status_code==403
    assert c.post('/api/statements/confirm',json=decision(preview,verify=True)).status_code==403


def test_reader_refuses_non_transaction_rows_and_formulae():
    rows=parse_statement(b'Date,Amount,Currency\n2026-11-01,5,USD\n2026-11-02,=2+2,BDT\n','csv','BDT',2,'DMY')
    assert len(rows)==2 and all(r['reason']=='UNREADABLE_ROW' for r in rows)
    with pytest.raises(ValueError):
        parse_statement(b'Date,Date,Amount\n2026-11-01,2026-11-01,2\n','csv','BDT',2,'DMY')
    data=b'Date,Amount\n'+20001*b'2026-11-21,5\n'
    with pytest.raises(ValueError,match='STATEMENT_SIZE_LIMIT'):
        parse_statement(data,'csv','BDT',2,'DMY')


def test_receivable_match_is_explicit_atomic_and_replay_safe(world):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from api.app.accounts import today_bd
    from api.tests.test_receivables import issue

    c,path=world;signup(c,'statement_receipts')
    _,first,_=issue(c,12300)
    _,second,_=issue(c,12300)
    raw=f'Date,Ref,Amount,Balance\n{today_bd()},SYN01,123,123\n'.encode()
    preview=upload(c,raw).json()
    signed=preview['rows'][0]
    assert signed['match_basis']=='amount'
    assert {m['receivable_id'] for m in signed['review']['matches']}=={first,second}
    assert set(preview['suggested_receivables'][first])=={'customer_name','number','due_on','open_minor'}
    save=decision(preview)
    choice=dict(save,receivable_id=first,checked_receivable=True)
    assert c.post('/api/statements/confirm',json=dict(choice,checked_receivable=False)).status_code==422
    assert c.post('/api/statements/confirm',json=dict(choice,receivable_id='unrelated')).status_code==422
    changed=dict(save['row'],matches=[dict(receivable_id=first,open_minor=12301)])
    assert c.post('/api/statements/confirm',json=dict(choice,row=changed)).status_code==409
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==0
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post('/api/statements/confirm',json=choice),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert sorted(r.json()['replayed'] for r in responses)==[False,True]
    assert c.post('/api/statements/confirm',json=dict(choice,receivable_id=second)).status_code==409
    assert c.post('/api/statements/confirm',json=save).status_code==409
    bundle=c.get('/api/workspace').json()
    assert bundle['snapshot']['income_minor']==12300 and bundle['month']['closing_balance_minor']==24600
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM statement_rows').fetchone()[0]==1
        proof=db.execute('SELECT s.receivable_id,s.cash_record_id,r.cash_record_id FROM receivable_settlements s JOIN statement_rows r ON s.business_id=r.business_id').fetchall()
        assert proof==[(first,responses[0].json()['cash_record_id'],responses[0].json()['cash_record_id'])]
    assert 'STATEMENT_ROW_IMPORTED' in [e['kind'] for e in bundle['audit']['events']]
    signup(c,'statement_other_tenant')
    assert c.post('/api/statements/confirm',json=choice).status_code==409


def test_invoice_reference_partial_and_stale_preview(world):
    import uuid
    from api.app.accounts import today_bd
    from api.tests.test_receivables import issue, post

    c,_=world;signup(c,'statement_partial')
    customer,_,fields=issue(c,8000)
    debt=post(c,'receivables',**dict(fields,customer_id=customer,number='INV-42',amount_minor=11000)).json()['id']
    raw=f'Date,Ref,Amount,Balance\n{today_bd()},inv-42,45,45\n{today_bd()},SYNOUT,-10,35\n'.encode()
    preview=upload(c,raw).json()
    receipt=preview['rows'][0]
    assert receipt['match_basis']=='reference' and receipt['review']['matches']==[dict(receivable_id=debt,open_minor=11000)]
    assert preview['rows'][1]['match_basis'] is None
    assert c.post('/api/statements/confirm',json=dict(decision(preview,1),receivable_id=debt,checked_receivable=True)).status_code==422
    c.post('/api/workspace/receivables/'+debt+'/settlements',json=dict(
        request_id=str(uuid.uuid4()),amount_minor=1000,received_on=str(today_bd()))).raise_for_status()
    assert c.post('/api/statements/confirm',json=dict(decision(preview),receivable_id=debt,checked_receivable=True)).json()['error']['code']=='STATEMENT_RECEIVABLE_CHANGED'
    assert upload(c,raw).json()['imported_count']==0
    refreshed=upload(c,raw).json()
    result=c.post('/api/statements/confirm',json=dict(decision(refreshed),receivable_id=debt,checked_receivable=True))
    assert result.status_code==200,result.text
    balances={d['id']:d['open_minor'] for d in c.get('/api/workspace/receivables').json()['receivables']}
    assert balances[debt]==5500
    assert c.get('/api/workspace').json()['snapshot']['balance_minor']==5500


def test_optional_cash_only_cannot_later_be_double_booked(world):
    from api.app.accounts import today_bd
    from api.tests.test_receivables import issue

    c,_=world;signup(c,'statement_cash_only')
    _,debt,_=issue(c,4700)
    raw=f'Date,Ref,Amount,Balance\n{today_bd()},SYN2,47,47\n'.encode()
    preview=upload(c,raw).json()
    save=decision(preview)
    assert c.post('/api/statements/confirm',json=save).json()['receivable_id'] is None
    assert c.post('/api/statements/confirm',json=dict(save,receivable_id=debt,checked_receivable=True)).status_code==409
    assert c.get('/api/workspace/receivables').json()['receivables'][0]['open_minor']==4700
