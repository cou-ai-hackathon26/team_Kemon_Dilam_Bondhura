"""Transport failures must not turn an interrupted approval into a fresh transaction."""
import json
import sqlite3
import httpx
import pytest
from api.app.database import RemoteConnection,connect,encode


def client(handler):
 return RemoteConnection('libsql://test.turso.io','not-a-real-token',transport=httpx.MockTransport(handler))


def ok(request,baton='next',rows=None):
 requests=json.loads(request.content)['requests']
 return httpx.Response(200,json={'baton':baton,'base_url':None,'results':[{'type':'ok','response':{'type':operation['type'],'result':{'cols':[{'name':'amount'}],'rows':rows or []}}} for operation in requests]})


def test_exact_large_integer_and_named_row():
 with client(lambda r:ok(r,rows=[[{'type':'integer','value':'9007199254740993'}]])) as db:
  db.row_factory=sqlite3.Row
  row=db.execute('SELECT amount').fetchone()
  assert row[0]==row['amount']==9007199254740993
  assert dict(row)=={'amount':9007199254740993}
  assert encode(row[0])=={'type':'integer','value':'9007199254740993'}
 db.close()


@pytest.mark.parametrize('mode',['timeout','http500','closed','wrong_type','wrong_count','unsafe_redirect','bad_json'])
def test_lost_stream_never_reopens_or_commits(mode):
 calls=[]
 def handler(request):
  calls.append(json.loads(request.content))
  if len(calls)==1:return ok(request,'first-baton')
  if mode=='timeout':raise httpx.ReadTimeout('private URL must not leak')
  if mode=='http500':return httpx.Response(500)
  if mode=='closed':return ok(request,None)
  if mode=='bad_json':return httpx.Response(200,text='<html>')
  data=json.loads(ok(request).content)
  if mode=='wrong_type':data['results'][0]['response']['type']='close'
  if mode=='wrong_count':data['results']=[]
  if mode=='unsafe_redirect':data['base_url']='https://attacker.invalid/'
  return httpx.Response(200,json=data)
 db=client(handler);db.execute('BEGIN IMMEDIATE')
 with pytest.raises(sqlite3.DatabaseError,match='Remote database operation failed'):db.execute('SELECT amount')
 with pytest.raises(sqlite3.DatabaseError):db.commit()
 with pytest.raises(sqlite3.DatabaseError):db.execute('INSERT INTO t VALUES (1)')
 assert len(calls)==2 and calls[1]['baton']=='first-baton'
 db.close()


def test_constraint_rollback_and_baton_rotation():
 calls=[]
 def handler(request):
  body=json.loads(request.content);calls.append(body)
  if len(calls)==2:return httpx.Response(200,json={'baton':'after-error','results':[{'type':'error','error':{'code':'SQLITE_CONSTRAINT_UNIQUE','message':'private SQL'}}]})
  return ok(request,'after-'+str(len(calls)))
 db=client(handler)
 with pytest.raises(sqlite3.IntegrityError):
  with db:db.execute('INSERT INTO t VALUES (?)',(7,))
 assert [x['requests'][-1]['stmt']['sql'] for x in calls]==['BEGIN','INSERT INTO t VALUES (?)','ROLLBACK']
 assert calls[2]['baton']=='after-error' and not db.in_transaction
 db.close()


@pytest.mark.parametrize('url,token',[('', ''),('libsql://demo.turso.io',''),('https://attacker.invalid','x'),('libsql://demo.turso.io?x=1','x')])
def test_vercel_never_falls_back_to_ephemeral_sqlite(monkeypatch,url,token):
 monkeypatch.setenv('VERCEL','1');monkeypatch.setenv('TURSO_DATABASE_URL',url);monkeypatch.setenv('TURSO_AUTH_TOKEN',token)
 with pytest.raises(sqlite3.DatabaseError):connect()


def test_turso_auth_failure_reports_status_without_credentials():
    def unauthorized(request):
        assert request.url.path == '/v3/pipeline'
        return httpx.Response(401, text='Invalid JWT: private diagnostic')
    db = client(unauthorized)
    with pytest.raises(sqlite3.DatabaseError) as caught:
        db.execute('SELECT 1')
    assert str(caught.value) == 'Remote database operation failed (Turso HTTP 401, authentication)'
    db.close()


def test_schemas_start_twice_on_a_remote_database():
 """Like Turso, any failed statement closes the stream, so a schema must never rely on 'try it and ignore the error'.
 Running each schema twice is what a second deploy does; it must not fail."""
 from api.app import advisor, business, market, monitor, payroll
 real=sqlite3.connect(':memory:')
 def handler(request):
  out=[]
  for op in json.loads(request.content)['requests']:
   sql=op['stmt']['sql'] if op['type']=='execute' else op.get('sql','')
   try:
    if op['type']=='sequence':real.executescript(sql);rows,cols=[],[]
    else:
     cur=real.execute(sql,[a.get('value') for a in op['stmt'].get('args',[])]);rows=cur.fetchall();cols=[{'name':d[0]} for d in (cur.description or [])]
   except sqlite3.Error as error:
    out.append({'type':'error','error':{'message':str(error),'code':'SQLITE_ERROR'}});continue
   out.append({'type':'ok','response':{'type':op['type'],'result':{'cols':cols,'rows':[[encode(v) for v in r] for r in rows]}}})
  return httpx.Response(200,json={'baton':'b','base_url':None,'results':out})
 for _ in range(2):
  db=client(handler)
  for module in (payroll,business,market,monitor,advisor):module.schema(db)
  assert not db.failed
  db.close()
 assert 'inputs' in {r[1] for r in real.execute('PRAGMA table_info(research_runs)')}


def test_idle_stream_closed_by_the_server_reopens_only_outside_a_transaction():
 calls=[]
 def handler(request):
  body=json.loads(request.content);calls.append(body)
  if body['baton']=='stale':return httpx.Response(404)
  return ok(request,'stale' if len(calls)==1 else 'fresh-'+str(len(calls)))
 db=client(handler)
 db.execute('SELECT 1')  # the stream is now idle; the server forgets it during a long outside call
 db.execute('BEGIN IMMEDIATE')
 assert calls[1]['baton']=='stale' and calls[2]['baton'] is None
 assert calls[2]['requests'][0]['stmt']['sql']=='PRAGMA foreign_keys=ON' and db.in_transaction
 db.close()
 # Inside a transaction the same 404 is a lost transaction: never reopened.
 calls.clear()
 def inside(request):
  body=json.loads(request.content);calls.append(body)
  return httpx.Response(404) if len(calls)==2 else ok(request,'stale')
 db=client(inside);db.execute('BEGIN')
 with pytest.raises(sqlite3.DatabaseError):db.execute('SELECT 1')
 assert len(calls)==2
 db.close()
