"""Deterministic parsing and receivable ranking for owner-shared BDT wallet messages."""
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
import re
import unicodedata


PROVIDERS = {
    'bkash': r'\bbkash\b|বিকাশ',
    'nagad': r'\bnagad\b|নগদ',
    'rocket': r'\brocket\b|রকেট',
}
INCOMING = r'\breceived\b|\bcredited\b|\bcash[ -]?in\b|পেয়েছেন|পেয়েছেন|গ্রহণ করেছেন|জমা হয়েছে|জমা হয়েছে'
OUTGOING = r'\bsent\b|\bdebited\b|\bcash[ -]?out\b|\bpaid\b|পাঠিয়েছেন|পাঠিয়েছেন|প্রদান করেছেন'
AMOUNT = r'(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.([0-9]{1,2}))?(?![0-9,]|\.[0-9])'
UNIT = r'(?:\bbdt\b|\btk\.?|৳|টাকা)'
MONEY = re.compile(rf'{UNIT}\s*{AMOUNT}|{AMOUNT}\s*{UNIT}', re.I)
ANCILLARY = re.compile(r'(?:balance|bal|fee|charge|cashback|ব্যালেন্স|মাশুল|চার্জ)\s*[:=]?\s*$', re.I)
TRANSACTION = re.compile(r'(?:\b(?:trx|txn|trans(?:action)?)[ ._-]*(?:id|no)?\b|ট্রানজেকশন আইডি)\s*[:=#-]?\s*([a-z0-9-]{1,45})', re.I)
PARTY = re.compile(r'(?:\bfrom\s+|প্রেরক\s*[:=]\s*)(.{1,100}?)(?=[,;\n]|\.(?:\s|$)|\s+(?:ref(?:erence)?\b|trx|txn|trans(?:action)?\b|bal(?:ance)?\b|on\b|at\b|fee\b)|$)', re.I)
BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')


@dataclass(frozen=True)
class Payment:
    provider: str
    txn_id: str | None
    amount_minor: int
    direction: str
    party: str
    occurred_on: date
    flags: tuple[str, ...]


def normalized(text):
    return unicodedata.normalize('NFC', text).translate(BN_DIGITS).strip()


def minor(match):
    whole, cents, other_whole, other_cents = match.groups()
    return int((whole or other_whole).replace(',', '')) * 100 + int((cents or other_cents or '').ljust(2, '0'))


def parse_payment(text, today):
    text = normalized(text)
    if re.search(r'\b(?:otp|pin|password|verification code|passcode)\b|ওটিপি|পিন|পাসওয়ার্ড', text, re.I):
        raise ValueError('WALLET_SENSITIVE_MESSAGE')
    providers = [p for p, pattern in PROVIDERS.items() if re.search(pattern, text, re.I)]
    provider = providers[0] if len(providers) == 1 else 'unknown'
    flags = []
    if provider == 'unknown':
        flags.append('UNKNOWN_PROVIDER')
    incoming = bool(re.search(INCOMING,text,re.I))
    outgoing = bool(re.search(OUTGOING,text,re.I))
    if not (incoming ^ outgoing):
        raise ValueError('WALLET_DIRECTION_UNCLEAR')
    direction = 'in' if incoming else 'out'
    uncertain_status=(r"\b(?:failed|request(?:ed)?|pending|reversed|cancel(?:l)?ed|declined|unsuccessful|attempt(?:ed)?|"
                      r"unconfirmed|never|not|unable|couldn['’]t|haven['’]t|hasn['’]t|didn['’]t)\b|"
                      r"ব্যর্থ|বাতিল|অনুরোধ|চেষ্টা|পাননি|পান নাই|পাওয়া যায়নি|পাওয়া যায়নি|হয়নি|হয়নি")
    if re.search(uncertain_status, text, re.I):
        flags.append('UNVERIFIED_STATUS')
    amounts = []
    balances = {}
    for match in MONEY.finditer(text):
        before = text[:match.start()]
        label = ANCILLARY.search(before)
        if label:
            tail = before.lower()
            if re.search(r'(?:previous|old|opening)\s+bal(?:ance)?\s*[:=]?\s*$',tail):
                balances['old'] = minor(match)
            elif re.search(r'(?:new|closing)\s+bal(?:ance)?\s*[:=]?\s*$',tail):
                balances['new'] = minor(match)
            elif re.search(r'(?:charge|fee)\s*[:=]?\s*$',tail):
                balances['fee'] = minor(match)
            continue
        amounts.append(minor(match))
    if len(amounts) != 1 or not 0 < amounts[0] <= 10**12:
        raise ValueError('WALLET_AMOUNT_UNCLEAR')
    amount = amounts[0]
    if 'old' in balances and 'new' in balances:
        if balances['old'] + (amount if incoming else -amount) - balances.get('fee',0) != balances['new']:
            flags.append('BALANCE_MISMATCH')
    tx = TRANSACTION.search(text)
    txn = tx[1].upper() if tx else None
    patterns = {'bkash': r'[A-Z0-9]{10}', 'nagad': r'[A-Z0-9]{8,12}', 'rocket': r'[0-9]{8,16}'}
    if not txn or not re.fullmatch(patterns.get(provider,r'[A-Z0-9]{6,32}'), txn):
        flags.append('CHECK_TRANSACTION_ID')
    day = today
    stamp = re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2}|\d{2}[-/]\d{2}[-/](?:\d{4}|\d{2}))(?!\d)',text)
    if stamp:
        try:
            raw = stamp[1]
            if raw[4]=='-':
                day=date.fromisoformat(raw)
            else:
                day_text,month_text,year_text=re.split('[-/]',raw)
                day=date(int(year_text) if len(year_text)==4 else 2000+int(year_text),int(month_text),int(day_text))
        except ValueError:
            raise ValueError('WALLET_DATE_INVALID') from None
    else:
        flags.append('DATE_NOT_IN_MESSAGE')
    if day > today:
        flags.append('FUTURE_DATE')
    party = PARTY.search(text)
    return Payment(provider,txn,amount,direction,party[1].strip() if party else '',day,tuple(flags))


def rank_matches(book, party, amount, occurred_on):
    names = {c['id']: c for c in book['customers']}
    party = normalized(party).casefold()
    party_digits = re.sub(r'\D','',party)
    results = []
    for debt in book['receivables']:
        if debt['open_minor'] < amount or debt['issued_on'] > occurred_on.isoformat():
            continue
        customer = names[debt['customer_id']]
        name = normalized(customer['name'] or '').casefold()
        phone = re.sub(r'\D','',normalized(customer['phone'] or ''))
        identity = SequenceMatcher(None,name,party).ratio() if name and party else 0
        if len(phone)>=10 and len(party_digits)>=10 and phone[-10:]==party_digits[-10:]:
            identity=1
        days=abs((date.fromisoformat(debt['due_on'])-occurred_on).days)
        # An exact customer name or registered number outranks a similar name with a nearer due date.
        identity_points = 65 if identity == 1 else round(45*identity)
        score = identity_points + (35 if debt['open_minor']==amount else 0) + max(0,10-days)
        results.append(dict(receivable_id=debt['id'],customer=customer['name'],number=debt['number'],
                            open_minor=debt['open_minor'],due_on=debt['due_on'],score=score))
    return sorted(results,key=lambda r:(-r['score'],r['due_on'],r['receivable_id']))[:5]
