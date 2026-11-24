"""Market data and research for each business.

Every figure is an observation with its source, when it was observed and fetched, and what kind it is:
`delayed` (free public feed), `derived` (computed from delayed feeds), `owner` (typed in by the owner) or
`research` (found by web-grounded AI search, with a citation). Nothing is ever filled in when a source fails:
the indicator says it is unavailable, or shows the last value marked stale.

Research only sends public facts (industry, niche, area, product names, competitor names), never costs or sales,
and only after the owner allowed it in setup.
"""
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import Field

from api.app import business_catalog as cat
from api.app import security as sec
from api.app.contracts import Contract
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/market')
log = logging.getLogger('capzoryx.market')
TRANSPORT = None  # tests inject an httpx.MockTransport
OUNCE_GRAMS = Decimal('31.1034768')
BHORI_GRAMS = Decimal('11.664')
RESEARCH_EVERY = 6 * 3600
METALS_SOURCES = ('https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json',
                  'https://latest.currency-api.pages.dev/v1/currencies/usd.json')
GEMINI_MODELS = ('gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 'gemini-flash-lite-latest')
GROQ_SEARCH_MODELS = ('groq/compound-mini', 'groq/compound', 'compound-beta-mini', 'compound-beta')  # built-in web search
GROQ_BROWSER_MODELS = ('openai/gpt-oss-120b', 'openai/gpt-oss-20b')  # web search through the browser_search tool
_failures = {}  # indicator -> (time, reason) of the last failed fetch, so a dead source is not hammered
_lock = threading.Lock()

# key: (label, unit, kind of source, refresh seconds, stale after seconds, purity for derived metals)
INDICATORS = {
    'fx_usd': ('US dollar exchange rate', 'per USD', 'delayed', 3600, 2 * 86400, None),
    'gold_intl': ('International gold price (24K, before local charges)', 'per {weight}', 'derived', 6 * 3600, 3 * 86400, Decimal(1)),
    'silver_intl': ('International silver price (before local charges)', 'per {weight}', 'derived', 6 * 3600, 3 * 86400, Decimal(1)),
    'gold_22k_local': ('Local 22K gold dealer price', 'per {weight}', 'quote', 0, 3 * 86400, None),
    'gold_21k_local': ('Local 21K gold dealer price', 'per {weight}', 'quote', 0, 3 * 86400, None),
    'gold_18k_local': ('Local 18K gold dealer price', 'per {weight}', 'quote', 0, 3 * 86400, None),
    'gold_traditional_local': ('Local traditional gold dealer price', 'per {weight}', 'quote', 0, 3 * 86400, None),
    'silver_local': ('Local silver dealer price', 'per {weight}', 'quote', 0, 7 * 86400, None),
}
USD_LINKED = {'jewelry', 'electronics', 'wholesale', 'manufacturing', 'pharmacy', 'automotive', 'construction', 'ecommerce', 'it'}
INDUSTRY_INDICATORS = {'jewelry': ('gold_intl', 'silver_intl')}
PRODUCT_INDICATORS = {'gold_22k': ('gold_intl', 'gold_22k_local'), 'gold_21k': ('gold_intl', 'gold_21k_local'),
                      'gold_18k': ('gold_intl', 'gold_18k_local'), 'gold_traditional': ('gold_intl', 'gold_traditional_local'),
                      'silver': ('silver_intl', 'silver_local')}


PRODUCT_STALE = 14 * 86400
TRACK_LIMIT = 25


def product_indicator(product_id):
    """Every product can have its own market price (what it sells for around you), from quotes or cited research."""
    return 'p:' + product_id


def stale_after(indicator):
    return INDICATORS[indicator][4] if indicator in INDICATORS else PRODUCT_STALE


def tracked(items):
    """Products whose market price is followed: the most valuable first. Gold and silver use the shared metal prices."""
    own = [p for p in items if (p.get('market_key') or '') not in PRODUCT_INDICATORS]
    value = lambda p: Decimal(p.get('quantity') or 0) * (p.get('cost_minor') or p.get('price_minor') or 0)
    return sorted(own, key=lambda p: -value(p))[:TRACK_LIMIT]


def product_view(db, business, product, currency, now):
    unit = 'per ' + (product.get('unit') or 'unit')
    last = latest(db, business, product_indicator(product['id']), currency, unit)
    status = 'needs_quote' if not last else 'stale' if now - datetime.fromisoformat(last['observed_at']).timestamp() > PRODUCT_STALE else 'ok'
    gap = None
    if last and product.get('price_minor'):
        gap = round((product['price_minor'] - last['value_minor']) * 10000 / last['value_minor'])
    return dict(id=product['id'], name=product['name'], indicator=product_indicator(product['id']), unit=unit, currency=currency,
                price_minor=product.get('price_minor'), cost_minor=product.get('cost_minor'), latest=last, status=status, your_price_gap_bp=gap)


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS market_observations(id TEXT PRIMARY KEY, business_id TEXT NOT NULL DEFAULT '', indicator TEXT NOT NULL,
        value_minor INTEGER NOT NULL CHECK(value_minor > 0), currency TEXT NOT NULL, unit TEXT NOT NULL, source TEXT NOT NULL,
        url TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL, fetched_at INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('delayed','derived','owner','research')), confidence TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS market_obs_latest ON market_observations(indicator, currency, unit, business_id, fetched_at);
      CREATE TRIGGER IF NOT EXISTS market_obs_no_update BEFORE UPDATE ON market_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
      CREATE TABLE IF NOT EXISTS market_watch(business_id TEXT NOT NULL, indicator TEXT NOT NULL, added_at INTEGER NOT NULL,
        PRIMARY KEY(business_id, indicator));
      CREATE TABLE IF NOT EXISTS research_runs(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, status TEXT NOT NULL
        CHECK(status IN ('running','done','unavailable','failed')), result_enc TEXT, key_tag TEXT, reason TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL, finished_at INTEGER);
      CREATE INDEX IF NOT EXISTS research_by_business ON research_runs(business_id, created_at);
    """)
    # Older databases: remember what each run researched, so it is repeated only when that changes. Check first:
    # on the remote database any failed statement closes the whole stream, so "try and ignore" breaks startup.
    if 'inputs' not in {row[1] for row in db.execute('PRAGMA table_info(research_runs)').fetchall()}:
        db.execute("ALTER TABLE research_runs ADD COLUMN inputs TEXT NOT NULL DEFAULT ''")


# ---------- what each business watches ----------
def weight_unit(currency):
    return 'bhori' if currency == 'BDT' else 'gram'


def wanted(industry, market_keys, currency):
    # The dollar rate matters to businesses that import or price in dollars; for others it is noise.
    keys = ['fx_usd'] if currency != 'USD' and industry in USD_LINKED else []
    keys += INDUSTRY_INDICATORS.get(industry, ())
    for key in market_keys:
        keys += PRODUCT_INDICATORS.get(key, ())
    return list(dict.fromkeys(keys))


def refresh_watch(db, business):
    """Rebuild the watch list from the profile and products. Called whenever either changes."""
    row = db.execute('SELECT industry FROM biz_profile WHERE business_id=?', (business,)).fetchone()
    keys = [r[0] for r in db.execute('SELECT DISTINCT market_key FROM biz_products WHERE business_id=? AND market_key IS NOT NULL', (business,))]
    target = wanted(row[0] if row else '', keys, sec.original_dataset(db, business).currency)
    current = {r[0] for r in db.execute('SELECT indicator FROM market_watch WHERE business_id=?', (business,))}
    for key in current - set(target):
        db.execute('DELETE FROM market_watch WHERE business_id=? AND indicator=?', (business, key))
    for key in target:
        if key not in current:
            db.execute('INSERT INTO market_watch VALUES (?,?,?)', (business, key, sec.now()))
    return target


# ---------- providers ----------
def get_json(url):
    with httpx.Client(transport=TRANSPORT, timeout=8, trust_env=False, follow_redirects=False) as client:
        response = client.get(url, headers={'Accept': 'application/json'})
        response.raise_for_status()
        if len(response.content) > 600_000:
            raise ValueError('response too large')
        return response.json()


def usd_table():
    """Units per 1 USD, including XAU and XAG, from the free currency API (daily, so 'delayed')."""
    last = None
    for url in METALS_SOURCES:
        try:
            body = get_json(url)
            table = {str(k).upper(): Decimal(str(v)) for k, v in body['usd'].items() if isinstance(v, (int, float, str))}
            if table.get('XAU', 0) > 0 and table.get('XAG', 0) > 0:
                return table, str(body.get('date') or date.today().isoformat()), url
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
            last = error
    raise ValueError(f'metal prices unavailable ({type(last).__name__ if last else "no data"})')


def fetch_public(indicator, currency):
    """[(value_minor, unit, source, url, observed_at, kind, confidence)] for a public indicator, or raises."""
    table, day, url = usd_table()
    local = table.get(currency)
    if not local or local <= 0:
        raise ValueError(f'no {currency} rate')
    observed = day + 'T00:00:00+00:00'
    if indicator == 'fx_usd':
        return (int((local * 100).to_integral_value()), 'per USD', 'Currency API (daily reference rates)', url, observed, 'delayed', 'medium')
    metal = 'XAU' if indicator == 'gold_intl' else 'XAG'
    weight = weight_unit(currency)
    grams = BHORI_GRAMS if weight == 'bhori' else Decimal(1)
    per_unit = (Decimal(1) / table[metal]) * local * grams / OUNCE_GRAMS
    return (int((per_unit * 100).to_integral_value()), 'per ' + weight, f'Derived from international {metal}/USD and USD/{currency} reference rates',
            url, observed, 'derived', 'low')


def latest(db, business, indicator, currency, unit):
    row = db.execute("""SELECT value_minor,source,url,observed_at,fetched_at,kind,confidence FROM market_observations
                        WHERE indicator=? AND currency=? AND unit=? AND business_id IN ('',?) ORDER BY fetched_at DESC, rowid DESC LIMIT 1""",
                     (indicator, currency, unit, business)).fetchone()
    return None if not row else dict(zip(('value_minor', 'source', 'url', 'observed_at', 'fetched_at', 'kind', 'confidence'), row))


def record(db, business, indicator, currency, unit, value, source, url, observed, kind, confidence):
    db.execute('INSERT INTO market_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, business, indicator, value, currency, unit, source[:160], url[:500], observed, sec.now(), kind, confidence))


def due(db, indicator, currency, now):
    """Whether a public indicator should be fetched now (not a quote, not fresh, not a source that just failed)."""
    _, unit_t, source_kind, every, _, _ = INDICATORS[indicator]
    if source_kind == 'quote':
        return False
    have = latest(db, '', indicator, currency, unit_t.format(weight=weight_unit(currency)))
    return not (have and now - have['fetched_at'] < every)


def try_fetch(indicator, currency, now):
    """Network only, never inside a database transaction. Returns an observation tuple or the failure reason."""
    with _lock:
        failed = _failures.get((indicator, currency))
    if failed and now - failed[0] < 900:
        return failed[1]
    try:
        return fetch_public(indicator, currency)
    except (ValueError, ZeroDivisionError, InvalidOperation) as error:
        reason = f'Source not reachable right now: {error}'
        with _lock:
            _failures[(indicator, currency)] = (now, reason)
        return reason


def indicator_view(db, business, indicator, currency, now, reason=''):
    label, unit_t, source_kind, _, stale_after, _ = INDICATORS[indicator]
    unit = unit_t.format(weight=weight_unit(currency))
    last = latest(db, business, indicator, currency, unit)
    status = 'ok'
    if not last:
        status = 'needs_quote' if source_kind == 'quote' else 'unavailable'
    elif now - last['fetched_at'] > stale_after:
        status = 'stale'
    elif reason:
        status = 'stale'
    return dict(key=indicator, label=label, unit=unit, currency=currency, source_kind=source_kind, latest=last, status=status,
                reason=reason if status in ('unavailable', 'stale') else '')


# ---------- research (web-grounded, optional) ----------
def gemini_key():
    return (os.getenv('CAPZORYX_CHAT_GEMINI_KEY') or os.getenv('SAFELEDGER_LLM_API_KEY') or '').strip()


def research_prompt(profile, products, currency, notes=()):
    ident = profile.get('identity') or {}
    ops = profile.get('operations') or {}
    label = cat.INDUSTRIES.get(ident.get('industry', ''), ('',))[0]
    names = ', '.join(sorted({p['name'] for p in products})[:15]) or 'not listed'
    keys = sorted({k for p in products for k in PRODUCT_INDICATORS.get(p.get('market_key') or '', ()) if k.endswith('_local')})
    items = tracked(products)[:10]
    asks = [f"{k} (local dealer price per {weight_unit(currency)})" for k in keys] + \
           [f"{n} ({p['name']}, per {p.get('unit') or 'unit'})" for n, p in enumerate(items, start=1)]
    price_ask = (f"\nPRICES: for each of these, give the typical current selling price in {ident.get('city')} in {currency} seen in shops "
                 f"or online listings, as 'PRICE: key-or-number | number | YYYY-MM-DD': {'; '.join(asks)}. "
                 "Only prices you found in a search result.") if asks else ''
    return (f"Research this small business's market using Google Search. Business: {ident.get('niche')} ({label}); sells as "
            f"{', '.join(ident.get('business_types', []))}; located in {ident.get('city')}, {ident.get('country')}. Products: {names}. "
            f"Competitors the owner named: {', '.join(ops.get('competitors', [])) or 'none'}.\n"
            "Reply in plain lines only, no markdown tables.\n"
            "MARKET: 3 to 6 lines starting 'FINDING:' about demand, prices, supply, seasonality, regulation or risks for this market in "
            "this country and city, each with the date or period it refers to.\n"
            "COMPETITORS: up to 8 real, currently operating businesses comparable in size and location, as "
            "'COMPETITOR: name | direct, indirect, emerging, leader or benchmark | area | one-line reason'. Do not invent businesses."
            + price_ask + owner_notes(notes) + "\nIf something cannot be found, leave it out. Never estimate revenue or profit of private businesses.")


def owner_notes(notes):
    """What the owner told Capzoryx about the business (only notes they allowed for research), to focus the search."""
    if not notes:
        return ''
    lines = [f"- {n['kind']}: {n['title']}: {' '.join(n['text'].split())[:500]}" for n in notes[:6]]
    return ("\nThe owner shared these notes about the business. Use them to choose what to search for (products, places, "
            "competitors, customers); do not repeat their private figures back:\n" + '\n'.join(lines))


def call_gemini(prompt):
    key = gemini_key()
    last = None
    for model in GEMINI_MODELS:
        for attempt in range(2):  # "high demand" (503) usually clears within seconds
            try:
                with httpx.Client(transport=TRANSPORT, timeout=60, trust_env=False, follow_redirects=False) as client:
                    r = client.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                                    headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                                    json={'contents': [{'role': 'user', 'parts': [{'text': prompt}]}], 'tools': [{'google_search': {}}],
                                          'generationConfig': {'temperature': 0.2, 'maxOutputTokens': 2048}})
                if r.status_code == 503 and attempt == 0:
                    time.sleep(0 if TRANSPORT else 3)
                    continue
                if r.status_code in (400, 403, 404, 429, 500, 503):
                    last = f'{model} returned {r.status_code}'
                    break
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as error:
                last = type(error).__name__
                break
    raise RuntimeError(last or 'no model answered')


def groq_key():
    from api.app.chat import llm
    return next((p.key for p in llm.PROVIDERS if p.name == 'groq'), '')


def _results(node, out):
    """Every {url, title, content} search result anywhere in Groq's executed_tools, whatever its exact nesting."""
    if isinstance(node, dict):
        if isinstance(node.get('url'), str):
            out.append(dict(url=node['url'], title=str(node.get('title') or ''), content=str(node.get('content') or '')))
        for value in node.values():
            _results(value, out)
    elif isinstance(node, list):
        for value in node:
            _results(value, out)
    return out


def call_groq(prompt):
    """Web research through Groq's search-enabled models, reshaped like a grounded Gemini answer.

    Groq returns the search results it used but not which sentence came from which page, so a line is linked to a
    page only when the page supports it: a price line when that exact number appears on the page, a competitor when
    its name does, a finding when it shares several distinctive words with the page. Anything else stays unverified.
    """
    key = groq_key()
    last = None
    try:  # model names change: use the search models this account actually has
        with httpx.Client(transport=TRANSPORT, timeout=15, trust_env=False, follow_redirects=False) as client:
            listed = {m.get('id', '') for m in client.get('https://api.groq.com/openai/v1/models',
                                                          headers={'Authorization': 'Bearer ' + key}).json().get('data', [])}
    except (httpx.HTTPError, ValueError, AttributeError):
        listed = set()
    compound = [m for m in GROQ_SEARCH_MODELS if m in listed] + sorted(m for m in listed if 'compound' in m and m not in GROQ_SEARCH_MODELS)
    plans = [(m, None) for m in (compound or ([] if listed else GROQ_SEARCH_MODELS))]
    plans += [(m, [{'type': 'browser_search'}]) for m in GROQ_BROWSER_MODELS if not listed or m in listed]
    for model, tools in plans:
        try:
            payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.2, 'max_tokens': 2048}
            if tools:
                payload['tools'] = tools
            with httpx.Client(transport=TRANSPORT, timeout=150, trust_env=False, follow_redirects=False) as client:
                r = client.post('https://api.groq.com/openai/v1/chat/completions', headers={'Authorization': 'Bearer ' + key}, json=payload)
            if r.status_code != 200:
                try:
                    detail = str(r.json().get('error', {}).get('message', ''))[:120]
                except ValueError:
                    detail = ''
                last = f'{model} returned {r.status_code} {detail}'.strip()
                continue
            message = r.json()['choices'][0]['message']
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
            last = type(error).__name__
            continue
        text = message.get('content') or ''
        pages = [p for p in _results(message.get('executed_tools') or [], []) if re.match(r'^https?://', p['url'])][:15]
        chunks, supports = [], []
        for page in pages:
            chunks.append({'web': {'uri': page['url'], 'title': page['title'] or urlsplit(page['url']).hostname or ''}})
        digits = lambda value: re.sub(r'[^\d]', '', value)
        for raw in text.splitlines():
            head, _, rest = raw.strip(' *-•\t').partition(':')
            head, rest = head.strip().upper(), rest.strip()
            if not rest:
                continue
            found = []
            for index, page in enumerate(pages):
                body = (page['title'] + ' ' + page['content']).lower()
                if head == 'PRICE':
                    parts = [x.strip() for x in rest.split('|')]
                    number = digits(parts[1]) if len(parts) > 1 else ''
                    if number and number in digits(body.replace(',', '')):
                        found.append(index)
                elif head == 'COMPETITOR':
                    name = rest.split('|')[0].strip().lower()
                    if len(name) > 2 and name in body:
                        found.append(index)
                elif head == 'FINDING':
                    words = {w for w in re.findall(r'[a-z]{5,}', rest.lower())}
                    if len(words & set(re.findall(r'[a-z]{5,}', body))) >= 3:
                        found.append(index)
            if found:
                supports.append({'segment': {'text': rest}, 'groundingChunkIndices': found[:3]})
        return {'candidates': [{'content': {'parts': [{'text': text}]},
                                'groundingMetadata': {'groundingChunks': chunks, 'groundingSupports': supports, 'webSearchQueries': []}}]}
    raise RuntimeError(last or 'no model answered')


def research_service():
    return bool(gemini_key() or groq_key())


def call_research(prompt):
    """Gemini with Google Search first; Groq's web search when Gemini is busy, out of quota or not configured."""
    errors = []
    for available, call in ((gemini_key, call_gemini), (groq_key, call_groq)):
        if not available():
            continue
        try:
            body = call(prompt)
            if parse_research(body)['findings'] or parse_research(body)['competitors']:
                return body
            errors.append('empty answer')
        except RuntimeError as error:
            errors.append(str(error))
    raise RuntimeError('; '.join(errors) or 'no research service configured')


def parse_research(body):
    candidate = (body.get('candidates') or [{}])[0]
    text = ''.join(p.get('text', '') for p in (candidate.get('content') or {}).get('parts', []))
    meta = candidate.get('groundingMetadata') or {}
    safe = lambda u: u if isinstance(u, str) and re.match(r'^https?://[^\s]{1,490}$', u) else ''  # only web links are ever shown
    chunks = [dict(url=safe((c.get('web') or {}).get('uri', '')), title=str((c.get('web') or {}).get('title', ''))[:120]) for c in meta.get('groundingChunks', [])]
    supports = [((s.get('segment') or {}).get('text', ''), s.get('groundingChunkIndices', [])) for s in meta.get('groundingSupports', [])]

    def sources(line):
        found = []
        for segment, indexes in supports:
            if segment and (segment.strip() in line or line in segment):
                found += [chunks[i] for i in indexes if i < len(chunks) and chunks[i]['url']]
        return [dict(t) for t in {tuple(sorted(s.items())) for s in found}][:4]

    findings, competitors, prices = [], [], []
    for raw in text.splitlines():
        line = raw.strip(' *-•\t')
        head, _, rest = line.partition(':')
        head = head.strip().upper()
        if head == 'FINDING' and rest.strip():
            findings.append(dict(text=rest.strip()[:400], sources=sources(line)))
        elif head == 'COMPETITOR':
            parts = [p.strip() for p in rest.split('|')]
            if parts and parts[0]:
                kind = (parts[1].lower() if len(parts) > 1 else '')
                competitors.append(dict(name=parts[0][:80], kind=kind if kind in ('direct', 'indirect', 'emerging', 'leader', 'benchmark') else 'direct',
                                        area=(parts[2] if len(parts) > 2 else '')[:80], why=(parts[3] if len(parts) > 3 else '')[:200], sources=sources(line)))
        elif head == 'PRICE':
            parts = [p.strip() for p in rest.split('|')]
            target = parts[0] if parts and parts[0] in INDICATORS and INDICATORS[parts[0]][2] == 'quote' else \
                (int(parts[0]) if parts and parts[0].isdigit() else None)
            if len(parts) >= 3 and target is not None:
                try:
                    value = int((Decimal(re.sub(r'[^\d.]', '', parts[1])) * 100).to_integral_value())
                    day = date.fromisoformat(parts[2][:10]).isoformat()
                except (InvalidOperation, ValueError):
                    continue
                prices.append(dict(indicator=target if isinstance(target, str) else None, item=target if isinstance(target, int) else None,
                                   value_minor=value, observed_on=day, sources=sources(line)))
    return dict(findings=findings[:8], competitors=competitors[:8], prices=prices,
                sources=[c for c in chunks if c['url']][:12], searches=meta.get('webSearchQueries', [])[:6])


def run_research(identity, business):
    """Runs after the response, with its own connection. Stores the result sealed, or the honest reason it failed."""
    from api.app.business import load_profile, notes, products
    from api.app.database import connect
    with closing(connect()) as db:
        with sec.transaction(db):
            profile, _, _, _ = load_profile(db, business)
            items = products(db, business)
            currency = sec.original_dataset(db, business).currency
            shared = notes(db, business, research_only=True, limit=6)
    status, reason, result = 'done', '', None
    try:
        result = parse_research(call_research(research_prompt(profile, items, currency, shared)))
        if not result['findings'] and not result['competitors']:
            status, reason = 'failed', 'The search returned nothing usable. Try again later.'
    except RuntimeError as error:
        log.warning('market research failed: %s', error)  # details for the operator; the owner gets a plain message
        status, reason = 'failed', 'The research services are busy right now. Research will try again automatically within a few hours.'
    # A web search can take minutes; a fresh connection saves the result, so an idle database stream cannot lose it.
    with closing(connect()) as db, sec.transaction(db):
        now = sec.now()
        if result:
            items = tracked(items)[:10]
            for price in result['prices']:
                if price.get('item'):
                    if not 1 <= price['item'] <= len(items):
                        continue
                    product = items[price['item'] - 1]
                    price['indicator'], unit = product_indicator(product['id']), 'per ' + (product.get('unit') or 'unit')
                else:
                    unit = 'per ' + weight_unit(currency)
                if price['sources'] and price['value_minor'] > 0:  # only cited prices become observations
                    cite = price['sources'][0]
                    record(db, business, price['indicator'], currency, unit, price['value_minor'],
                           'Web research: ' + (cite['title'] or 'cited page'), cite['url'], price['observed_on'] + 'T00:00:00+00:00',
                           'research', 'medium')
            key = customer_key(db, business, create=True)
            box = seal(key, sec.canonical(result).encode(), identity + '/research')
            db.execute('UPDATE research_runs SET status=?,result_enc=?,key_tag=?,reason=?,finished_at=? WHERE id=?',
                       (status, box, key_tag(key), reason, now, identity))
        else:
            db.execute('UPDATE research_runs SET status=?,reason=?,finished_at=? WHERE id=?', (status, reason, now, identity))


STALE_RUN = 600


def research_view(db, business):
    row = db.execute('SELECT id,status,result_enc,key_tag,reason,created_at,finished_at,inputs FROM research_runs WHERE business_id=? '
                     'ORDER BY created_at DESC, rowid DESC LIMIT 1', (business,)).fetchone()
    if not row:
        return None
    result = None
    if row[2]:
        key = customer_key(db, business)
        if key and key_tag(key) == row[3]:
            result = json.loads(unseal(key, row[2], row[0] + '/research'))
    status, reason = row[1], row[4]
    if status == 'running' and sec.now() - row[5] > STALE_RUN:  # the server restarted mid-search: say so instead of spinning
        status, reason = 'failed', 'Research was interrupted. It will try again automatically, or you can start it again now.'
    return dict(id=row[0], status=status, reason=reason, started_at=row[5], finished_at=row[6], result=result, inputs=row[7])


# ---------- routes ----------
class Quote(Contract):
    indicator: str = Field(min_length=2, max_length=80)
    value_minor: int = Field(gt=0, le=10**15)
    observed_on: date
    source: str = Field(min_length=2, max_length=120)
    url: str = Field(default='', max_length=300)


def watched(db, business):
    return [r[0] for r in db.execute('SELECT indicator FROM market_watch WHERE business_id=? ORDER BY added_at, rowid', (business,))]


def overview(db, business, reasons=None):
    from api.app.business import load_profile, products
    profile, step, completed, updated = load_profile(db, business)
    currency = sec.original_dataset(db, business).currency
    keys = watched(db, business)
    now = sec.now()
    reasons = {k: (reasons or {}).get(k, '') for k in keys}
    industry = (profile.get('identity') or {}).get('industry', '')
    consent = profile.get('consent') or {}
    no_feed = bool(industry) and industry not in INDUSTRY_INDICATORS
    return dict(currency=currency, profile_ready=bool(profile.get('identity')), industry=industry,
                indicators=[indicator_view(db, business, k, currency, now, reasons[k]) for k in keys],
                products=[product_view(db, business, p, currency, now) for p in tracked(products(db, business))],
                coverage_note=(f'No free live price feed covers {cat.INDUSTRIES.get(industry, ("this industry",))[0].lower()}, so your '
                               'products are priced from what you see in the market and from cited web research.') if no_feed else '',
                research=research_view(db, business), research_allowed=bool(consent.get('external_research')),
                research_configured=research_service(), owner_competitors=(profile.get('operations') or {}).get('competitors', []),
                simulation_only=True)


@router.get('')
def market(actor=Depends(principal), db=Depends(db_connection)):
    now = sec.now()
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        refresh_watch(db, business)
        currency = sec.original_dataset(db, business).currency
        pending = [k for k in watched(db, business) if due(db, k, currency, now)]
    fetched = {k: try_fetch(k, currency, now) for k in pending}
    with owner_transaction(db, actor) as owner:
        reasons = {}
        for key, got in fetched.items():
            if isinstance(got, str):
                reasons[key] = got
            else:
                value, unit, source, url, observed, kind, confidence = got
                record(db, '', key, currency, unit, value, source, url, observed, kind, confidence)
        return overview(db, business, reasons)


@router.post('/quotes', status_code=201)
def add_quote(body: Quote, actor=Depends(principal), db=Depends(db_connection)):
    if body.observed_on > date.today():
        sec.fail(422, 'DATE_IN_FUTURE')
    if body.url and not re.match(r'^https?://[^\s]+$', body.url):
        sec.fail(422, 'INVALID_URL')
    with owner_transaction(db, actor) as owner:
        currency = sec.original_dataset(db, owner.business_id).currency
        if body.indicator.startswith('p:'):
            from api.app.business import products
            product = next((p for p in products(db, owner.business_id) if product_indicator(p['id']) == body.indicator), None)
            if not product:
                sec.fail(404, 'PRODUCT_NOT_FOUND')
            unit = 'per ' + (product.get('unit') or 'unit')
        else:
            if not db.execute('SELECT 1 FROM market_watch WHERE business_id=? AND indicator=?', (owner.business_id, body.indicator)).fetchone():
                sec.fail(404, 'INDICATOR_NOT_WATCHED')
            unit = INDICATORS[body.indicator][1].format(weight=weight_unit(currency))
        record(db, owner.business_id, body.indicator, currency, unit, body.value_minor, body.source, body.url,
               body.observed_on.isoformat() + 'T00:00:00+00:00', 'owner', 'owner')
        sec.audit(db, owner, owner.business_id, 'MARKET_QUOTE_ADDED', {'indicator': body.indicator})
        return overview(db, owner.business_id)


REFRESH_EVERY = 7 * 86400  # background research: weekly, or sooner when the profile or products change


def research_inputs(db, business):
    """A fingerprint of what research sends (industry, area, product and competitor names, notes the owner shared)."""
    from api.app.business import load_profile, notes, products
    profile = load_profile(db, business)[0]
    ident, ops = profile.get('identity') or {}, profile.get('operations') or {}
    public = dict(industry=ident.get('industry'), niche=ident.get('niche'), city=ident.get('city'), country=ident.get('country'),
                  types=ident.get('business_types'), competitors=ops.get('competitors'), products=sorted(p['name'] for p in products(db, business)),
                  notes=sorted(n['id'] for n in notes(db, business, research_only=True, limit=6)))
    return sec.signature('research-inputs', sec.canonical(public))


def queue_research(db, business, manual=False):
    """Record a new research run and return its id, or None when one is running, still fresh, or impossible right now."""
    last = research_view(db, business)
    now = sec.now()
    inputs = research_inputs(db, business)
    if last and last['status'] == 'running' and now - last['started_at'] < STALE_RUN:
        return None
    fresh = last and last['status'] == 'done' and last.get('inputs') == inputs
    if fresh and now - last['started_at'] < (RESEARCH_EVERY if manual else REFRESH_EVERY):
        if manual:
            sec.fail(429, 'RESEARCH_RECENT')
        return None
    if not manual and last and last['status'] in ('failed', 'unavailable') and last.get('inputs') == inputs and now - last['started_at'] < 6 * 3600:
        return None  # do not retry a failing service every pass
    identity = uuid.uuid4().hex
    if not research_service():
        if manual or not last:
            db.execute("INSERT INTO research_runs(id,business_id,status,reason,created_at,finished_at,inputs) VALUES (?,?,'unavailable',?,?,?,?)",
                       (identity, business, 'No web research service is configured on this server, so no research was done.', now, now, inputs))
        return None
    db.execute("INSERT INTO research_runs(id,business_id,status,created_at,inputs) VALUES (?,?,'running',?,?)", (identity, business, now, inputs))
    return identity


@router.post('/research', status_code=202)
def start_research(background: BackgroundTasks, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.business import load_profile
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        profile, _, _, updated = load_profile(db, business)
        if not profile.get('identity'):
            sec.fail(409, 'PROFILE_REQUIRED')
        if not (profile.get('consent') or {}).get('external_research'):
            sec.fail(409, 'RESEARCH_CONSENT_REQUIRED')
        identity = queue_research(db, business, manual=True)
        if identity:
            sec.audit(db, owner, identity, 'MARKET_RESEARCH_STARTED', {})
    if identity:
        background.add_task(run_research, identity, business)
    with sec.transaction(db):
        return overview(db, business)
