"""Privacy classification, quotas and routing for opt-in model inference."""
from collections import OrderedDict, deque
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from api.app import security as sec
from api.app.agents.errors import ProviderError

REGISTRY = json.loads(Path(__file__).with_name('gateway_routes.json').read_text())
request_trace = ContextVar('gateway_request_trace', default=None)
TASKS = REGISTRY['tasks']
LOCK = threading.Lock()
_cache = OrderedDict()
_failures = {}
_open_until = {}


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS ai_token_budgets(business_id TEXT NOT NULL,day TEXT NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(business_id,day));
    CREATE TABLE IF NOT EXISTS model_calls(id TEXT PRIMARY KEY,trace_id TEXT NOT NULL,business_hash TEXT,
        task TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,data_class TEXT NOT NULL,
        fallback_depth INTEGER NOT NULL,latency_ms INTEGER NOT NULL,prompt_tokens INTEGER,
        completion_tokens INTEGER,ok INTEGER NOT NULL,created_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS calls_by_workspace_time ON model_calls(business_hash,created_at);
    CREATE INDEX IF NOT EXISTS calls_by_time ON model_calls(created_at);
    """)


def reset():
    with LOCK:
        _failures.clear()
        _open_until.clear()
        _cache.clear()


def policy(provider):
    profile = provider.profile
    info = dict(REGISTRY['providers'][profile])
    info['host'] = urlsplit(provider.base).hostname
    info['model'] = provider.model
    if profile == 'compatible':
        if provider.model.endswith(':free') or info['host'] == 'openrouter.ai':
            info['allowed'] = ['ui_text']
        elif os.getenv('SAFELEDGER_RECORD_PROVIDER_NO_TRAINING') == '1':
            info.update(may_train_on_data=False, allowed=['ui_text', 'question', 'record_data'])
    return info


def permitted(provider, data_class, consent):
    if provider.profile != 'hisab_lm' and os.getenv('SAFELEDGER_LLM_FREE_TIER_CONFIRMED') != '1':
        raise ProviderError('AI_FREE_TIER_CONFIRMATION_REQUIRED')
    info = policy(provider)
    if data_class not in info['allowed'] or (data_class == 'record_data' and info['may_train_on_data']):
        raise ProviderError('PRIVATE_RECORD_PROVIDER_REQUIRED')
    if data_class != 'ui_text' and not consent:
        raise ProviderError('EXTERNAL_AI_CONSENT_REQUIRED')


def local_tasks():
    return set(os.getenv('SAFELEDGER_LOCAL_LLM_TASKS', '').split(',')) & {'explain','briefing','message','extract_text','agent_plan'}


def providers_for(task, preferred=None, *, include_local=True):
    from api.app.agents.provider import ChatProvider
    configs = []
    if include_local and task in local_tasks() and os.getenv('SAFELEDGER_LOCAL_LLM_URL'):
        configs.append({'profile':'hisab_lm'})
    if preferred:
        configs.append({'model':preferred})
    configs.append({})
    errors = []
    candidates = []
    for options in configs:
        try:
            provider = ChatProvider(**options)
        except ProviderError as exc:
            errors.append(exc)
            continue
        if not any(p.base == provider.base and p.model == provider.model for p in candidates):
            candidates.append(provider)
    if not candidates:
        raise errors[0]
    return candidates


def disclosure():
    from api.app.agents.provider import ChatProvider
    configs = []
    if local_tasks() and os.getenv('SAFELEDGER_LOCAL_LLM_URL'):
        try:
            configs.append(policy(ChatProvider(profile='hisab_lm')))
        except ProviderError:
            pass
    try:
        configured = ChatProvider()
        permitted(configured, 'ui_text', False)
        configs.append(policy(configured))
    except ProviderError:
        pass
    return configs


def record_backup_configured():
    from api.app.agents.provider import ChatProvider
    try:
        candidate = ChatProvider()
        permitted(candidate, 'record_data', True)
    except ProviderError:
        return False
    return True


def cache_get(key):
    with LOCK:
        row = _cache.get(key)
        if row and row[0] > time.monotonic():
            _cache.move_to_end(key)
            return row[1]
        _cache.pop(key, None)


def trip(identity):
    now = time.monotonic()
    with LOCK:
        failures = _failures.setdefault(identity, deque(maxlen=3))
        failures.append(now)
        if len(failures) == 3 and now - failures[0] <= 60:
            _open_until[identity] = now + 300
            failures.clear()


def reserve(db, business, amount):
    day = datetime.now(timezone.utc).date().isoformat()
    try:
        limit = int(os.environ.get('SAFELEDGER_AI_DAILY_TOKENS','250000'))
    except ValueError:
        raise ProviderError('AI_CONFIGURATION_INVALID') from None
    with sec.transaction(db):
        row = db.execute('SELECT tokens FROM ai_token_budgets WHERE business_id=? AND day=?', (business,day)).fetchone()
        if amount + (row[0] if row else 0) > limit:
            raise ProviderError('AI_DAILY_BUDGET_EXCEEDED')
        db.execute("""INSERT INTO ai_token_budgets VALUES (?,?,?)
                      ON CONFLICT(business_id,day) DO UPDATE SET tokens=tokens+excluded.tokens""",
                   (business, day, amount))
    return day


def metered(db, task, business_id, trace_id, provider, model, data_class, prompt_bytes, max_tokens, depth, send):
    identity = provider
    with LOCK:
        if _open_until.get(identity, 0) > time.monotonic():
            raise ProviderError('AI_CIRCUIT_OPEN')
    # Reserve before a network call and charge uncertain outcomes conservatively.
    reserved = prompt_bytes + max_tokens
    day = reserve(db, business_id, reserved)
    started = time.monotonic()
    ok = False
    prompt = completion = None
    try:
        result, usage = send()
        if isinstance(usage, dict):
            a, b = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if all(type(n) is int and 0 <= n <= 10**9 for n in (a,b)):
                prompt, completion = a,b
        ok = True
        return result
    except ProviderError:
        trip(identity)
        raise
    finally:
        with sec.transaction(db):
            if prompt is not None:
                db.execute('UPDATE ai_token_budgets SET tokens=tokens+? WHERE business_id=? AND day=?',
                           (prompt+completion-reserved, business_id, day))
            db.execute('INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uuid4().hex, trace_id, sec.signature('ai-tenant',business_id), task, provider, model, data_class,
                        depth, round(1000*(time.monotonic()-started)), prompt, completion, int(ok), sec.now()))


def complete(task, messages, *, db, business_id, data_class, consent=False, trace_id=None, schema=None,
             max_tokens=600, vision=False, approved_messages=(), include_local=True):
    from api.app import cloud
    spec = TASKS.get(task)
    if not spec or spec['data_class'] != data_class:
        raise ProviderError('AI_DATA_CLASS_MISMATCH')
    if task == 'message' and not approved_messages:
        raise ProviderError('OUTBOUND_ENGINE_FACTS_REQUIRED')
    raw = json.dumps([messages,schema,max_tokens], ensure_ascii=False, separators=(',',':')).encode()
    if len(raw) > (1900000 if vision else 65536):
        raise ProviderError('AI_INPUT_BUDGET_EXCEEDED')
    trace = trace_id or request_trace.get() or uuid4().hex
    preferred = os.getenv('SAFELEDGER_VISION_MODEL') if vision else None
    deadline = time.monotonic() + spec['timeout']
    attempts = 0
    last_error = None
    try:
        for candidate in providers_for(task, preferred, include_local=include_local):
            try:
                permitted(candidate, data_class, consent)
            except ProviderError as exc:
                last_error = exc
                continue
            cache_key = sha256(json.dumps([task,candidate.base,candidate.model,business_id]).encode()+raw).digest()
            if spec['ttl']:
                hit = cache_get(cache_key)
                if hit is not None:
                    return hit
            remaining = deadline - time.monotonic()
            if remaining <= 0 or attempts >= 3:
                break
            try:
                if attempts:
                    time.sleep(min(remaining / 2,random.uniform(0.02,0.12)))
                result = metered(db, task, business_id, trace, candidate.profile, candidate.model, data_class,
                                 len(raw) if not vision else len(raw)//40+1200, max_tokens, attempts,
                                 lambda: cloud.send(candidate,messages,max_tokens,schema,deadline))
            except ProviderError as exc:
                last_error = exc
                if exc.code not in {'AI_QUOTA_EXHAUSTED','AI_PROVIDER_REJECTED','AI_TIMEOUT','AI_TIMEOUT_OR_NETWORK_FAILURE',
                                    'AI_CIRCUIT_OPEN','AI_INVALID_RESPONSE','AI_INCOMPLETE_RESPONSE'}:
                    raise
                attempts += 1
                continue
            if task == 'message':
                from api.app.outbound import verified_draft
                result = verified_draft(result, approved=approved_messages)
            if spec['ttl']:
                with LOCK:
                    _cache[cache_key] = (time.monotonic()+spec['ttl'],result)
                    if len(_cache)>256:
                        _cache.popitem(last=False)
            return result
    except ProviderError as exc:
        if exc.code != 'AI_NOT_CONFIGURED' or not last_error:
            raise
    raise last_error or ProviderError('AI_NOT_CONFIGURED')

def agent_call(db, provider, messages, *, business_id, trace_id, task, **options):
    # Production agent providers pass the record-data policy before a run opens.
    # Standalone evaluation doubles implement the same bounded message interface.
    profile = getattr(provider, 'profile', 'test')
    model = getattr(provider, 'model', 'test')
    payload = json.dumps([messages, options], ensure_ascii=False).encode()
    def send():
        answer = provider.complete(messages, **options)
        calls = getattr(provider, 'telemetry', None)
        return answer, calls[-1].get('usage') if calls else None
    return metered(db, task, business_id, trace_id, profile, model, TASKS[task]['data_class'],
                   len(payload), 600, 0, send)


def translate_labels(db, batch, send):
    if os.getenv('SAFELEDGER_GOOGLE_TRANSLATE_FREE_CONFIRMED') != '1':
        raise ProviderError('AI_FREE_TIER_CONFIRMATION_REQUIRED')
    return metered(db, 'translate_ui', 'system:translations', request_trace.get() or uuid4().hex, 'translate', 'standard',
                   'ui_text', sum(len(text.encode()) for text in batch), 0, 0, lambda:(send(), None))
