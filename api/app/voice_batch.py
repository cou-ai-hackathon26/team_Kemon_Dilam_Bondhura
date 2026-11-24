"""A week of spending in one voice note: speech to text, then one checked draft per entry.

Flow: audio (opus, up to 3 minutes) -> Whisper large-v3 on Groq (free tier), then Gemini as a fallback -> transcript the
owner can edit -> entries. An AI model splits the transcript, but nothing it says is trusted on its own:
  * every entry must quote words that are really in the transcript,
  * its amount must be a number actually said in those words (digits, Bangla digits, or number words in Bangla,
    Banglish or English, e.g. "dui hajar pachsho", "দেড় হাজার", "2.5k"),
  * its date is worked out here from what was said ("gotokal", "shombar", "3 din age"); a sentence without a date
    belongs to the day mentioned before it.
When no AI service answers, a rule-based splitter makes the drafts. Nothing is saved until the owner confirms;
saving many drafts is one atomic step (see inputs.add_records).
"""
import base64
import binascii
import json
import logging
import math
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends
from pydantic import Field, StrictBool

from api.app import security as sec
from api.app.accounts import today_bd
from api.app.contracts import Contract
from api.app.currencies import CURRENCIES
from api.app.routes import db_connection, principal

log = logging.getLogger(__name__)
router = APIRouter(prefix='/api/assist')
TRANSPORT = None  # tests inject an httpx.MockTransport
MAX_AUDIO_BYTES = 2_400_000  # about 10 minutes of 32 kbps opus; the recorder stops at 3
MAX_TRANSCRIPT = 4000
MAX_ENTRIES = 40
LOOKBACK_DAYS = 14

# ---------- numbers said aloud ----------
BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
_SMALL = {}


def _add(words, start, step=1):
    for i, word in enumerate(words.split()):
        _SMALL[unicodedata.normalize('NFC', word)] = start + i * step


_add('এক দুই তিন চার পাঁচ ছয় সাত আট নয় দশ এগারো বারো তেরো চৌদ্দ পনেরো ষোলো সতেরো আঠারো উনিশ', 1)
_add('বিশ একুশ বাইশ তেইশ চব্বিশ পঁচিশ ছাব্বিশ সাতাশ আটাশ ঊনত্রিশ', 20)
_add('ত্রিশ একত্রিশ বত্রিশ তেত্রিশ চৌত্রিশ পঁয়ত্রিশ ছত্রিশ সাঁইত্রিশ আটত্রিশ ঊনচল্লিশ', 30)
_add('চল্লিশ একচল্লিশ বিয়াল্লিশ তেতাল্লিশ চুয়াল্লিশ পঁয়তাল্লিশ ছেচল্লিশ সাতচল্লিশ আটচল্লিশ ঊনপঞ্চাশ', 40)
_add('পঞ্চাশ একান্ন বাহান্ন তিপ্পান্ন চুয়ান্ন পঞ্চান্ন ছাপ্পান্ন সাতান্ন আটান্ন ঊনষাট', 50)
_add('ষাট একষট্টি বাষট্টি তেষট্টি চৌষট্টি পঁয়ষট্টি ছেষট্টি সাতষট্টি আটষট্টি ঊনসত্তর', 60)
_add('সত্তর একাত্তর বাহাত্তর তিয়াত্তর চুয়াত্তর পঁচাত্তর ছিয়াত্তর সাতাত্তর আটাত্তর ঊনআশি', 70)
_add('আশি একাশি বিরাশি তিরাশি চুরাশি পঁচাশি ছিয়াশি সাতাশি আটাশি ঊননব্বই', 80)
_add('নব্বই একানব্বই বিরানব্বই তিরানব্বই চুরানব্বই পঁচানব্বই ছিয়ানব্বই সাতানব্বই আটানব্বই নিরানব্বই', 90)
_add('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen '
     'eighteen nineteen', 1)
_add('twenty thirty forty fifty sixty seventy eighty ninety', 20, 10)
# Banglish (romanised Bangla) as people type and as speech engines spell it.
for words, value in [('ek ak', 1), ('dui du duii', 2), ('tin teen', 3), ('char', 4), ('panch pach paach', 5),
                     ('choy chhoy soy', 6), ('saat sat', 7), ('aat aath', 8), ('noy nou', 9), ('dosh dos', 10),
                     ('egaro', 11), ('baro', 12), ('tero', 13), ('choddo', 14), ('ponero', 15), ('sholo', 16),
                     ('shotero', 17), ('atharo', 18), ('unish', 19), ('bish bis', 20), ('tirish tiris trish', 30),
                     ('chollish cholish', 40), ('ponchash ponchas', 50), ('shaat shat', 60), ('sottor', 70),
                     ('ashi', 80), ('nobboi', 90)]:
    for word in words.split():
        _SMALL.setdefault(word, value)
_HUNDREDS = {}
for i, forms in enumerate(['এক', 'দুই দু', 'তিন', 'চার', 'পাঁচ', 'ছয় ছ', 'সাত', 'আট', 'নয়']):
    for form in forms.split():
        for ending in ('শ', 'শো', 'শত', 'সো'):
            _HUNDREDS[unicodedata.normalize('NFC', form + ending)] = (i + 1) * 100
for spelling in ('কুড়ি', 'কুডি', 'কুরি'):
    _SMALL[unicodedata.normalize('NFC', spelling)] = 20
for i, forms in enumerate(['ek', 'du dui', 'tin', 'char', 'pach panch', 'choy chhoy', 'sat saat', 'at aat', 'noy']):
    for form in forms.split():
        for ending in ('sho', 'sh', 'show', 'shoto'):
            _HUNDREDS[form + ending] = (i + 1) * 100
HUNDRED = {'hundred', 'শ', 'শো', 'শত', 'sho', 'shoto'}
SCALES = {'thousand': 1000, 'হাজার': 1000, 'hajar': 1000, 'hazar': 1000, 'k': 1000,
          'lakh': 100_000, 'lac': 100_000, 'lakhs': 100_000, 'লাখ': 100_000, 'লক্ষ': 100_000,
          'million': 1_000_000, 'crore': 10_000_000, 'কোটি': 10_000_000, 'koti': 10_000_000}
FRACTIONS = {'দেড়': 1.5, 'der': 1.5, 'deŗ': 1.5, 'আড়াই': 2.5, 'arai': 2.5, 'adai': 2.5}
HALF_MORE = {'সাড়ে', 'sare', 'shade', 'sharhe'}
TOKEN = re.compile(r'\d+(?:[.,]\d+)*(?:\.\d+)?k?|[^\W\d][\w\u0981-\u0983\u09bc\u09be-\u09cd\u09d7\u09e2\u09e3\u200c\u200d]*', re.UNICODE)


def _norm(text):
    return unicodedata.normalize('NFC', text.translate(BN_DIGITS)).casefold()


def _digit(token):
    raw = token[:-1] if token.endswith('k') else token
    try:
        value = float(raw.replace(',', ''))
    except ValueError:
        return None
    return value * 1000 if token.endswith('k') else value


def spoken_amounts(text):
    """Every amount said in `text`, as numbers in major units ("2 hajar 500" -> 2500, "দেড় হাজার" -> 1500)."""
    found = set()
    tokens = TOKEN.findall(_norm(text))
    i = 0
    while i < len(tokens):
        start = i
        total, current, fraction, half, seen = 0.0, 0.0, 0.0, False, False
        while i < len(tokens):
            word = tokens[i]
            number = _digit(word) if word[0].isdigit() else None
            if number is not None:
                current += number
            elif word in _SMALL:
                current += _SMALL[word]
            elif word in _HUNDREDS:
                current += _HUNDREDS[word]
            elif word in HUNDRED and seen:
                current = max(current, 1) * 100
            elif word in FRACTIONS:
                fraction = FRACTIONS[word]
            elif word in HALF_MORE:
                half = True
            elif word in SCALES and (seen or fraction):
                base = fraction or current or 1
                total += (base + (0.5 if half else 0)) * SCALES[word]
                current, fraction, half = 0.0, 0.0, False
            elif word == 'and' and seen:
                pass
            else:
                break
            seen = True
            i += 1
        if seen:
            value = total + current
            if math.isfinite(value) and value > 0:
                found.add(round(value, 2))
        if i == start:
            i += 1
    return found


# ---------- dates said aloud ----------
WEEKDAYS = {
    0: ('monday', 'সোমবার', 'shombar', 'sombar', 'somobar', 'shomobar'),
    1: ('tuesday', 'মঙ্গলবার', 'mongolbar', 'mongalbar'),
    2: ('wednesday', 'বুধবার', 'budhbar', 'budbar', 'budhobar'),
    3: ('thursday', 'বৃহস্পতিবার', 'brihospotibar', 'bishudbar', 'bishudbar', 'brihoshpotibar'),
    4: ('friday', 'শুক্রবার', 'shukrobar', 'sukrobar'),
    5: ('saturday', 'শনিবার', 'shonibar', 'sonibar'),
    6: ('sunday', 'রবিবার', 'robibar', 'robbar', 'রোববার'),
}
TODAY_WORDS = ('today', 'আজ', 'আজকে', 'aj', 'aaj', 'ajke', 'aajke')
YESTERDAY_WORDS = ('yesterday', 'গতকাল', 'gotokal', 'gotokaal', 'kal', 'kalke', 'কাল', 'কালকে')
DAY_BEFORE_WORDS = ('পরশু', 'porshu', 'porsu', 'poshu')
AGO = re.compile(r'(\d+)\s*(?:din|দিন|days?)\s*(?:age|আগে|ago)')


def _weekday(text):
    words = TOKEN.findall(_norm(text))
    lowered = _norm(text)
    for weekday, names in WEEKDAYS.items():
        if any(n in words or (not n.isascii() and n in lowered) for n in names):
            return weekday
    return None


def said_day(text, today, week_start=None):
    """The day a phrase refers to, or None. A weekday falls in the chosen week (week_start .. +6 days); without one,
    in the seven days ending today."""
    words = TOKEN.findall(_norm(text))
    lowered = _norm(text)
    if 'day before yesterday' in lowered or any(w in words for w in DAY_BEFORE_WORDS):
        return today - timedelta(days=2)
    match = AGO.search(lowered)
    if match is None:
        for word, value in _SMALL.items():  # "tin din age", "তিন দিন আগে"
            if value <= 13 and re.search(rf'(?<!\w){re.escape(word)}\s*(?:din|দিন|days?)\s*(?:age|আগে|ago)', lowered):
                return today - timedelta(days=value)
    elif int(match.group(1)) <= LOOKBACK_DAYS:
        return today - timedelta(days=int(match.group(1)))
    weekday = _weekday(text)
    if weekday is not None:
        end = week_start + timedelta(days=6) if week_start else today
        return end - timedelta(days=(end.weekday() - weekday) % 7)
    if any(w in words for w in YESTERDAY_WORDS):
        return today - timedelta(days=1)
    if any(w in words for w in TODAY_WORDS):
        return today
    iso = re.search(r'(20\d\d)-(\d\d)-(\d\d)', lowered)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    return None


# ---------- what kind of statement a part is ----------
INCOME = re.compile(r'\b(?:received|got|earned|sold|sale|sales|collected|income|pelam|pailam|peyechi|peyesi|paisi|'
                    r'bikri|becha|bechlam|joma|borrowed|nilam|niyechilam)\b|পেয়েছি|পেলাম|পাইলাম|বিক্রি|বেচা|বেচলাম|আয়|জমা|'
                    r'নিয়েছিলাম|নিয়েছি|নিলাম|পেয়েছিলাম|পাইসি|পাইছি|পাইছিলাম|পাইসিলাম|পেয়েছি|পাইছে|পাইসে|\bpaichi\b|\bpaisilam\b|\bpaichilam\b', re.I)
BILL = re.compile(r'\b(?:baki|due|owe|bill pending|unpaid)\b|(?<!\w)বাকি(?![\w\u0981-\u09cd])|পাওনা', re.I)
SPENT = re.compile(r'\b(?:paid|pay|spent|bought|buy|gave|dilam|diyechi|disi|dichi|kinlam|kinechi|khoroch|khorch|bhara|vara|'
                   r'rent|transport|bill|salary|beton|mal|bazar|nasta|chaa|cha|lunch|tel|fuel|repair|kharcha|expense|kinsi|'
                   r'delivery|packaging)\b|'
                   r'দিলাম|দিয়েছি|দিয়েছিলাম|দিতে পেরেছি|কিনলাম|কিনেছি|খরচ|ভাড়া|বিল|বেতন|মাল|বাজার|নাস্তা|চা|তেল', re.I)
PURCHASE = re.compile(r'(?:৳\s*\d+(?:[.,]\d+)*|টাকা)\s+দিয়ে(?:ই)?\s+|'
                      r'\b(?:paid for|bought|spent on)\b', re.I)
REQUESTED = re.compile(r'ধার\s+চে(?:য়ে|ই)ছিল|টাকা\s+চে(?:য়ে|ই)ছিল|'
                       r'\b(?:asked (?:me )?(?:for|to lend)|wanted to borrow|dhar cheyechilo)\b', re.I)
BALANCE = re.compile(r'আমার কাছে.{0,80}টাকা (?:আছে|ছিল)|হাতে.{0,60}টাকা (?:আছে|ছিল)|'
                     r'\b(?:cash on hand|balance|had left|taka ase|taka ache)\b', re.I)
# "Rahim 5000 taka dey nai", "দেইনি", "pai nai", "didn't pay": nothing moved.
NEGATED = re.compile(r'\b(?:dey ?nai|dei ?nai|dey ?ni|deini|dini|dei nii|pai ?nai|pay ?nai|paini|hoy ?nai|hoini|'
                     r'not paid|didn\'?t|did not|never|no payment)\b|দেয়নি|দেয়নি|দেইনি|দিইনি|দিনি|পাইনি|পায়নি|পায়নি|হয়নি|হয়নি', re.I)
# Plans and questions are not money that moved: "kal dibo", "will pay", "koto dibo?".
PLANNED = re.compile(r'\b(?:dibo|debo|dibe|debe|diben|deben|pabo|pabe|dite hobe|korbo|kinbo|will pay|will buy|going to|plan|planning|next week|'
                     r'agami|porer shoptahe)\b|দিব|দেব|পাবো|পাব(?:ে|েন)?(?![\u0980-\u09ff])|দিতে হবে|কিনব|করব|আগামী|পরের সপ্তাহে', re.I)
# "amake Sadia 500 dise", "আমাকে রহিম ৫০০ টাকা দিয়েছে": someone gave money TO the owner, so it came in.
TO_ME = re.compile(r'\b(?:amake|amare|amay|amaye)\b.{0,60}\b(?:dise|disey|dichhe|dicche|dilo|dilen|diyeche|diyechen|dise)\b|'
                   r'(?:আমাকে|আমারে|আমায়).{0,60}(?:দিসে|দিছে|দিল|দিলো|দিয়েছে|দিয়েছেন|দিয়েছে|দিলেন)|'
                   # "Karim 700 taka ferot dise": someone paid money back to the owner.
                   r'\bferot\s+(?:dise|dichhe|dicche|dilo|dilen|diyeche|diyechen)\b|'
                   r'ফেরত\s+(?:দিসে|দিছে|দিল|দিলো|দিয়েছে|দিয়েছেন|দিলেন)', re.I)
BN_NEGATED = re.compile(r'(?:দেয়|দেই|দিই|পাই|পায়|হয়)\s+নাই|(?:দিতে|পেতে)\s+পারিনি'
                        r'|(?:দিলাম|দিসি|দিছি|দিয়েছি|দিসে|দিছে|দিল|দিলো|পেলাম|পাইলাম|পাইসি|পাইছি|কিনলাম|করলাম|নিলাম|হইসে|হয়েছে)'
                        r'\s+না(?![\u0980-\u09ff])(?!\s+না)')
# "500 taka dilam na" (I did not give it): a verb followed by "na". "na na" is a correction, not a negation.
VERB_NA = re.compile(r'\b(?:dilam|disi|dichi|diyechi|dise|dilo|dey|dei|pailam|pelam|paisi|paichi|korlam|kinlam|kinsi|'
                     r'nilam|hoise|hoyeche)\s+na\b(?!\s+na\b)', re.I)


def intent(text):
    """'income', 'bill', 'expense', or a reason the part is not a money movement ('negated', 'planned', 'question', 'unclear')."""
    text = _norm(text)
    if NEGATED.search(text) or BN_NEGATED.search(text) or VERB_NA.search(text):
        return 'negated'
    if PLANNED.search(text):
        return 'planned'
    if '?' in text:
        return 'question'
    if REQUESTED.search(text):
        return 'requested'
    if BALANCE.search(text) and not (SPENT.search(text) or PURCHASE.search(text) or INCOME.search(text)):
        return 'balance'
    if BILL.search(text):
        return 'bill'
    if TO_ME.search(text):
        return 'income'
    if PURCHASE.search(text):
        return 'expense'
    if INCOME.search(text):
        return 'income'
    if SPENT.search(text):
        return 'expense'
    return 'unclear'


CORRECTION = r'\b(?:na\s+na|no\s+no|correction|i mean)\b|না\s+না|(?<!\w)মানে(?!\w)'
SPLIT = re.compile(rf'[.](?!\d)|[।!;\n]+|(?<=\?)|,(?!\d|\s*(?:{CORRECTION}))|'
                   r'\b(?:tarpor|tarpore|and then|then|arpor|erpor|after that)\b|'
                   r'আর তারপর|তারপরে|তারপর|এরপরে|এরপর|(?=\s+(?:ও তো|সে তো|কিন্তু)\s)|'
                   r'(?<=টাকা আছে)(?=\s)|(?<=টাকা ছিল)(?=\s)', re.I)
CONTRAST = re.compile(r'^(?:কিন্তু|but)\s+', re.I)
JOIN = re.compile(r'\b(?:and|ar|r)\b|(?<!\w)আর(?!\w)', re.I)
CORRECTED = re.compile(CORRECTION, re.I)
ISO_DAY = re.compile(r'\b20\d\d-\d\d-\d\d\b')
WORD_AGO = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(word) for word, value in _SMALL.items() if value <= 13)
                      + r')\s*(?:din|দিন|days?)\s*(?:age|আগে|ago)(?!\w)')
CURRENCY_WORDS = {
    'BDT': r'৳|টাকা|তাকা|\b(?:taka|takar|tk)\b',
    'USD': r'\busd\b|\bus\s+dollars?\b|ডলার',
    'EUR': r'€|\b(?:eur|euros?)\b|ইউরো',
    'GBP': r'£|\b(?:gbp|pounds? sterling)\b',
    'INR': r'₹|\b(?:inr|indian\s+rupees?)\b',
}
DOLLAR_CURRENCIES = {'USD', 'CAD', 'AUD', 'NZD', 'SGD', 'HKD'}
FILLER = re.compile(r'\b(?:taka|tk|bdt|dilam|diyechi|disi|pelam|pailam|e|te|theke|ke|r|ar|and|then|tarpor)\b|টাকা|দিলাম|পেলাম|থেকে', re.I)


def _payment_amounts(text):
    normalized = _norm(text)
    normalized = ISO_DAY.sub(' ', WORD_AGO.sub(' ', AGO.sub(' ', normalized)))
    return spoken_amounts(CORRECTED.split(normalized)[-1])


def _has_unreadable_amount_tail(part):
    """A word between a known spoken number and its currency can be a misheard part of that number."""
    tokens = TOKEN.findall(_norm(part))
    for i, token in enumerate(tokens):
        if token not in _SMALL and token not in _HUNDREDS and not token[0].isdigit():
            continue
        for end in range(i + 2, min(i + 4, len(tokens))):
            if tokens[end] not in {'টাকা', 'টাকার', 'তাকা', 'তাকার', 'কা', 'taka', 'takar'}:
                continue
            middle = tokens[i + 1:end]
            if all(word not in {'টাকা', 'টাকার', 'তাকা', 'তাকার', 'taka', 'takar', 'tk'} for word in middle):
                if any(word not in _SMALL and word not in _HUNDREDS and word not in SCALES
                       and word not in HUNDRED and word != 'and' and not word[0].isdigit() for word in middle):
                    return True
    return False


def _foreign_currency(part, currency):
    codes = {code for code in re.findall(r'\b[A-Z]{3}\b', part) if code in CURRENCIES}
    codes.update(code for code, pattern in CURRENCY_WORDS.items() if re.search(pattern, part, re.I))
    dollars = '$' in part or re.search(r'\bdollars?\b', part, re.I)
    return any(code != currency for code in codes) or (dollars and currency not in DOLLAR_CURRENCIES)


def _wordy(text):
    """At least two words that are not numbers, currency or filler: a clause, not just part of a number."""
    words = [w for w in TOKEN.findall(_norm(FILLER.sub(' ', text))) if not w[0].isdigit() and w not in _SMALL and w not in SCALES
             and w not in _HUNDREDS and w not in HUNDRED and w not in ('tk', 'taka', 'টাকা', 'and', 'ar')]
    return len(words) >= 2


def _note_parts(transcript):
    parts = []
    for fragment in SPLIT.split(transcript):
        fragment = fragment.strip(' ,')
        if len(fragment) < 3:
            continue
        # Keep number phrases such as "one hundred and fifty" together.
        start = 0
        for match in JOIN.finditer(fragment):
            left = fragment[start:match.start()].strip()
            right = fragment[match.end():].strip()
            # Two payments joined by "and"/"আর": split when both halves have an amount and real words, and at least one
            # half says what happened ("one hundred and fifty" has no words besides numbers, so it stays together).
            known = (intent(left) != 'unclear') + (intent(right) != 'unclear')
            if (known == 2 or (known and _wordy(left) and _wordy(right))) and _payment_amounts(left) and _payment_amounts(right):
                parts.append(left)
                start = match.end()
        parts.append(fragment[start:].strip())
    return parts


def _title(part):
    """A short label from the words of the part: numbers, days and filler removed."""
    words = [w for w in re.split(r'\s+', FILLER.sub(' ', part)) if w and not any(ch.isdigit() for ch in w.translate(BN_DIGITS))
             and _weekday(w) is None and w.casefold() not in _SMALL and w.casefold() not in SCALES and w.casefold() not in _HUNDREDS]
    return ' '.join(words[:5])[:40].strip(' ,.-') or ''


BN_DRAFT_LABELS = (
    (r'ক্রিকেট', 'ক্রিকেট', 'sports'),
    (r'স্ন্যাক্স|স্ন্যাকস|নাশতা|নাস্তা', 'স্ন্যাক্স / নাস্তা', 'food'),
    (r'ভাড়া', 'ভাড়া', 'transport'),
    (r'ধার.*(?:নিয়েছি|নিলাম|নিয়েছিলাম)|থেকে.{0,45}টাকা.{0,10}(?:নিয়েছি|নিলাম|নিয়েছিলাম)', 'টাকা নেওয়া', 'other'),
    (r'ওকে.{0,35}টাকা দিলাম|তাকে.{0,35}টাকা দিলাম|দিতে পেরেছি', 'টাকা দেওয়া', 'other'),
)


LATIN_DRAFT_LABELS = (
    (r'\btransport\b', 'Transport', 'transport'),
    (r'\b(?:inventory|stock kinsi)\b', 'Inventory', 'stock'),
    (r'\b(?:salary|beton)\b', 'Salary', 'salary'),
    (r'\b(?:electricity|biddut)\b', 'Electricity', 'utilities'),
    (r'\bdelivery\b', 'Delivery', 'delivery'),
    (r'\b(?:rent|vara|bhara)\b', 'Rent', 'rent'),
    (r'\bpackaging\b', 'Packaging', 'packaging'),
)


def _rule_label(part):
    for pattern, title, category in BN_DRAFT_LABELS + LATIN_DRAFT_LABELS:
        if re.search(pattern, _norm(part)):
            return title, category
    return _title(part), ''


def rule_entries(transcript):
    """A sentence-by-sentence reading used without AI, and to catch anything the AI missed."""
    entries = []
    for position, part in enumerate(_note_parts(transcript)):
        amounts = _payment_amounts(part)
        if amounts:
            title, category = _rule_label(part)
            entries.append(dict(evidence=part, amount=next(iter(amounts)) if len(amounts) == 1 else None,
                                type=intent(part), title=title, category=category, position=position))
    return entries


# ---------- splitting with AI ----------
SPLIT_SYSTEM = (
    'You split a small business owner\'s voice note into separate money entries. The note may be Bangla, Banglish '
    '(Bangla in English letters) or English, and may contain speech-recognition mistakes.\n'
    'Return JSON only: {"entries":[{"evidence":"the exact words of the note for this one entry, copied character for '
    'character","amount":number as said (taka, digits only),"type":"expense|income|bill","party":"who was paid or who '
    'paid, or empty","category":"one or two English words such as rent, transport, stock, salary, utilities, food, '
    'sales, repair, other","title":"a clean 2 to 4 word label in the language of the note, e.g. দোকান ভাড়া, '
    'Rahim theke bikri, Shop rent"}]}\n'
    'Rules: one entry for every separate payment, purchase or sale, even when they share a sentence; never merge two '
    'amounts; never invent an amount or a payment that was not said; convert number words to digits '
    '("dui hajar pachsho" = 2500, "দেড় হাজার" = 1500); if the speaker corrects an amount ("na na, 600"), give only '
    'the corrected amount; type "bill" only for money still owed to a supplier; leave out anything that did not '
    'happen (not paid, will pay later, questions).')


def _providers():
    from api.app.chat import llm
    return [p for p in llm.available() if not p.own]


def ai_entries(transcript, deadline):
    """Entries from the first AI service that returns valid JSON in time, or None."""
    from api.app.chat import llm
    messages = [{'role': 'system', 'content': SPLIT_SYSTEM}, {'role': 'user', 'content': transcript}]
    for provider in _providers():
        timeout = min(20, deadline - time.monotonic())
        if timeout <= 2:
            break
        payload = {'model': provider.model_name, 'messages': messages, 'temperature': 0, 'max_tokens': 2500,
                   'response_format': {'type': 'json_object'}}
        try:
            with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
                response = client.post(provider.url + '/chat/completions', json=payload, headers={'Authorization': 'Bearer ' + provider.key})
                if response.status_code == 400:  # a model without JSON mode: ask again without it
                    payload.pop('response_format')
                    response = client.post(provider.url + '/chat/completions', json=payload, headers={'Authorization': 'Bearer ' + provider.key})
            if response.status_code == 429:
                llm.rest(provider, 600)
                continue
            if response.status_code != 200:
                llm.rest(provider, 120)
                continue
            content = response.json()['choices'][0]['message']['content'] or ''
            content = content[content.find('{'):content.rfind('}') + 1]
            entries = json.loads(content).get('entries')
            if isinstance(entries, list):
                return entries, provider.name
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue
    return None


# ---------- checking and turning entries into drafts ----------
def _flat(text):
    """Lower case, digits made ASCII, punctuation and extra spaces removed: for exact quote matching."""
    return ' '.join(TOKEN.findall(_norm(text)))


def quoted(evidence, transcript):
    """The entry quotes the note: its words appear, in order and together, in the transcript."""
    words = _flat(evidence)
    return bool(words) and f' {words} ' in f' {_flat(transcript)} '


CATEGORY = re.compile(r'^[a-z][a-z /&-]{0,30}$')
SKIP_REASONS = {
    'negated': 'NOT_PAID', 'planned': 'PLANNED', 'question': 'QUESTION',
    'requested': 'REQUESTED_ONLY', 'balance': 'BALANCE_ONLY',
}


def _part_dates(parts, today, week_start):
    day = None
    dates = []
    for part in parts:
        warnings = []
        spoken = said_day(part, today, week_start)
        if spoken is not None:
            if day is not None and spoken < day and _weekday(part) is not None and week_start is None:
                warnings.append('The days in your note go backwards. Choose the week above if this was last week.')
            day = spoken
        elif day is None:
            warnings.append('No day was said, so today is used. Change it if needed.')
        effective = day or today
        unsafe = effective > today or effective < today - timedelta(days=60)
        if unsafe:
            warnings.append('Check the date.')
            effective = min(max(effective, today - timedelta(days=60)), today)
        dates.append((effective, warnings, unsafe))
    return dates


def checked_drafts(raw_entries, transcript, today, currency, digits, week_start=None, *, from_rules=False):
    """Validate model evidence and check the full clause for negation, currency and correction."""
    drafts, dropped = [], []
    parts = _note_parts(transcript)
    dates = _part_dates(parts, today, week_start)
    raw_entries = raw_entries or []
    if len(raw_entries) > MAX_ENTRIES:
        dropped.append(dict(evidence='', reason='TOO_MANY_ENTRIES', count=len(raw_entries) - MAX_ENTRIES))
    taken = set()
    amounts_in_parts = set()
    for raw in raw_entries[:MAX_ENTRIES]:
        if not isinstance(raw, dict):
            continue
        evidence = str(raw.get('evidence') or '').strip()[:MAX_TRANSCRIPT]
        quote = _flat(evidence)
        candidates = ([raw['position']] if from_rules else
                      [i for i, part in enumerate(parts) if quoted(evidence, part)])
        if not candidates:
            dropped.append(dict(evidence=evidence[:120], reason='NOT_IN_NOTE'))
            continue
        position = next((i for i in candidates if (i, quote) not in taken), candidates[0])
        part = parts[position]
        what = intent(part)
        if what in SKIP_REASONS:
            dropped.append(dict(evidence=part[:120], reason=SKIP_REASONS[what]))
            continue
        if _foreign_currency(part, currency):
            dropped.append(dict(evidence=part[:120], reason='CURRENCY_MISMATCH'))
            continue
        try:
            amount = round(float(str(raw.get('amount')).replace(',', '')), 2)
        except (TypeError, ValueError):
            amount = 0
        said = _payment_amounts(evidence)
        corrected = bool(CORRECTED.search(part))
        if corrected:
            said &= _payment_amounts(part)
        if not math.isfinite(amount) or amount <= 0 or amount not in said:
            reason = 'AMBIGUOUS_AMOUNT' if from_rules and len(said) > 1 else 'AMOUNT_NOT_SAID'
            dropped.append(dict(evidence=part[:120], reason=reason))
            continue
        minor = int(round(amount * 10 ** digits)) if amount <= 10**12 else 0
        if not 0 < minor <= 10**12:
            dropped.append(dict(evidence=part[:120], reason='AMOUNT_OUT_OF_RANGE'))
            continue
        if (position, minor) in amounts_in_parts:
            continue
        amounts_in_parts.add((position, minor))
        effective, date_warnings, unsafe_date = dates[position]
        warnings = date_warnings.copy()
        claimed = raw.get('type') if raw.get('type') in ('expense', 'income', 'bill') else None
        kind = what if what in ('expense', 'income', 'bill') else (claimed or 'expense')
        if what == 'unclear':
            warnings.append('No payment word was heard. Check whether this money was paid or received.')
        elif claimed and claimed != what:
            warnings.append('The type was set from your words. Check it.')
        party = re.sub(r'[\x00-\x1f]+', ' ', str(raw.get('party') or '')).strip()[:60]
        category = str(raw.get('category') or '').strip().casefold()
        category = category if CATEGORY.match(category) else ''
        title = re.sub(r'[\x00-\x1f]+', ' ', str(raw.get('title') or '')).strip()[:40] or _title(part) or category.capitalize()
        taken.add((position, quote))
        ambiguous = len(_payment_amounts(part)) > 1 or corrected
        if ambiguous:
            warnings.append('Two numbers were said here. Check that this is the right amount.')
        memo = ' · '.join(x for x in (title, party if party and party not in title else '') if x)[:200]
        draft = dict(ok=True, kind='invoice' if kind == 'bill' else 'cash', entry_type=kind, amount_minor=minor,
                     currency=currency, workspace_currency=currency, currency_mismatch=False,
                     effective_on=effective.isoformat(), memo=memo, title=title, warnings=warnings, can_save=True,
                     needs_review=what == 'unclear' or ambiguous or unsafe_date,
                     confidence=0.9 if not warnings else 0.6, evidence=part if corrected else evidence,
                     party=party, category=category, _position=position)
        if kind == 'bill':
            draft.update(due_on=(effective + timedelta(days=7)).isoformat(), vendor_name=party or 'Supplier',
                         invoice_number=f'VOICE-{uuid4()}')
        else:
            draft.update(direction='inflow' if kind == 'income' else 'outflow', status='cleared')
        drafts.append(draft)
    return drafts, dropped


def split_note(transcript, today, currency, digits, *, use_ai=True, deadline=None, week_start=None, source='typed'):
    """Use validated AI entries, with deterministic recovery for missed clauses."""
    drafts, dropped = checked_drafts(rule_entries(transcript), transcript, today, currency, digits, week_start, from_rules=True)
    method = 'rules'
    if use_ai:
        found = ai_entries(transcript, deadline or time.monotonic() + 30)
        if found:
            ai_drafts, ai_dropped = checked_drafts(found[0], transcript, today, currency, digits, week_start)
            dropped = list({(d['evidence'], d['reason']): d for d in ai_dropped + dropped}.values())
            if ai_drafts:
                method = 'ai'
                covered = {d['_position'] for d in ai_drafts}
                extras = [d for d in drafts if d['_position'] not in covered]
                for extra in extras:
                    extra['warnings'].append('Found by the backup reader. Check it, or untick it if it is not a payment.')
                    extra['confidence'], extra['needs_review'] = 0.5, True
                drafts = ai_drafts + extras
    drafts.sort(key=lambda d: d['_position'])
    # A contrast immediately after a request can repeat a transfer already stated just before it.
    # Keep every other repeated entry, since two distinct payments may have the same amount.
    parts = _note_parts(transcript)
    draft_by_part = {draft['_position']: draft for draft in drafts}
    repeated = set()
    for position, part in enumerate(parts):
        if position < 2 or not CONTRAST.match(part) or intent(parts[position - 1]) != 'requested':
            continue
        earlier, current = draft_by_part.get(position - 2), draft_by_part.get(position)
        if earlier and current and earlier['entry_type'] == current['entry_type'] == 'expense':
            same_amount = earlier['amount_minor'] == current['amount_minor']
            shared_recipient = re.search(r'(?:ওকে|তাকে|\bhim\b|\bher\b)', parts[position - 2]) and re.search(
                r'(?:ওকে|তাকে|\bhim\b|\bher\b)', part)
            if same_amount and shared_recipient:
                repeated.add(position)
    drafts = [draft for draft in drafts if draft['_position'] not in repeated]
    if len(drafts) > MAX_ENTRIES:
        dropped.append(dict(evidence='', reason='TOO_MANY_ENTRIES', count=len(drafts) - MAX_ENTRIES))
    for draft in drafts:
        if source == 'speech':
            draft['needs_review'] = True
            draft['warnings'].append('Check this amount against the recording before selecting the draft.')
            if _has_unreadable_amount_tail(parts[draft['_position']]):
                draft['amount_minor'] = 0
                draft['warnings'].append('Part of the amount is unclear. Enter the amount you heard.')
        del draft['_position']
    return drafts[:MAX_ENTRIES], dropped, method


# ---------- speech to text ----------
WHISPER_MODELS = ('whisper-large-v3', 'whisper-large-v3-turbo')
TRANSCRIPTION_BUDGET_SECONDS = 52
SPEECH_PROMPTS = {
    'bn': ('বাংলা বা বাংলিশে একজন দোকানির কথার হুবহু প্রতিলিপি। টাকার অঙ্ক, নাম, তারিখ, বিরতি, ধার চেয়েছিল, '
           'দিতে পেরেছি, দেয় নাই, হাতে আছে, ভাড়া, খরচ, বিকাশ, নগদ, bKash, taka, hajar।'),
    'en': 'Verbatim small-business voice note. Keep names, amounts, dates, corrections and negatives as spoken.',
}


def _vocabulary(db, business, language):
    """Give the transcriber a few relevant vendor spellings in addition to its language prompt."""
    names = set()
    try:
        ds = sec.original_dataset(db, business)
        names |= {i.vendor_id.replace('-', ' ') for i in ds.invoices[-60:]}
    except Exception:  # noqa: BLE001 - a hint only
        pass
    return SPEECH_PROMPTS[language] + ' ' + ', '.join(sorted(names)[:12])[:160]


def _speech_timeout(deadline, per_attempt=14):
    remaining = min(per_attempt, deadline - time.monotonic())
    if remaining < 1:
        return None
    return httpx.Timeout(remaining, connect=min(4, remaining), write=min(4, remaining))


def _uncertain_whisper(segments):
    """Use each segment's reported confidence without rewriting or discarding any words."""
    if not isinstance(segments, list):
        return False
    for segment in segments:
        if not isinstance(segment, dict) or not str(segment.get('text') or '').strip():
            continue
        if any(isinstance(segment.get(key), (float, int)) and check(segment[key]) for key, check in (
            ('avg_logprob', lambda value: value < -1.4),
            ('compression_ratio', lambda value: value > 2.8),
        )):
            return True
    return False


def whisper(audio, mime, language, hint, deadline, models=WHISPER_MODELS):
    from api.app.market import groq_key
    key = groq_key()
    if not key:
        return None, 'groq: no key', False
    reasons = []
    extensions = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'mp4',
                  'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/x-m4a': 'm4a', 'audio/aac': 'aac'}
    extension = extensions[mime.split(';')[0]]
    for model in models:
        timeout = _speech_timeout(deadline, 24 if model == WHISPER_MODELS[0] else 14)
        if timeout is None:
            break
        try:
            form = {'model': model, 'language': language, 'temperature': '0',
                    'response_format': 'json' if model == WHISPER_MODELS[1] else 'verbose_json'}
            if hint:
                form['prompt'] = hint
            with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
                response = client.post('https://api.groq.com/openai/v1/audio/transcriptions',
                                       headers={'Authorization': 'Bearer ' + key},
                                       data=form,
                                       files={'file': (f'note.{extension}', audio, mime)})
            if response.status_code == 200:
                body = response.json()
                text = str(body.get('text') or '').strip()
                if text:
                    return text, model, _uncertain_whisper(body.get('segments'))
            reasons.append(f'{model} {response.status_code}')
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            reasons.append(f'{model} {type(error).__name__}')
    return None, '; '.join(reasons), False


def _gemini_speech_key():
    direct = os.getenv('CAPZORYX_CHAT_GEMINI_KEY', '').strip()
    if direct:
        return direct
    try:
        endpoint = urlsplit(os.getenv('SAFELEDGER_LLM_BASE_URL', ''))
    except ValueError:
        return ''
    if endpoint.scheme == 'https' and endpoint.hostname == 'generativelanguage.googleapis.com':
        return os.getenv('SAFELEDGER_LLM_API_KEY', '').strip()
    return ''


def gemini_transcript(audio, mime, language, hint, deadline):
    from api.app.market import GEMINI_MODELS
    key = _gemini_speech_key()
    if not key:
        return None, 'gemini: no key'
    ask = ('Transcribe this voice note exactly as spoken, in the language spoken ('
           + ('Bangla, write Bangla words in Bangla script' if language == 'bn' else 'English') +
           '). Preserve names, amounts, negations, requests, cash balances and every self-correction in spoken order. '
           'Use punctuation at natural pauses. Never add, translate or summarise anything. Write [unclear] for an unreadable word. '
           'Words that may appear: ' + hint)
    body = {'contents': [{'parts': [{'inline_data': {'mime_type': mime.split(';')[0], 'data': base64.b64encode(audio).decode()}},
                                    {'text': ask}]}], 'generationConfig': {'temperature': 0}}
    reasons = []
    for model in GEMINI_MODELS[:2]:
        timeout = _speech_timeout(deadline, per_attempt=10)
        if timeout is None:
            break
        try:
            with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
                response = client.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                                       headers={'x-goog-api-key': key}, json=body)
            if response.status_code == 200:
                parts = response.json()['candidates'][0]['content']['parts']
                text = ''.join(p.get('text', '') for p in parts).strip()
                if text:
                    return text, model
            reasons.append(f'{model} {response.status_code}')
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            reasons.append(f'{model} {type(error).__name__}')
    return None, '; '.join(reasons)


def hf_transcript(audio, mime, language, hint, deadline):
    """Whisper large-v3 on Hugging Face's free inference, with the HF token Render already has."""
    key = (os.getenv('CAPZORYX_HISAB_HF_TOKEN') or os.getenv('HF_TOKEN') or '').strip()
    if not key:
        return None, 'hf: no key'
    timeout = _speech_timeout(deadline)
    if timeout is None:
        return None, 'hf: transcription budget exhausted'
    try:
        with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout) as client:
            response = client.post('https://router.huggingface.co/hf-inference/models/openai/whisper-large-v3',
                                   headers={'Authorization': 'Bearer ' + key, 'Content-Type': mime.split(';')[0]}, content=audio)
        if response.status_code == 200:
            text = str(response.json().get('text') or '').strip()
            if text:
                return text, 'hf-whisper-large-v3'
        return None, f'hf {response.status_code}'
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        return None, f'hf {type(error).__name__}'


def transcribe(audio, mime, language, hint, alternate=False):
    deadline = time.monotonic() + TRANSCRIPTION_BUDGET_SECONDS
    args = (audio, mime, language, hint, deadline)
    if alternate:
        text, model = gemini_transcript(*args)
        if text:
            return text, model
        text, model, _ = whisper(audio, mime, language, '', deadline, models=WHISPER_MODELS[::-1])
        if text:
            return text, model
        return hf_transcript(*args)
    text, model, uncertain = whisper(*args)
    if text and not uncertain:
        return text, model
    if not text:
        log.warning('voice note: whisper unavailable (%s)', model)
    backup, backup_model = gemini_transcript(*args)
    if backup:
        return backup, backup_model
    backup, backup_model = hf_transcript(*args)
    if backup:
        return backup, backup_model
    return (text, model) if text else (None, None)


def _speech_financial_signature(text, currency, today):
    if len(text) > MAX_TRANSCRIPT:
        return None
    digits = CURRENCIES[currency]['digits']
    drafts, dropped, _ = split_note(text, today, currency, digits, use_ai=False, source='speech')
    if (not drafts or any(not draft['amount_minor'] or intent(draft['evidence']) not in ('expense', 'income', 'bill')
                          for draft in drafts)
            or any(item['reason'] in ('AMOUNT_NOT_SAID', 'AMBIGUOUS_AMOUNT', 'TOO_MANY_ENTRIES') for item in dropped)):
        return None
    return [(draft['entry_type'], draft['amount_minor'], draft['effective_on']) for draft in drafts]


def compare_transcriptions(audio, mime, language, hint, currency, today):
    """Run independent transcribers, then compare the grounded cash movements in their answers."""
    deadline = time.monotonic() + TRANSCRIPTION_BUDGET_SECONDS
    args = (audio, mime, language, hint, deadline)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='voice-compare') as pool:
        primary = pool.submit(whisper, *args, models=WHISPER_MODELS[:1])
        second = pool.submit(whisper, audio, mime, language, '', deadline, models=WHISPER_MODELS[1:])
        gemini = pool.submit(gemini_transcript, *args) if _gemini_speech_key() else None
        main_text, main_model, _ = primary.result()
        second_text, second_model, _ = second.result()
        gemini_text, gemini_model = gemini.result() if gemini else (None, None)
    candidates = [(text, model) for text, model in (
        (main_text, main_model), (gemini_text, gemini_model), (second_text, second_model)
    ) if text]
    if not candidates:
        text, model = hf_transcript(*args)
        return text, model, [], 'single'
    text, model = candidates[0]
    alternatives = [{'transcript': other[:MAX_TRANSCRIPT], 'model': provider}
                    for other, provider in candidates[1:]]
    if not alternatives:
        return text, model, alternatives, 'single'
    signatures = [_speech_financial_signature(candidate, currency, today) for candidate, _ in candidates]
    same = signatures[0] is not None and all(signature == signatures[0] for signature in signatures[1:])
    return text, model, alternatives, 'agree' if same else 'review'


def speech_available():
    from api.app.market import groq_key
    return bool(groq_key() or _gemini_speech_key() or os.getenv('CAPZORYX_HISAB_HF_TOKEN') or os.getenv('HF_TOKEN'))


# ---------- routes ----------
class VoiceNote(Contract):
    audio_b64: str = Field(min_length=100, max_length=MAX_AUDIO_BYTES * 4 // 3 + 8)
    mime: str = Field(pattern=r'^audio/(webm|ogg|mp4|mpeg|wav|x-m4a|aac)(;codecs=[a-z0-9.]+)?$')
    language: str = Field(pattern=r'^(bn|en)$')
    consent: StrictBool
    mode: Literal['primary', 'alternate', 'compare'] = 'primary'


class NoteText(Contract):
    transcript: str = Field(min_length=2, max_length=MAX_TRANSCRIPT)
    week_start: date | None = None  # the first day of the week the note describes, when the owner chose one
    consent: StrictBool = False  # may the note go to an AI service to be split (otherwise rules only)
    source: Literal['typed', 'speech'] = 'typed'


def _owner_or_bookkeeper(actor):
    if actor.role not in ('owner', 'bookkeeper'):
        sec.fail(403, 'RECORDKEEPER_REQUIRED')


def _cloud_allowed(db, actor):
    from api.app.assist import owner_allows_cloud
    return owner_allows_cloud(db, actor.business_id)


@router.get('/voice-note')
def voice_note_status(actor=Depends(principal), db=Depends(db_connection)):
    return dict(speech=speech_available(), cloud_allowed=_cloud_allowed(db, actor), max_seconds=180, simulation_only=True)


@router.post('/voice-note')
def voice_note(body: VoiceNote, actor=Depends(principal), db=Depends(db_connection)):
    """Audio to text. The audio goes to the speech service and is not stored anywhere by Capzoryx."""
    _owner_or_bookkeeper(actor)
    if not body.consent or not _cloud_allowed(db, actor):
        sec.fail(403, 'VOICE_CONSENT_REQUIRED')
    budget = [('voice-note:' + actor.user_id, 6), ('voice-note-all', 30)]
    if body.mode == 'compare':
        budget.extend([('voice-compare:' + actor.user_id, 4), ('voice-compare-all', 16)])
    sec.budgets(db, budget)
    try:
        audio = base64.b64decode(body.audio_b64, validate=True)
    except (ValueError, binascii.Error):
        sec.fail(422, 'INVALID_VOICE_AUDIO')
    if not 1000 <= len(audio) <= MAX_AUDIO_BYTES:
        sec.fail(422, 'INVALID_VOICE_DURATION')
    with sec.transaction(db):
        hint = _vocabulary(db, actor.business_id, body.language)
        currency = sec.original_dataset(db, actor.business_id).currency if body.mode == 'compare' else None
    alternatives, agreement = [], 'single'
    if currency is None:
        text, model = transcribe(audio, body.mime, body.language, hint, alternate=body.mode == 'alternate')
    else:
        text, model, alternatives, agreement = compare_transcriptions(
            audio, body.mime, body.language, hint, currency, today_bd()
        )
    if not text:
        sec.fail(503, 'SPEECH_SERVICE_BUSY')
    return dict(transcript=text[:MAX_TRANSCRIPT], engine='whisper' if 'whisper' in model else 'gemini',
                model=model, truncated=len(text) > MAX_TRANSCRIPT, alternatives=alternatives,
                agreement=agreement, simulation_only=True)


@router.post('/batch-drafts')
def batch_drafts(body: NoteText, actor=Depends(principal), db=Depends(db_connection)):
    """One checked draft per entry in the note. Nothing is saved here."""
    _owner_or_bookkeeper(actor)
    sec.budgets(db, [('batch-drafts:' + actor.user_id, 10)])
    with sec.transaction(db):
        ds = sec.original_dataset(db, actor.business_id)
        cloud = body.consent and _cloud_allowed(db, actor)
    digits = CURRENCIES[ds.currency]['digits']
    today = today_bd()
    week = body.week_start if body.week_start and today - timedelta(days=60) <= body.week_start <= today else None
    drafts, dropped, method = split_note(body.transcript.strip(), today, ds.currency, digits, use_ai=cloud,
                                         week_start=week, source=body.source)
    return dict(drafts=drafts, dropped=dropped, method=method, simulation_only=True)
