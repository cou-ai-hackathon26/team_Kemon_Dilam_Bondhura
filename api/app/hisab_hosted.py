"""Hugging Face private Space adapter for owner-reviewed BDT cash drafts."""
import json
import os
import re
import threading
import time
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends
from pydantic import Field, StrictBool

from api.app import gateway, security as sec
from api.app.agents.errors import ProviderError
from api.app.accounts import today_bd
from api.app.assist import BANGLA_DIGITS, normalize_draft, parse_amount, predict_draft, smart_enabled
from api.app.contracts import Contract
from api.app.currencies import CURRENCIES
from api.app.routes import db_connection, principal

router = APIRouter(prefix="/api")
SPACE = "https://pranto-404-capzoryx-hisab-inference.hf.space/gradio_api/call/extract"
SLOT = threading.BoundedSemaphore(1)
ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
MULTIPLIERS = dict.fromkeys(('k', 'thousand', 'hajar', 'hazar', 'হাজার'), 1000) | dict.fromkeys(('lakh', 'lakhs', 'lac', 'লাখ', 'লক্ষ'), 100000) | dict.fromkeys(('crore', 'koti', 'কোটি'), 10000000) | {'million': 1000000}
AMOUNT = re.compile(r"(?<![\w,.])(?P<value>(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?)(?:\s*(?P<unit>" + '|'.join(MULTIPLIERS) + r"))?(?![\w,]|\.\d)", re.I)
CURRENCY_CODE = re.compile(r'\b(?:' + '|'.join(sorted(CURRENCIES.keys() - {'BDT'})) + r')\b', re.I)
OTHER_CURRENCY = re.compile(r"\b(?:dollars?|rupees?|euros?|pounds?|yen|yuan|riyals?|dirhams?|ringgit|pesos?|francs?|won|rubles?)\b", re.I)
INFLOW = re.compile(r"\b(?:received|pailam|pelam|peyechi|paid me|got paid)\b|পেলাম|পেয়েছি|পেয়েছি", re.I)
TAKEN = re.compile(r"\b(?:niyechi|niyechhi|niyesi|nilam)\b|নিয়েছি|নিয়েছি|নিলাম", re.I)
FROM_SOURCE = re.compile(r"\b(?:from|theke)\b|থেকে", re.I)
OUTFLOW = re.compile(r"\b(?:(?<!\bgot )paid(?! me\b)|spent|dilam|diyechi|disi)\b|দিলাম|দিয়েছি|দিয়েছি|খরচ করলাম", re.I)
YESTERDAY = re.compile(r"\b(?:yesterday|gotokal|kal)\b|গতকাল|(?<!\S)কাল(?!\w)", re.I)
AMBIGUOUS = re.compile(r"[?？]|\b(?:not|never|no|didn['’]t|unpaid|pending|due|will|would|could|can|should|if|and|or|approximately|about|around|tomorrow|next|last|ago|monday|tuesday|wednesday|thursday|friday|saturday|sunday|na|nai)\b|আগামী|বাকি|দিইনি|দেয়নি|দেয়নি|নিইনি|(?<!\S)না(?!\w)", re.I)
PARTY_WORD = r"[^\W\d_][^\s\d,;:!?]*"
NAMED_PARTY = PARTY_WORD + r"(?:\s+" + PARTY_WORD + r"){0,3}"
PARTY_AFTER_PREPOSITION = re.compile(r"\b(?:to|from|by)\s+(" + NAMED_PARTY + r")", re.I)
PARTY_AFTER_VERB = re.compile(r"\bpaid\s+(" + NAMED_PARTY + r")(?=\s|$)", re.I)
LOCAL_PARTY = re.compile(r"(?:^|\s)(" + PARTY_WORD + r")(?:\s+(?:ke|theke|থেকে|কে)|কে)(?!\w)", re.I)
PARTY_STOP = re.compile(r"\s+(?:for|on|at|today|yesterday|gotokal|kal|me|bdt|tk|taka|টাকা)\b", re.I)


def configured():
    return bool(os.getenv("CAPZORYX_HISAB_HF_TOKEN", "").strip())


def facts(sentence, today):
    """Verify one completed cash amount. A bare number uses the BDT workspace currency."""
    text = ' '.join(sentence.translate(BANGLA_DIGITS).split())
    received = INFLOW.search(text) or (TAKEN.search(text) and FROM_SOURCE.search(text))
    directions = (bool(received), bool(OUTFLOW.search(text)))
    foreign_symbol = any(char != '৳' and unicodedata.category(char) == 'Sc' for char in text)
    if foreign_symbol or CURRENCY_CODE.search(text) or OTHER_CURRENCY.search(text) or sum(directions) != 1 or AMBIGUOUS.search(text):
        return None
    dates = ISO_DATE.findall(text)
    numeric = ISO_DATE.sub("", text)
    amounts = list(AMOUNT.finditer(numeric))
    if len(dates) > 1 or len(amounts) != 1 or re.search(r"[-−–+]\s*(?:৳\s*)?\d|\(\s*(?:৳\s*)?\d", numeric):
        return None
    match = amounts[0]
    if re.search(r"\d", numeric[:match.start()] + numeric[match.end():]):
        return None
    value = parse_amount(match['value'])
    if value is None:
        return None
    amount = value * MULTIPLIERS.get((match['unit'] or '').lower(), 1)
    if not 0 < amount <= Decimal("10000000000") or amount != amount.quantize(Decimal("0.01")):
        return None
    try:
        day = date.fromisoformat(dates[0]) if dates else today - timedelta(days=int(bool(YESTERDAY.search(text))))
    except ValueError:
        return None
    if day > today:
        return None
    return amount, "inflow" if directions[0] else "outflow", day


def verified_party(sentence, prediction):
    match = LOCAL_PARTY.search(sentence)
    if match:
        return match[1]
    match = PARTY_AFTER_PREPOSITION.search(sentence) or PARTY_AFTER_VERB.search(sentence)
    if match:
        party = PARTY_STOP.split(match[1], maxsplit=1)[0].strip(" .')")
        if party and party.casefold() not in {'me', 'tk', 'bdt', 'taka', 'টাকা'}:
            return party
    vendor = prediction.get('vendor')
    vendor = vendor.strip() if isinstance(vendor, str) else ''
    return vendor if vendor and re.search(r'(?<!\w)' + re.escape(vendor) + r'(?!\w)', sentence, re.I) else ''


def verifies_economics(prediction, amount, direction):
    return (prediction.get('kind') == 'cash' and prediction.get('currency') == 'BDT'
            and parse_amount(prediction.get('amount')) == amount and prediction.get('direction') == direction)


def hosted_extract(sentence):
    """Keep the Space credential server-side and accept only the private endpoint's result."""
    from api.app.agents.provider import strict_json

    key = os.environ.get("CAPZORYX_HISAB_HF_TOKEN", "").strip()
    if not key:
        raise ProviderError("HISAB_NOT_AVAILABLE")
    if not SLOT.acquire(blocking=False):
        raise ProviderError("HISAB_BUSY")
    deadline = time.monotonic() + 24
    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {key}"},
            follow_redirects=False,
            timeout=httpx.Timeout(21, connect=4),
            trust_env=False,
        ) as client:
            reply = client.post(SPACE, json={"data": [sentence]}, timeout=6)
            if reply.status_code != 200:
                raise ProviderError("HISAB_NOT_AVAILABLE")
            ticket = reply.json().get("event_id")
            if not isinstance(ticket, str) or not re.fullmatch(r"[a-f0-9]{32}", ticket):
                raise ProviderError("HISAB_NOT_AVAILABLE")
            with client.stream("GET", f"{SPACE}/{ticket}", headers={"Accept": "text/event-stream"}) as events:
                if events.status_code != 200:
                    raise ProviderError("HISAB_NOT_AVAILABLE")
                event = ""
                for line in events.iter_lines():
                    if time.monotonic() >= deadline or len(line) > 8192:
                        break
                    if line.startswith("event: "):
                        event = line[7:]
                        if event == "error":
                            break
                    elif event == "complete" and line.startswith("data: "):
                        values = strict_json(line[6:])
                        if isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict):
                            return values[0]
                        break
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    finally:
        SLOT.release()
    raise ProviderError("HISAB_NOT_AVAILABLE")


class ExtractBody(Contract):
    text: str = Field(min_length=3, max_length=400)
    consent: StrictBool
    allow_api_fallback: StrictBool = False


@router.post("/assist/hisab")
def extract(body: ExtractBody, actor=Depends(principal), db=Depends(db_connection)):
    if actor.role != "owner":
        sec.fail(403, "OWNER_REQUIRED")
    if not body.consent:
        sec.fail(403, "EXTERNAL_AI_CONSENT_REQUIRED")
    if sec.runtime_dataset(db, actor.business_id).currency != "BDT":
        sec.fail(422, "HISAB_BDT_ONLY")
    today = today_bd()
    source = facts(body.text, today)
    if source is None:
        sec.fail(422, "HISAB_ONE_CASH_RECORD")
    amount, direction, when = source
    prediction, source_engine = {}, 'verified'
    if configured():
        try:
            sec.budgets(db, [("hisab:" + actor.business_id, 4), ("hisab-hosted", 10)])
            candidate = hosted_extract(body.text)
            if verifies_economics(candidate, amount, direction):
                prediction, source_engine = candidate, 'hisab_lm'
        except ProviderError:
            pass
        except sec.PolicyError as error:
            if error.status != 429:
                raise
    if source_engine == 'verified' and body.allow_api_fallback and smart_enabled(db, actor) and gateway.record_backup_configured():
        try:
            sec.budgets(db, [('extract:' + actor.user_id, 12), ('assist-all', 40)])
            candidate = predict_draft(body.text, db, actor.business_id, today, include_local=False)
            if verifies_economics(candidate, amount, direction):
                prediction, source_engine = candidate, 'api_backup'
        except ProviderError:
            pass
        except sec.PolicyError as error:
            if error.status != 429:
                raise
    vendor = verified_party(body.text, prediction)
    draft = normalize_draft(
        dict(kind="cash", amount=format(amount, "f"), direction=direction, currency="BDT",
             date=when.isoformat(), vendor=vendor, description=body.text, status="cleared"),
        "BDT",
        today,
    )
    return dict(draft=draft, source=source_engine, model={
        'hisab_lm': 'Hisab-LM', 'api_backup': 'Online backup', 'verified': 'Verified extraction',
    }[source_engine], simulation_only=True)
