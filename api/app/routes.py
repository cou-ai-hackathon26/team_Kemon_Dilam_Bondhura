"""Authenticated financial routes. No public execute/session-provisioning endpoint."""
from contextlib import closing
import hashlib
import json
import sqlite3
from typing import Annotated
from fastapi import APIRouter, BackgroundTasks, Depends, Path as ApiPath, Request
from api.app import security as sec
from api.app.contracts import ApprovalRequest, Contract, PositiveMinor, ProposalRequest, ScenarioRequest
from api.app.services.finance import Dataset, FinancingRequest, compare_financing, project_cashflow, snapshot
from api.app.services.gap_fix import collection_plan
from api.app.services.receivables import open_receipts
from api.app.services.storage import store_dataset
from api.app.services import record_notes

ProposalPath=Annotated[str,ApiPath(pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')]
TracePath=Annotated[str,ApiPath(pattern=r'^[A-Za-z0-9-]{1,80}$')]
router=APIRouter(prefix='/api')


def db_connection():
    from api.app.main import connect
    with closing(connect()) as db:
        yield db


def principal(request: Request, db=Depends(db_connection)):
    from api.app.app_sessions import native
    is_app = native(request)
    authorization=request.headers.get('Authorization','')
    if is_app and not authorization:sec.fail(401,'AUTHENTICATION_REQUIRED')
    # Cheap in-memory ceiling first: a flood from one infrastructure-observed address never reaches the database.
    sec.budgets(db,[('request-edge:'+sec.client_keys(request)[1],1200)])
    try:
        parts=authorization.split()
        if authorization:
            if len(parts)!=2 or parts[0].lower()!='bearer':sec.fail(401,'AUTHENTICATION_REQUIRED')
            token=parts[1]
        else:
            from api.app.accounts import COOKIE,browser_origin
            token=request.cookies.get(COOKIE,'')
            if token and request.method not in {'GET','HEAD','OPTIONS'}:browser_origin(request)
        actor=sec.authenticate(db,token)
    except sec.PolicyError:
        claimed,edge=sec.client_keys(request)
        sec.budgets(db,[('auth-failure:'+claimed,20),('auth-failure-edge:'+edge,200)])
        raise
    sec.budget(db,'session:'+actor.token_hash,240 if request.method=='GET' else 60)
    from api.app.accounts import PUBLIC_TEMPLATES
    from api.app.feature_flags import workspace_status
    route = request.scope.get('route')
    meta = workspace_status(db, actor.business_id, getattr(route, 'path', request.url.path))
    if actor.business_id in PUBLIC_TEMPLATES or (meta and meta[0]=='demo'):
        sec.fail(403,'ARCHIVED_EXAMPLE_WORKSPACE')
    if meta and meta[1]:
        sec.fail(403,'WORKSPACE_CLEARED')
    if meta and not meta[2]:
        sec.fail(503, 'FEATURE_PAUSED')
    return actor


@router.get('/session')
def session(actor=Depends(principal),db=Depends(db_connection)):
    from api.app.accounts import identity
    return identity(db,actor)


@router.post('/invoices/import',status_code=201)
def import_dataset(body: Dataset,actor=Depends(principal),db=Depends(db_connection)):
    def authorize():
        current=sec.recheck(db,actor)
        if current.role!='owner':sec.fail(403,'OWNER_REQUIRED')
        sec.verified_audit(db,current.business_id)
    def evidence(ds,checksum):
        sec.audit(db,actor,'import','IMPORTED',dict(dataset_sha256=checksum,invoice_count=len(ds.invoices),event_count=len(ds.events)))
    try:
        result=store_dataset(db,actor.business_id,body,before_write=authorize,after_write=evidence)
    except sec.PolicyError as exc:
        sec.deny_event(db,actor,'import',exc.code)
        raise
    except sqlite3.IntegrityError:
        sec.deny_event(db,actor,'import','DATASET_ALREADY_IMPORTED')
        sec.fail(409,'DATASET_ALREADY_IMPORTED')
    return dict(result,simulation_only=True)


@router.get('/dashboard')
def dashboard(actor=Depends(principal),db=Depends(db_connection)):
    sec.verified_audit(db,actor.business_id)
    ds=sec.runtime_dataset(db,actor.business_id)
    return dict(snapshot(ds),simulation_only=True)


@router.get('/invoices')
def invoices(actor=Depends(principal),db=Depends(db_connection)):
    ds=sec.runtime_dataset(db,actor.business_id)
    return dict(invoices=[x.model_dump(mode='json') for x in ds.invoices],simulation_only=True)


@router.post('/cashflow/scenarios')
def cashflow(body: ScenarioRequest,actor=Depends(principal),db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        ds=sec.runtime_dataset(db,actor.business_id)
        if body.as_of!=ds.as_of:sec.fail(409,'SNAPSHOT_DATE_CHANGED_REFRESH_REQUIRED')
        return dict(project_cashflow(ds,body,open_receipts(db,actor.business_id)),simulation_only=True)


@router.get('/radar')
def cash_radar(actor=Depends(principal),db=Depends(db_connection)):
    """The realistic cash outlook: customer payment habits, late and doubtful money, and the alert level."""
    if actor.role not in {'owner', 'approver', 'viewer', 'bookkeeper'}:
        sec.fail(403, 'MEMBER_REQUIRED')
    from api.app.accounts import today_bd
    from api.app.services.radar import radar
    from api.app.receivables import customer_book
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        ds = sec.runtime_dataset(db, current.business_id)
        policy = db.execute('SELECT reserve_minor FROM security_policies WHERE business_id=?', (current.business_id,)).fetchone()
        view = radar(db, current.business_id, ds, policy[0] if policy else 0, max(today_bd(), ds.as_of))
        names = {}
        if current.role == 'owner':
            try:
                names = {c['id']: c['name'] for c in customer_book(db, current.business_id)['customers']}
            except Exception:  # noqa: BLE001 - names are a convenience; ids still identify each customer
                names = {}
        for item in view['receivables'] + view['depends_on']:
            item['customer'] = names.get(item['customer_id'], '')
        return dict(view, simulation_only=True)


@router.post('/copilot/gap-fix')
def gap_fix(body: ScenarioRequest, actor=Depends(principal), db=Depends(db_connection)):
    if actor.role not in {'owner', 'approver', 'viewer', 'bookkeeper'}:
        sec.fail(403, 'MEMBER_REQUIRED')
    with sec.transaction(db):
        current = sec.recheck(db, actor)
        sec.verified_audit(db, current.business_id)
        dataset = sec.runtime_dataset(db, current.business_id)
        if dataset.as_of != body.as_of:
            sec.fail(409, 'SNAPSHOT_DATE_CHANGED_REFRESH_REQUIRED')
        if body.delay_inflows_days:
            sec.fail(422, 'USE_BASELINE_RECEIPT_DATES')
        receipts = open_receipts(db, current.business_id)
        from api.app.copilot.plans import propose
        return propose(db, current, dataset, receipts, body)


class CompareRequest(Contract):
    principal_minor: PositiveMinor


@router.post('/finance/compare')
def financing(body:CompareRequest,actor=Depends(principal)):
    sec.fail(410,'FICTIONAL_FINANCING_RETIRED')


@router.post('/proposals',status_code=201)
def proposal(body: ProposalRequest,background:BackgroundTasks,actor=Depends(principal),db=Depends(db_connection)):
    from api.app.notifications import push
    result=sec.propose(db,actor,body.invoice_id)
    if push.enabled('webpush') or push.enabled('fcm'):
        background.add_task(push.urgent_delivery,actor.business_id)
    return result


@router.get('/proposals')
def list_proposals(actor=Depends(principal),db=Depends(db_connection)):
    db.row_factory=sqlite3.Row
    rows=db.execute('SELECT * FROM proposals WHERE business_id=? ORDER BY rowid DESC LIMIT 200',(actor.business_id,)).fetchall()
    for row in rows:sec.verify_proposal(db,row)
    return {'proposals':[sec.proposal_view(row) for row in rows],'simulation_only':True}


@router.get('/proposals/{proposal_id}')
def get_proposal(proposal_id: ProposalPath,actor=Depends(principal),db=Depends(db_connection)):
    row=sec.get_proposal(db,actor,proposal_id)
    sec.verify_proposal(db,row)
    return sec.proposal_view(row)


@router.post('/proposals/{proposal_id}/approve')
def decide(proposal_id: ProposalPath,body: ApprovalRequest,actor=Depends(principal),db=Depends(db_connection)):
    return sec.decide(db,actor,proposal_id,body)


@router.get('/audit/{trace_id}')
def trace(trace_id: TracePath,actor=Depends(principal),db=Depends(db_connection)):
    all_events=sec.verified_audit(db,actor.business_id)
    events=[x for x in all_events if x['trace_id']==trace_id]
    if not events:sec.fail(404,'TRACE_NOT_FOUND')
    return dict(trace_id=trace_id,events=events,chain_verified=True,tenant_chain_head=all_events[-1]['event_hash'],simulation_only=True)


@router.get('/audit')
def export_audit(actor=Depends(principal),db=Depends(db_connection)):
    events=sec.verified_audit(db,actor.business_id)
    return dict(events=events,chain_verified=True,tenant_chain_head=events[-1]['event_hash'] if events else '0'*64,simulation_only=True)


from api.app.agents.models import TaskRequest

@router.post('/agent/tasks')
def agent_task(body: TaskRequest,actor=Depends(principal),db=Depends(db_connection)):
    from api.app.accounts import PUBLIC_TEMPLATES
    if actor.business_id in PUBLIC_TEMPLATES:sec.fail(403,'CREATE_ACCOUNT_FOR_ASSISTANT')
    if not body.external_ai_consent:sec.fail(403,'EXTERNAL_AI_CONSENT_REQUIRED')
    from api.app.assist import owner_allows_cloud
    if actor.role!='owner' and not owner_allows_cloud(db,actor.business_id):sec.fail(403,'WORKSPACE_OWNER_SMART_ASSIST_OFF')
    if db.execute('SELECT 1 FROM account_members WHERE user_id=?',(actor.user_id,)).fetchone():
        sec.budget(db,'public-ai-global',8)
    from api.app.agents import runner
    return runner.run_task(db,actor,body)

@router.get('/workspace')
def workspace_bundle(actor=Depends(principal),db=Depends(db_connection)):
    """One consistent read snapshot avoids seven repeated remote database loads."""
    from pathlib import Path
    db.execute('BEGIN')
    with db:
        events=sec.verified_audit(db,actor.business_id)
        ds=sec.runtime_dataset(db,actor.business_id,verified_history=events)
        rows=db.execute('SELECT * FROM proposals WHERE business_id=? ORDER BY rowid DESC LIMIT 200',(actor.business_id,)).fetchall()
        for row in rows:sec.verify_proposal(db,row)
        # Return one owner's next collection alongside the existing policy read.
        # The subquery uses the same transaction and current session as the snapshot.
        policy=db.execute("""SELECT p.reserve_minor,p.payment_limit_minor,
            (SELECT t.payload_json FROM copilot_tasks t
             JOIN security_users u ON u.business_id=t.business_id AND u.role='owner' AND u.active=1
             JOIN security_sessions s ON s.user_id=u.user_id AND s.token_hash=? AND s.expires_at>?
             WHERE t.business_id=p.business_id AND t.status='open' AND t.kind='collect_early'
             ORDER BY t.due_on,t.created_at,t.id LIMIT 1)
            FROM security_policies p WHERE p.business_id=?""", (actor.token_hash,sec.now(),actor.business_id)).fetchone()
        if policy is None:sec.fail(409,'POLICY_NOT_CONFIGURED')
        action=json.loads(policy[2]) if policy[2] else None
        next_collection=dict(due_on=action['collect_by'],amount_minor=action['amount_minor']) if action else None
        receipts = open_receipts(db, actor.business_id)
        week=project_cashflow(ds,ScenarioRequest(as_of=ds.as_of,horizon_days=7,reserve_minor=policy[0],delay_inflows_days=0), receipts)
        month=project_cashflow(ds,ScenarioRequest(as_of=ds.as_of,horizon_days=30,reserve_minor=policy[0],delay_inflows_days=0), receipts)
        month["collection_plan"] = collection_plan(ds, month, receipts)
        notes=record_notes.read(db, actor.business_id, events)
        for entry in events:
            if entry['kind']=='CASH_CORRECTED':
                notes[entry['trace_id']]=notes.get(entry['payload']['original_id'],'')
        return dict(next_collection=next_collection,dataset_revision=sec.fingerprint(sec.original_dataset(db,actor.business_id)),snapshot=snapshot(ds),invoices=[i.model_dump(mode='json') for i in ds.invoices],events=[dict(e.model_dump(mode='json'),note=notes.get(e.source_record_id,'')) for e in ds.events],proposals=[sec.proposal_view(row) for row in rows],audit=dict(events=events,chain_verified=True,tenant_chain_head=events[-1]['event_hash'] if events else '0'*64),week=week,month=month,policy=dict(reserve_minor=policy[0],payment_limit_minor=policy[1],second_person=sec.has_approver(db,actor.business_id)),simulation_only=True)


@router.get('/workspace/export')
def export_workspace(actor=Depends(principal),db=Depends(db_connection)):
    db.execute('BEGIN')
    with db:
        events=sec.verified_audit(db,actor.business_id)
        original=sec.original_dataset(db,actor.business_id)
        current=sec.runtime_dataset(db,actor.business_id,verified_history=events)
        collections = {}
        if sec.recheck(db,actor).role == 'owner':
            from api.app.receivables import customer_book
            collections = customer_book(db,actor.business_id)
            collections['settlements'] = [dict(row) for row in db.execute(
                'SELECT id,receivable_id,amount_minor,cash_record_id,created_at FROM receivable_settlements WHERE business_id=? ORDER BY created_at,id',
                (actor.business_id,))]
            from api.app.business import export as business_export
            collections['business_intelligence'] = business_export(db, actor.business_id)
        return dict(**collections, cash_notes=record_notes.read(db, actor.business_id, events),format_version=1,workspace_id=actor.business_id,original_dataset=original.model_dump(mode='json'),current_dataset=current.model_dump(mode='json'),audit_events=events,recordkeeping_only=True,simulation_only=True)


@router.get('/agent/status')
def agent_status(actor=Depends(principal)):
    import os
    from api.app.agents.provider import ChatProvider,ProviderError
    from api.app.agents.fallback import configured_secondary
    from api.app.gateway import disclosure, REGISTRY
    providers=disclosure()
    profile=os.environ.get('SAFELEDGER_LLM_PROFILE','gemini_free')
    reason=None
    try:ChatProvider();configured=True
    except ProviderError as exc:configured=False;reason=exc.code
    return dict(providers=providers,provider_name=providers[0]['label'] if providers else REGISTRY['providers'].get(profile,REGISTRY['providers']['compatible'])['label'],configured=bool(providers),configuration_error=reason,availability_checked=False,fallback_provider='OpenAI',fallback_configured=configured_secondary(),free_only=os.environ.get('SAFELEDGER_ALLOW_PAID_AI')!='1',simulation_only=True)


from pydantic import StrictBool

class SyntheticDiagnostic(Contract):
    confirm_synthetic_test: StrictBool = False

@router.post('/agent/diagnostic')
def agent_diagnostic(body:SyntheticDiagnostic,actor=Depends(principal),db=Depends(db_connection)):
    if actor.role!='owner':sec.fail(403,'OWNER_REQUIRED')
    if not body.confirm_synthetic_test:sec.fail(403,'SYNTHETIC_TEST_CONFIRMATION_REQUIRED')
    sec.budget(db,'provider-diagnostic:'+actor.business_id,2)
    sec.budget(db,'public-ai-global',8)
    from api.app.agents.diagnostic import synthetic_check
    # This function has no dataset access. No account identifiers or records are sent.
    return synthetic_check(db=db,business_id=actor.business_id)
