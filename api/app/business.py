"""Business profile: what the business is, what it sells, how it runs and what the owner wants.

Collected once through a five-step setup that saves every step, so an unfinished profile resumes where it stopped.
Only step 1 is required; missing financial details are listed as gaps instead of blocking setup. Profile and products
are sealed with the workspace key; only the industry and a product's market key stay readable for market monitoring.
"""
import csv
import io
import json
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from pydantic import Field, field_validator

from api.app import business_catalog as cat
from api.app import security as sec
from api.app.contracts import Contract
from api.app.receivables import owner_transaction
from api.app.routes import db_connection, principal
from api.app.services.receivables import customer_key, key_tag, seal, unseal

router = APIRouter(prefix='/api/business')
STEPS = ('identity', 'products', 'operations', 'goals', 'consent')
MAX_PRODUCTS = 5000
MAX_IMPORT_BYTES = 1_000_000
Money = Field(default=None, ge=0, le=10**15)
Short = Field(default='', max_length=80)


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS biz_profile(business_id TEXT PRIMARY KEY, data_enc TEXT NOT NULL, key_tag TEXT NOT NULL,
        industry TEXT NOT NULL DEFAULT '', step INTEGER NOT NULL DEFAULT 0, completed_at INTEGER, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS biz_products(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, data_enc TEXT NOT NULL,
        key_tag TEXT NOT NULL, market_key TEXT, dedupe TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS biz_products_unique ON biz_products(business_id, dedupe);
      CREATE TABLE IF NOT EXISTS biz_notes(id TEXT PRIMARY KEY, business_id TEXT NOT NULL, data_enc TEXT NOT NULL,
        key_tag TEXT NOT NULL, kind TEXT NOT NULL, research INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS biz_notes_business ON biz_notes(business_id, created_at);
    """)


# ---------- inputs ----------
def clean_url(value):
    value = (value or '').strip()
    if not value:
        return ''
    if not re.match(r'^https?://', value, re.I):
        value = 'https://' + value
    parts = urlsplit(value)
    if parts.scheme.lower() not in ('http', 'https') or not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', (parts.hostname or '').lower()):
        raise ValueError('Enter a web address like example.com')
    return value[:200]


class Identity(Contract):
    name: str = Field(min_length=2, max_length=80)
    industry: str
    niche: str = Field(min_length=2, max_length=80)
    business_types: list[str] = Field(min_length=1, max_length=6)
    country: str = Field(min_length=2, max_length=60)
    city: str = Field(min_length=2, max_length=80)
    website: str = ''
    facebook: str = ''
    instagram: str = ''
    store: str = ''
    year_established: int | None = Field(default=None, ge=1800, le=2100)
    description: str = Field(default='', max_length=600)

    @field_validator('industry')
    @classmethod
    def known_industry(cls, v):
        if v not in cat.INDUSTRIES:
            raise ValueError('Choose an industry from the list')
        return v

    @field_validator('business_types')
    @classmethod
    def known_types(cls, v):
        if any(x not in cat.BUSINESS_TYPES for x in v):
            raise ValueError('Unknown business type')
        return sorted(set(v))

    @field_validator('website', 'facebook', 'instagram', 'store')
    @classmethod
    def urls(cls, v):
        return clean_url(v)


class Expense(Contract):
    name: str = Field(min_length=1, max_length=60)
    monthly_minor: int = Field(ge=0, le=10**15)


class Operations(Contract):
    mode: Literal['online', 'offline', 'hybrid'] | None = None
    customers: list[str] = Field(default_factory=list, max_length=10)
    locations: list[str] = Field(default_factory=list, max_length=20)
    branches: int | None = Field(default=None, ge=0, le=100000)
    competitors: list[str] = Field(default_factory=list, max_length=20)
    suppliers: list[str] = Field(default_factory=list, max_length=20)
    channels: list[str] = Field(default_factory=list, max_length=len(cat.CHANNELS))
    expenses: list[Expense] = Field(default_factory=list, max_length=30)

    @field_validator('customers', 'locations', 'competitors', 'suppliers')
    @classmethod
    def names(cls, v):
        out = []
        for item in v:
            item = ' '.join(item.split())[:80]
            if item and item.lower() not in {x.lower() for x in out}:
                out.append(item)
        return out

    @field_validator('channels')
    @classmethod
    def known_channels(cls, v):
        if any(x not in cat.CHANNELS for x in v):
            raise ValueError('Unknown sales channel')
        return sorted(set(v))


class Goals(Contract):
    investment_minor: int | None = Money
    working_capital_minor: int | None = Money
    monthly_revenue_minor: int | None = Money
    monthly_expenses_minor: int | None = Money
    monthly_profit_minor: int | None = Field(default=None, ge=-10**15, le=10**15)
    inventory_value_minor: int | None = Money
    growth_budget_minor: int | None = Money
    challenges: str = Field(default='', max_length=600)
    goals: list[str] = Field(default_factory=list, max_length=len(cat.GOALS))

    @field_validator('goals')
    @classmethod
    def known_goals(cls, v):
        if any(x not in cat.GOALS for x in v):
            raise ValueError('Unknown goal')
        return [g for g in cat.GOALS if g in v]


class Consent(Contract):
    personalized_ai: bool = False
    external_research: bool = False
    monitoring: bool = False
    alerts: bool = False
    warn_cushion_pct: float = Field(default=5, ge=0.5, le=50)  # warn when the price is this close to break-even
    move_pct: float = Field(default=3, ge=0.5, le=50)  # warn when the price falls this much between checks


STEP_MODEL = {'identity': Identity, 'operations': Operations, 'goals': Goals, 'consent': Consent}


class Product(Contract):
    name: str = Field(min_length=1, max_length=80)
    category: str = Short
    variant: str = Field(default='', max_length=60)
    brand: str = Field(default='', max_length=60)
    unit: str = Field(default='', max_length=20)
    quantity: Decimal | None = Field(default=None, ge=0, le=10**9, decimal_places=3)
    cost_minor: int | None = Money
    price_minor: int | None = Money
    expense_minor: int | None = Money  # per-unit selling costs: making charge, buyback deduction, transport


class Csv(Contract):
    csv: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)


# ---------- storage ----------
def load_profile(db, business):
    row = db.execute('SELECT data_enc,key_tag,step,completed_at,updated_at FROM biz_profile WHERE business_id=?', (business,)).fetchone()
    if not row:
        return {}, 0, None, None
    key = customer_key(db, business)
    data = json.loads(unseal(key, row[0], business + '/business')) if key and key_tag(key) == row[1] else {}
    return data, row[2], row[3], row[4]


def save_profile(db, business, data, step, completed_at=None):
    key = customer_key(db, business, create=True)
    box = seal(key, sec.canonical(data).encode(), business + '/business')
    industry = data.get('identity', {}).get('industry', '')
    db.execute("""INSERT INTO biz_profile(business_id,data_enc,key_tag,industry,step,completed_at,updated_at) VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(business_id) DO UPDATE SET data_enc=excluded.data_enc,key_tag=excluded.key_tag,industry=excluded.industry,
                  step=MAX(biz_profile.step,excluded.step),completed_at=COALESCE(biz_profile.completed_at,excluded.completed_at),
                  updated_at=excluded.updated_at""", (business, box, key_tag(key), industry, step, completed_at, sec.now()))


def products(db, business):
    key = customer_key(db, business)
    rows = db.execute('SELECT id,data_enc,key_tag,market_key FROM biz_products WHERE business_id=? ORDER BY created_at,rowid', (business,)).fetchall()
    out = []
    for identity, box, tag, market in rows:
        if key and key_tag(key) == tag:
            item = dict(json.loads(unseal(key, box, identity + '/product')), id=identity, market_key=market)
            if item.get('quantity'):
                item['quantity'] = format(Decimal(item['quantity']).normalize(), 'f')  # rows saved before the fix read '4E+1'
            out.append(item)
    return out


def normal_product(body):
    data = body.model_dump()
    data['unit'] = cat.unit_of(data['unit'])
    data['quantity'] = None if data['quantity'] is None else format(data['quantity'].normalize(), 'f')  # 40, not 4E+1
    for field in ('name', 'category', 'variant', 'brand'):
        data[field] = ' '.join(data[field].split())
    dedupe = sec.signature('biz-product', '|'.join((data['name'].lower(), data['variant'].lower(), data['unit'])))
    return data, dedupe, cat.market_key(data['name'] + ' ' + data['variant'], data['category'], data['unit'])


def store_product(db, business, body, identity=None):
    data, dedupe, market = normal_product(body)
    clash = db.execute('SELECT id FROM biz_products WHERE business_id=? AND dedupe=?', (business, dedupe)).fetchone()
    if clash and clash[0] != identity:
        sec.fail(409, 'PRODUCT_EXISTS')
    key = customer_key(db, business, create=True)
    now = sec.now()
    if identity is None:
        if db.execute('SELECT COUNT(*) FROM biz_products WHERE business_id=?', (business,)).fetchone()[0] >= MAX_PRODUCTS:
            sec.fail(409, 'PRODUCT_LIMIT_REACHED')
        identity = uuid.uuid4().hex
        db.execute('INSERT INTO biz_products VALUES (?,?,?,?,?,?,?,?)',
                   (identity, business, seal(key, sec.canonical(data).encode(), identity + '/product'), key_tag(key), market, dedupe, now, now))
    else:
        updated = db.execute('UPDATE biz_products SET data_enc=?,key_tag=?,market_key=?,dedupe=?,updated_at=? WHERE id=? AND business_id=?',
                             (seal(key, sec.canonical(data).encode(), identity + '/product'), key_tag(key), market, dedupe, now, identity, business))
        if not updated.rowcount:
            sec.fail(404, 'PRODUCT_NOT_FOUND')
    return identity


def gaps(data, items):
    """What is missing and which results it holds back. Never blocks setup."""
    out = []
    identity, ops, goals = data.get('identity') or {}, data.get('operations') or {}, data.get('goals') or {}
    if not identity:
        out.append(('identity', 'Business name, industry and location are needed before anything else.'))
    if not items:
        out.append(('products', 'Add the products or services you sell so market prices can be matched to them.'))
    else:
        no_cost = sum(1 for p in items if p.get('cost_minor') is None)
        no_price = sum(1 for p in items if p.get('price_minor') is None)
        if no_cost:
            out.append(('cost', f'{no_cost} product(s) have no purchase cost, so profit and break-even cannot be calculated for them.'))
        if no_price:
            out.append(('price', f'{no_price} product(s) have no selling price, so margins cannot be calculated for them.'))
    if not ops.get('competitors'):
        out.append(('competitors', 'Name a few competitors you know so they can be compared first.'))
    if goals.get('monthly_revenue_minor') is None or goals.get('monthly_expenses_minor') is None:
        out.append(('finance', 'Monthly revenue and expenses are needed for the profit and growth estimates.'))
    if not goals.get('goals'):
        out.append(('goals', 'Choose your goals so recommendations are ranked for you.'))
    return [dict(field=f, why=w) for f, w in out]


def view(db, business):
    from api.app.market import refresh_watch
    refresh_watch(db, business)  # products, industry or location may have changed what the market panel follows
    data, step, completed, updated = load_profile(db, business)
    items = products(db, business)
    return dict(profile=data, step=step, completed=completed is not None, completed_at=completed, updated_at=updated,
                products=items, gaps=gaps(data, items), currency=sec.original_dataset(db, business).currency,
                niche_category=cat.classify((data.get('identity') or {}).get('niche', '')) if data.get('identity') else None,
                simulation_only=True)


# ---------- routes ----------
@router.get('/catalog')
def catalog():
    return dict(cat.catalog(), simulation_only=True)


@router.get('')
def get_profile(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        return view(db, owner.business_id)


@router.post('/step/{name}')
def save_step(name: str, body: dict, actor=Depends(principal), db=Depends(db_connection)):
    if name not in STEPS:
        sec.fail(404, 'UNKNOWN_STEP')
    model = STEP_MODEL.get(name)
    try:
        parsed = model.model_validate(body).model_dump(mode='json') if model else {}
    except ValidationError as error:
        raise RequestValidationError(error.errors(include_url=False, include_context=False)) from None
    with owner_transaction(db, actor) as owner:
        data, _, _, _ = load_profile(db, owner.business_id)
        if name != 'identity' and not data.get('identity'):
            sec.fail(409, 'IDENTITY_REQUIRED_FIRST')
        if model:
            data[name] = parsed
        save_profile(db, owner.business_id, data, STEPS.index(name) + 1)
        sec.audit(db, owner, owner.business_id, 'BUSINESS_PROFILE_SAVED', {'step': name})
        return view(db, owner.business_id)


@router.post('/complete')
def complete(background: BackgroundTasks, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.market import queue_research, run_research
    research = None
    with owner_transaction(db, actor) as owner:
        data, step, _, updated = load_profile(db, owner.business_id)
        if not data.get('identity'):
            sec.fail(409, 'IDENTITY_REQUIRED_FIRST')
        save_profile(db, owner.business_id, data, len(STEPS), sec.now())
        sec.audit(db, owner, owner.business_id, 'BUSINESS_PROFILE_COMPLETED', {'products': len(products(db, owner.business_id))})
        if (data.get('consent') or {}).get('external_research'):
            research = queue_research(db, owner.business_id)  # the AI starts studying the market right away
        result = view(db, owner.business_id)
    if research:
        background.add_task(run_research, research, owner.business_id)
    return dict(result, research_started=bool(research))


@router.post('/products', status_code=201)
def add_product(body: Product, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        store_product(db, owner.business_id, body)
        return view(db, owner.business_id)


@router.post('/products/{identity}')
def edit_product(identity: str, body: Product, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        store_product(db, owner.business_id, body, identity)
        return view(db, owner.business_id)


@router.post('/products/{identity}/delete')
def delete_product(identity: str, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        if not db.execute('DELETE FROM biz_products WHERE id=? AND business_id=?', (identity, owner.business_id)).rowcount:
            sec.fail(404, 'PRODUCT_NOT_FOUND')
        return view(db, owner.business_id)


HEADERS = {'name': ('name', 'product', 'item', 'product name', 'পণ্য', 'নাম'), 'category': ('category', 'type', 'ক্যাটাগরি'),
           'variant': ('variant', 'size', 'model'), 'brand': ('brand', 'ব্র্যান্ড'), 'unit': ('unit', 'একক'),
           'quantity': ('quantity', 'qty', 'stock', 'পরিমাণ'), 'cost': ('cost', 'purchase price', 'buy price', 'cost price', 'ক্রয়মূল্য'),
           'price': ('price', 'selling price', 'sale price', 'mrp', 'বিক্রয়মূল্য'),
           'expense': ('expense', 'expenses', 'selling cost', 'other cost', 'charges', 'খরচ')}


def to_minor(text):
    text = (text or '').replace(',', '').strip()
    if not text:
        return None
    value = Decimal(re.sub(r'^[^\d.-]+', '', text))
    if value < 0:
        raise InvalidOperation
    return int((value * 100).to_integral_value())


@router.post('/import')
def import_products(body: Csv, actor=Depends(principal), db=Depends(db_connection)):
    rows = list(csv.reader(io.StringIO(body.csv.lstrip('﻿'))))
    if not rows:
        sec.fail(422, 'EMPTY_FILE')
    head = [h.strip().lower() for h in rows[0]]
    column = {field: next((i for i, h in enumerate(head) if h in names), None) for field, names in HEADERS.items()}
    if column['name'] is None:
        sec.fail(422, 'NAME_COLUMN_REQUIRED')
    added, skipped = 0, []
    with owner_transaction(db, actor) as owner:
        for number, row in enumerate(rows[1:], start=2):
            cell = lambda f: row[column[f]].strip() if column[f] is not None and column[f] < len(row) else ''
            if not any(c.strip() for c in row):
                continue
            try:
                product = Product(name=cell('name'), category=cell('category')[:80], variant=cell('variant')[:60], brand=cell('brand')[:60],
                                  unit=cell('unit')[:20], quantity=Decimal(cell('quantity').replace(',', '')) if cell('quantity') else None,
                                  cost_minor=to_minor(cell('cost')), price_minor=to_minor(cell('price')),
                                  expense_minor=to_minor(cell('expense')))
                store_product(db, owner.business_id, product)
                added += 1
            except sec.PolicyError as error:  # duplicate or limit; checked before any write, so nothing partial is left
                skipped.append(dict(row=number, reason=error.code))
            except Exception:  # noqa: BLE001 - any bad cell skips only that row
                skipped.append(dict(row=number, reason='INVALID_ROW'))
        return dict(view(db, owner.business_id), added=added, skipped=skipped[:50])


BI_TABLES = ('biz_profile', 'biz_products', 'biz_notes', 'market_watch', 'research_runs', 'biz_alerts', 'biz_alert_state', 'advisor_feedback')


# ---------- business data the owner submits ----------
NOTE_KINDS = ('sales', 'prices', 'competitors', 'customers', 'suppliers', 'plans', 'other')
MAX_NOTES = 200
MAX_NOTE_CHARS = 20000


class Note(Contract):
    kind: Literal['sales', 'prices', 'competitors', 'customers', 'suppliers', 'plans', 'other']
    title: str = Field(min_length=2, max_length=120)
    text: str = Field(min_length=2, max_length=MAX_NOTE_CHARS)
    file_name: str = Field(default='', max_length=120)
    research: bool = True  # the owner allows this note to guide web research

    @field_validator('title', 'text', 'file_name')
    @classmethod
    def printable(cls, value):
        return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)


def notes(db, business, research_only=False, limit=MAX_NOTES):
    """The owner's submitted business data, newest first. Call inside a transaction."""
    key = customer_key(db, business)
    if not key:
        return []
    where = ' AND research=1' if research_only else ''
    out = []
    for identity, box, tag, at in db.execute(f'SELECT id,data_enc,key_tag,created_at FROM biz_notes WHERE business_id=?{where} '  # noqa: S608
                                            'ORDER BY created_at DESC, rowid DESC LIMIT ?', (business, limit)).fetchall():
        if key_tag(key) == tag:
            out.append(dict(json.loads(unseal(key, box, identity + '/note')), id=identity, created_at=at))
    return out


def notes_view(db, business):
    data = load_profile(db, business)[0]
    return dict(notes=notes(db, business), kinds=list(NOTE_KINDS),
                research_allowed=bool((data.get('consent') or {}).get('external_research')), profile_ready=bool(data.get('identity')),
                simulation_only=True)


@router.get('/notes')
def list_notes(actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        return notes_view(db, owner.business_id)


@router.post('/notes', status_code=201)
def add_note(body: Note, background: BackgroundTasks, actor=Depends(principal), db=Depends(db_connection)):
    from api.app.market import queue_research, run_research
    research = None
    with owner_transaction(db, actor) as owner:
        business = owner.business_id
        if db.execute('SELECT COUNT(*) FROM biz_notes WHERE business_id=?', (business,)).fetchone()[0] >= MAX_NOTES:
            sec.fail(409, 'NOTE_LIMIT')
        identity = uuid.uuid4().hex
        key = customer_key(db, business, create=True)
        box = seal(key, sec.canonical(body.model_dump(mode='json')).encode(), identity + '/note')
        db.execute('INSERT INTO biz_notes(id,business_id,data_enc,key_tag,kind,research,created_at) VALUES (?,?,?,?,?,?,?)',
                   (identity, business, box, key_tag(key), body.kind, int(body.research), sec.now()))
        sec.audit(db, owner, identity, 'BUSINESS_DATA_ADDED', {'kind': body.kind, 'chars': len(body.text)})
        profile = load_profile(db, business)[0]
        if body.research and profile.get('identity') and (profile.get('consent') or {}).get('external_research'):
            research = queue_research(db, business)  # new facts from the owner: study the market again
        result = notes_view(db, business)
    if research:
        background.add_task(run_research, research, business)
    return dict(result, research_started=bool(research))


@router.post('/notes/{identity}/delete')
def delete_note(identity: str, actor=Depends(principal), db=Depends(db_connection)):
    with owner_transaction(db, actor) as owner:
        if not db.execute('DELETE FROM biz_notes WHERE id=? AND business_id=?', (identity, owner.business_id)).rowcount:
            sec.fail(404, 'NOTE_NOT_FOUND')
        sec.audit(db, owner, identity, 'BUSINESS_DATA_DELETED', {})
        return notes_view(db, owner.business_id)


def erase(db, business):
    """Remove the business profile and everything derived from it (called when the owner erases a workspace's private data)."""
    for table in BI_TABLES:
        db.execute(f'DELETE FROM {table} WHERE business_id=?', (business,))  # noqa: S608 - fixed table names
    db.execute('DELETE FROM market_observations WHERE business_id=?', (business,))  # the owner's own quotes; public prices stay


def export(db, business):
    """The owner's business-intelligence data in readable form, for the workspace export."""
    from api.app.market import research_view
    data, step, completed, _ = load_profile(db, business)
    quotes = db.execute('SELECT indicator,value_minor,currency,unit,source,url,observed_at,kind FROM market_observations '
                        'WHERE business_id=? ORDER BY fetched_at', (business,)).fetchall()
    key = customer_key(db, business)
    alerts = []
    for identity, level, box, tag, at in db.execute('SELECT id,level,data_enc,key_tag,created_at FROM biz_alerts WHERE business_id=? ORDER BY created_at',
                                                    (business,)).fetchall():
        if key and key_tag(key) == tag:
            alerts.append(dict(json.loads(unseal(key, box, identity + '/alert')), level=level, created_at=at))
    feedback = db.execute('SELECT insight,tier,response,baseline_json,created_at FROM advisor_feedback WHERE business_id=? ORDER BY created_at',
                          (business,)).fetchall()
    return dict(profile=data, setup_step=step, completed_at=completed, products=products(db, business), business_data=notes(db, business),
                market_quotes=[dict(zip(('indicator', 'value_minor', 'currency', 'unit', 'source', 'url', 'observed_at', 'kind'), q)) for q in quotes],
                research=research_view(db, business), alerts=alerts,
                advisor_feedback=[dict(insight=f[0], tier=f[1], response=f[2], baseline=json.loads(f[3]), created_at=f[4]) for f in feedback])
