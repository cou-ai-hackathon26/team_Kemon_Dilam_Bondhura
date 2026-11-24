"""Owner-scoped inference metadata and published synthetic quality measurements."""
import hmac
import json
import os
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from api.app import security as sec
from api.app.routes import db_connection, principal

router = APIRouter(prefix='/api')
REFERENCE = json.loads(Path(__file__).with_name('quality_reference.json').read_text())


@router.get('/admin/ai-quality')
def ai_quality(request: Request, db=Depends(db_connection)):
    token = request.headers.get('X-Capzoryx-Admin-Token')
    admin = token is not None
    tenant = None
    if admin:
        sec.budgets(db,[('quality-admin-edge:'+sec.client_keys(request)[1],10),('quality-admin-all',60)])
        key = os.getenv('SAFELEDGER_ADMIN_TOKEN','')
        if len(key)<32 or not hmac.compare_digest(token.encode(),key.encode()):
            sec.fail(403,'ADMIN_TOKEN_REQUIRED')
    else:
        actor = principal(request,db)
        if actor.role != 'owner':
            sec.fail(403,'OWNER_REQUIRED')
        sec.budget(db,'quality:'+actor.business_id,15)
        tenant = sec.signature('ai-tenant',actor.business_id)
    db.execute('BEGIN')
    with db:
        if not admin and sec.recheck(db,actor).role != 'owner':
            sec.fail(403,'OWNER_REQUIRED')
        cutoff = sec.now() - 7*86400
        where = 'WHERE created_at>=?' if admin else 'WHERE business_hash=? AND created_at>=?'
        params = (cutoff,) if admin else (tenant,cutoff)
        totals = db.execute('SELECT COUNT(*),COUNT(DISTINCT trace_id),COALESCE(SUM(ok),0) FROM model_calls '+where,params).fetchone()
        recent = db.execute("""SELECT trace_id,task,COUNT(*),SUM(ok),SUM(latency_ms),MAX(created_at)
                               FROM model_calls """+where+""" GROUP BY trace_id,task
                               ORDER BY MAX(created_at) DESC,trace_id DESC LIMIT 30""",params)
        traces = [dict(trace_id=t,task=k,calls=n,successful_calls=ok,latency_ms=ms,created_at=at) for t,k,n,ok,ms,at in recent]
    return dict(scope='all_workspaces' if admin else 'workspace',since=cutoff,
                calls=totals[0],requests=totals[1],successful_calls=totals[2],traces=traces,
                evaluations=REFERENCE,simulation_only=True)
