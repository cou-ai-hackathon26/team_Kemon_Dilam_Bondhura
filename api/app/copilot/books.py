"""Read-only, deterministic ledger questions with dated source citations."""
import json
import unicodedata
from datetime import timedelta
from hashlib import sha256

from fastapi import APIRouter, Depends
from pydantic import Field

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract, RecordId, ScenarioRequest
from api.app.receivables import customer_book, owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.finance import invoice_key, project_cashflow, snapshot
from api.app.services.receivables import customer_key, key_tag, open_receipts, seal, unseal

router = APIRouter(prefix='/api/copilot')


def schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY,business_id TEXT NOT NULL,kind TEXT NOT NULL,
          name TEXT NOT NULL,aliases_json TEXT NOT NULL DEFAULT '[]',UNIQUE(business_id,kind,name));
    """)


def normalize(value):
    text = unicodedata.normalize('NFKC',value).casefold()
    return ' '.join(''.join(char if unicodedata.category(char)[0] in 'LMN' else ' ' for char in text).split())


# Exact normalized phrases avoid answering an unsupported question with a plausible-looking total.
QUERIES = {
    'balance': ('cash balance','available cash','cash on hand','balance','how much cash do i have',
                'what is my cash balance','কত টাকা হাতে আছে','হাতে কত টাকা আছে','নগদ স্থিতি','হাতে কত আছে','cash koto'),
    'income': ('income','total income','cash received','how much did i receive','money received','আয় কত','মোট আয়','কত টাকা পেয়েছি','কত টাকা আয় করেছি','ay koto'),
    'expenses': ('expenses','total expenses','cash spent','how much did i spend','money spent','খরচ কত','মোট খরচ','কত টাকা খরচ করেছি','khoroch koto'),
    'payables': ('supplier bills','supplier dues','open bills','how much do i owe','what do i owe','total payable','বাকি বিল','কত টাকা দিতে হবে','কত দিতে হবে','সরবরাহকারীর পাওনা','koto dite hobe'),
    'overdue_bills': ('overdue bills','overdue supplier bills','past due bills','late supplier bills','মেয়াদোত্তীর্ণ বিল','বিল বকেয়া কত','overdue bill koto'),
    'receivables': ('customer dues','customer debt','open receivables','how much am i owed','how much is owed to me','amount owed to me','কত টাকা পাব','কত পাব','মোট পাওনা','পাওনা কত','paona koto'),
    'overdue_receipts': ('overdue receivables','overdue customer debts','past due receivables','late customer payments','মেয়াদোত্তীর্ণ পাওনা','মেয়াদ পেরোনো পাওনা','overdue paona koto'),
    'week': ('cash in 7 days','cash in seven days','cash next week','7 day forecast','cash forecast for 7 days','সাত দিনের নগদ পূর্বাভাস','৭ দিন পর কত থাকবে','সাত দিন পর কত থাকবে','7 din por koto thakbe'),
    'month': ('cash in 30 days','cash in thirty days','cash next month','30 day forecast','cash forecast for 30 days','ত্রিশ দিনের নগদ পূর্বাভাস','৩০ দিন পর কত থাকবে','ত্রিশ দিন পর কত থাকবে','30 din por koto thakbe'),
}
MAPPING = {normalize(phrase):kind for kind, phrases in QUERIES.items() for phrase in phrases}
WINDOWS={'today':('today','আজ','আজকে','aj'),'this_month':('this month','এই মাসে','এ মাসে','ei mashe'),
         'last_month':('last month','গত মাসে','goto mashe')}


def split_window(text):
    for window, phrases in WINDOWS.items():
        for phrase in phrases:
            marker=normalize(phrase)
            if text.startswith(marker+' '):
                return text[len(marker)+1:],window
            if text.endswith(' '+marker):
                return text[:-len(marker)-1],window
    return text,None


def query_parts(question):
    text=normalize(question)
    for prefix in ('please show me ', 'please show ', 'show me ', 'what is my ', 'what are my ', 'show my ', 'show ', 'আমাকে দেখান '):
        if text.startswith(prefix):
            text=text[len(prefix):]
            break
    return split_window(text)


def aliases(db, business, owner):
    items={}
    key=customer_key(db,business)
    rows=db.execute("SELECT id,kind,name,aliases_json FROM entities WHERE business_id=? AND (? OR kind='supplier')",(business,int(owner)))
    for identity,kind,name,raw in rows:
        box=json.loads(raw)
        if key and isinstance(box,dict) and box['tag']==key_tag(key):
            items[kind,name]=json.loads(unseal(key,box['data'],identity))
    return items


def entity_search(text, entities):
    matches=[]
    for kind, identity, names in entities:
        for name in set(normalize(n) for n in names if n):
            if not name:
                continue
            # Explicit entity syntax keeps unrelated uses of a merchant's name out of financial queries.
            for phrase in (('how much do i owe '+name,'payables'),('bills from '+name,'payables'),('supplier dues for '+name,'payables'),
                           (name+' এর বিল কত','payables'),(name+' কে কত দিতে হবে','payables')) if kind=='supplier' else (
                           ('how much does '+name+' owe me','receivables'),('amount owed by '+name,'receivables'),
                           ('customer dues for '+name,'receivables'),(name+' এর কাছে কত পাব','receivables'),(name+' এর পাওনা কত','receivables')):
                if text==phrase[0]:
                    matches.append((phrase[1],identity))
    matches=set(matches)
    return matches.pop() if len(matches)==1 else (None,None)


def answer(db, business, role, question):
    text,window=query_parts(question)
    kind=MAPPING.get(text)
    entity=None
    if window and kind not in ('income','expenses'):
        kind=None
    ds=sec.runtime_dataset(db,business)
    if not kind and not window:
        stored=aliases(db,business,role=='owner')
        entities=[('supplier',v,[v,*stored.get(('supplier',v),[])]) for v in {i.vendor_id for i in ds.invoices}]
        if role=='owner':
            for customer in customer_book(db,business)['customers']:
                entities.append(('customer',customer['id'],[customer['name'],*stored.get(('customer',customer['id']),[])]))
        kind,entity=entity_search(text,entities)
    if kind is None:
        return dict(answered=False,reason='UNSUPPORTED_OR_AMBIGUOUS_QUESTION',simulation_only=True)
    return compute(db,business,kind,window,entity,ds)


def compute(db, business, kind, window=None, entity=None, ds=None):
    """Figure for an already-understood question. Windows apply to income and expenses only."""
    ds=ds or sec.runtime_dataset(db,business)
    if kind not in ('income','expenses'):
        window=None
    if kind=='balance':
        result=snapshot(ds)
        amount,sources=result['balance_minor'],result['source_ids']
    elif kind in ('income','expenses'):
        start,end=ds.opening_on,today_bd()
        first=end.replace(day=1)
        if window=='today':
            start=end
        elif window=='yesterday':
            start=end=end-timedelta(days=1)
        elif window=='this_week':
            start=end-timedelta(days=(end.weekday()+2)%7)  # Bangladesh weeks start on Saturday.
        elif window=='this_month':
            start=first
        elif window=='last_month':
            end=first-timedelta(days=1)
            start=end.replace(day=1)
        direction='inflow' if kind=='income' else 'outflow'
        rows=[e for e in ds.events if e.direction==direction and e.status=='cleared' and start<=e.effective_on<=end]
        amount=sum(e.amount_minor for e in rows)
        sources=[e.source_record_id for e in rows]
    elif kind in ('payables','overdue_bills'):
        unique={}
        for inv in sorted(ds.invoices,key=lambda i:i.invoice_id):
            unique.setdefault(invoice_key(inv),inv)
        rows=[i for i in unique.values() if i.status=='open' and (entity is None or i.vendor_id==entity)
              and (kind=='payables' or i.due_on<today_bd())]
        amount=sum(i.amount_minor for i in rows)
        sources=[i.source_record_id for i in rows]
    elif kind in ('receivables','overdue_receipts'):
        rows=db.execute("""SELECT id,open_minor FROM receivable_balances WHERE business_id=? AND open_minor>0
                           AND (? IS NULL OR customer_id=?) AND (?=0 OR due_on<?)""",
                        (business,entity,entity,int(kind=='overdue_receipts'),str(today_bd()))).fetchall()
        amount=sum(row[1] for row in rows)
        sources=[row[0] for row in rows]
    else:
        scenario=ScenarioRequest(as_of=ds.as_of,horizon_days=7 if kind=='week' else 30,reserve_minor=0)
        result=project_cashflow(ds,scenario,open_receipts(db,business))
        amount,sources=result['closing_balance_minor'],result['source_ids']
    return dict(answered=True,kind=kind,currency=ds.currency,amount_minor=amount,as_of=str(ds.as_of),
                window=window,source_ids=sorted(sources),simulation_only=True)


class Question(Contract):
    question:str=Field(min_length=1,max_length=240)


@router.post('/ask')
def ask(body:Question,actor=Depends(principal),db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        current=sec.recheck(db,actor)
        if current.role not in ('owner','approver','viewer','bookkeeper'):
            sec.fail(403,'MEMBER_REQUIRED')
        sec.verified_audit(db,current.business_id)
        return answer(db,current.business_id,current.role,body.question)


class Alias(Contract):
    record_id:RecordId
    aliases:list[str]=Field(max_length=8)


@router.post('/aliases/{kind}')
def set_aliases(kind:str,body:Alias,actor=Depends(principal),db=Depends(db_connection)):
    if kind not in ('customer','supplier') or any(not n.strip() or len(n)>80 for n in body.aliases):
        sec.fail(422,'INVALID_ENTITY_ALIAS')
    with owner_transaction(db,actor) as current:
        business=current.business_id
        if kind=='supplier':
            exists=any(i.vendor_id==body.record_id for i in sec.original_dataset(db,business).invoices)
        else:
            exists=db.execute('SELECT 1 FROM customers WHERE business_id=? AND id=?',(business,body.record_id)).fetchone()
        if not exists:
            sec.fail(404,'ENTITY_NOT_FOUND')
        identity=sha256(sec.canonical([business,kind,body.record_id]).encode()).hexdigest()
        key=customer_key(db,business,create=True)
        box=dict(tag=key_tag(key),data=seal(key,sec.canonical(body.aliases).encode(),identity))
        db.execute("""INSERT INTO entities VALUES(?,?,?,?,?) ON CONFLICT(business_id,kind,name)
                      DO UPDATE SET aliases_json=excluded.aliases_json""",(identity,business,kind,body.record_id,sec.canonical(box)))
        sec.audit(db,current,identity,'ENTITY_ALIASES_UPDATED',{'kind':kind,'count':len(body.aliases)})
    return dict(simulation_only=True)
