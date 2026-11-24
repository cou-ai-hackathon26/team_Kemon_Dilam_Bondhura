"""Capzoryx chat brain: understands a message, checks the ledger and answers like a helpful person.

Figures always come from the deterministic finance engine. A language model is only an optional fallback for
free-form questions, gets aggregate facts only, and is capped to a small reply. Writing a record needs a separate
owner opt-in plus an explicit Save tap for every draft.
"""
import json
import logging
import os
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from api.app import advisor
from api.app.chat import business_answers, context
from api.app import security as sec
from api.app.chat import understand
from api.app.currencies import CURRENCIES, scale

log = logging.getLogger('uvicorn.error')
DRAFT_TTL = 15 * 60
STATE_TTL = 6 * 3600


def schema(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS chat_state(chat_hash TEXT PRIMARY KEY,channel TEXT NOT NULL,business_id TEXT NOT NULL,
        lang TEXT,last_kind TEXT,last_window TEXT,pending_enc TEXT,pending_tag TEXT,updated_at INTEGER NOT NULL);
    """)
    if 'history_enc' not in {row[1] for row in db.execute('PRAGMA table_info(chat_state)')}:
        db.execute('ALTER TABLE chat_state ADD COLUMN history_enc TEXT')


@dataclass
class Context:
    channel: str
    chat_hash: str
    business: str
    user: str
    chat_enabled: bool = True
    records_enabled: bool = False
    settings_hint: str = 'Settings'
    role: str = 'owner'
    allow_llm: bool = True


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=list)  # (label, payload)
    save: dict | None = None
    llm: str | None = None
    lang: str = 'en'
    understood: bool = True


# ---------- formatting ----------
BN_MONTHS = ('জানুয়ারি', 'ফেব্রুয়ারি', 'মার্চ', 'এপ্রিল', 'মে', 'জুন', 'জুলাই', 'আগস্ট', 'সেপ্টেম্বর', 'অক্টোবর', 'নভেম্বর', 'ডিসেম্বর')
TO_BN = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')


def group_lakh(whole):
    digits = str(whole)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ','.join(parts + [tail])


def money(minor, code, lang='en'):
    digits = CURRENCIES[code]['digits']
    value = Decimal(minor) / scale(code)
    negative = value < 0
    value = abs(value)
    if code == 'BDT':
        whole = int(value)
        cents = int((value - whole) * 100)
        text = '৳' + group_lakh(whole) + (f'.{cents:02d}' if cents else '')
    else:
        text = f'{code} {value:,.{digits}f}'
        if digits and text.endswith('.' + '0' * digits):
            text = text[:-(digits + 1)]
    text = ('-' if negative else '') + text
    return text.translate(TO_BN) if lang == 'bn' else text


def day_label(value, lang):
    if lang == 'bn':
        return f'{value.day} {BN_MONTHS[value.month - 1]}'.translate(TO_BN)
    return f'{value.day} {value.strftime("%b")}'


def count(n, lang):
    return str(n).translate(TO_BN) if lang == 'bn' else str(n)


def pick(options):
    return random.choice(options) if isinstance(options, (list, tuple)) else options


WINDOW_TEXT = {
    'en': {None: 'so far', 'today': 'today', 'yesterday': 'yesterday', 'this_week': 'this week',
           'this_month': 'this month', 'last_month': 'last month'},
    'bl': {None: 'ekhon porjonto', 'today': 'aj', 'yesterday': 'gotokal', 'this_week': 'ei shoptahe',
           'this_month': 'ei mashe', 'last_month': 'goto mashe'},
    'bn': {None: 'এখন পর্যন্ত', 'today': 'আজ', 'yesterday': 'গতকাল', 'this_week': 'এই সপ্তাহে',
           'this_month': 'এই মাসে', 'last_month': 'গত মাসে'},
}

T = {
    'greet': {
        'en': ['{hello}. How may I help you with your accounts today?', '{hello}. What would you like to review today?'],
        'bl': ['{hello}. Apnar hisab-er kon bishoye shahajjo korte pari?', '{hello}. Aj ki dekhte chan?'],
        'bn': ['{hello}। আপনার হিসাবের কোন বিষয়ে সাহায্য করতে পারি?', '{hello}। আজ কী দেখতে চান?'],
    },
    'how_are_you': {
        'en': 'I am well, thank you. How may I assist you with your finances today?',
        'bl': 'Ami bhalo achi, dhonnobad. Apnar hisab-er kon bishoye shahajjo korte pari?',
        'bn': 'আমি ভালো আছি, ধন্যবাদ। আপনার হিসাবের কোন বিষয়ে সাহায্য করতে পারি?',
    },
    'thanks': {
        'en': ["You're welcome.", 'It is my pleasure to help.'],
        'bl': ['Apnake shagotom.', 'Shahajjo korte pere bhalo laglo.'],
        'bn': ['আপনাকে স্বাগতম।', 'সাহায্য করতে পেরে ভালো লাগল।'],
    },
    'bye': {
        'en': 'Thank you. I will let you know if a bill needs your attention.',
        'bl': 'Allah Hafez. Kono bill-er bishoye apnar mononibesh dorkar hole janabo.',
        'bn': 'আল্লাহ হাফেজ। কোনো বিলে আপনার মনোযোগ দরকার হলে জানাব।',
    },
    'who': {
        'en': "I am Capzoryx, a financial assistant for your business. I read your Capzoryx books and explain your cash, "
              "sales, expenses, receivables, payables and outlook in plain language.",
        'bl': 'Ami Capzoryx, apnar business-er arthik shohokari. Apnar Capzoryx hisab theke cash, bikri, khoroch, paona, '
              'dena ar shamner obostha shohoj bhashay bujhiye dei.',
        'bn': 'আমি Capzoryx, আপনার ব্যবসার আর্থিক সহকারী। আপনার Capzoryx হিসাব থেকে নগদ, বিক্রি, খরচ, পাওনা, দেনা আর '
              'সামনের অবস্থা সহজ ভাষায় বুঝিয়ে দিই।',
    },
    'help': {
        'en': "You may ask in your own words. For example:\n• How much cash do I have?\n• What did I spend this month?\n"
              "• Who owes me the most?\n• Are any bills overdue?\n• Please summarise my business.\n• Received 500 from Rahim",
        'bl': 'Apni nijer bhashay jiggesh korte paren. Jemon:\n• cash koto ache?\n• ei mashe khoroch koto?\n'
              '• ke sobcheye beshi paona?\n• bokeya bill ache?\n• business-er obostha ki?\n• Rahim theke 500 taka pelam',
        'bn': 'আপনি নিজের ভাষায় জিজ্ঞেস করতে পারেন। যেমন:\n• হাতে কত টাকা আছে?\n• এই মাসে খরচ কত?\n'
              '• কার কাছে সবচেয়ে বেশি পাওনা?\n• বকেয়া বিল আছে?\n• ব্যবসার অবস্থা কেমন?\n• রহিম থেকে ৫০০ টাকা পেলাম',
    },
    'ack': {'en': ['Noted.', 'Understood.'], 'bl': ['Thik ache.', 'Bujhechi.'], 'bn': ['ঠিক আছে।', 'বুঝেছি।']},
    'clarify': {
        'en': 'Which figure would you like — cash in hand, sales this month, expenses, or what customers owe you?',
        'bl': 'Kon hisab-ti jante chan — hate cash, ei mash-er bikri, khoroch, na customer-der kache paona?',
        'bn': 'কোন হিসাবটি জানতে চান — হাতে নগদ, এই মাসের বিক্রি, খরচ, না গ্রাহকদের কাছে পাওনা?',
    },
    'secret': {
        'en': '⚠️ Please never share a PIN, OTP or password with anyone, including me. If you have shared it, change '
              'it now through your bank or bKash app. I do not store or need it.',
        'bl': '⚠️ PIN, OTP ba password kauke diben na, amake-o na. Diye thakle ekhoni bank ba bKash app theke bodle nin. '
              'Ami eta rakhi na, proyojon-o nei.',
        'bn': '⚠️ পিন, ওটিপি বা পাসওয়ার্ড কাউকে দেবেন না, আমাকেও না। দিয়ে থাকলে এখনই ব্যাংক বা বিকাশ অ্যাপ থেকে বদলে নিন। '
              'আমি এগুলো রাখি না, প্রয়োজনও নেই।',
    },
    'transfer': {
        'en': 'I cannot send or move money. Once you have made the payment yourself, tell me, for example “Paid 5,000 to '
              'Rahim”, and I will record it.',
        'bl': 'Ami taka pathate pari na. Apni nije pathanor por bolben, jemon “Rahim ke 5000 dilam”, ami entry kore dibo.',
        'bn': 'আমি টাকা পাঠাতে পারি না। আপনি নিজে পাঠানোর পর বলবেন, যেমন “রহিমকে ৫০০০ দিলাম”, আমি এন্ট্রি করে দেব।',
    },
    'others': {
        'en': 'I can only see your own business records. Other businesses’ data is private and never shared.',
        'bl': 'Ami shudhu apnar nijer business-er hisab dekhte pari. Onno business-er tottho gopon, kokhono dewa hoy na.',
        'bn': 'আমি শুধু আপনার নিজের ব্যবসার হিসাব দেখতে পারি। অন্য ব্যবসার তথ্য গোপন, কখনো দেওয়া হয় না।',
    },
    'injection': {
        'en': 'I will continue as your Capzoryx financial assistant. How may I help with your accounts?',
        'bl': 'Ami Capzoryx-er arthik shohokari hishebei thakbo. Apnar hisab-er kon bishoye shahajjo korte pari?',
        'bn': 'আমি Capzoryx-এর আর্থিক সহকারী হিসেবেই থাকব। আপনার হিসাবের কোন বিষয়ে সাহায্য করতে পারি?',
    },
    'unknown': {
        'en': "I'm sorry, I could not understand that. I can help with your cash, sales, expenses, dues and bills — for "
              "example, “How much cash do I have?” or “Who owes me money?”",
        'bl': 'Dukkhito, bishoyti bujhte parini. Cash, bikri, khoroch, paona ba bill niye jiggesh korte paren — jemon '
              '“cash koto?” ba “ke koto paona?”',
        'bn': 'দুঃখিত, বিষয়টি বুঝতে পারিনি। নগদ, বিক্রি, খরচ, পাওনা বা বিল নিয়ে জিজ্ঞেস করতে পারেন — যেমন '
              '“হাতে কত টাকা আছে?” বা “কে কত পাওনা?”',
    },
    'off': {
        'en': 'Book replies are off for this chat. You can turn them on in Capzoryx → {settings}.',
        'bl': 'Ei chat-e book reply bondho ache. Capzoryx → {settings} theke chalu korte paren.',
        'bn': 'এই চ্যাটে হিসাবের উত্তর বন্ধ আছে। Capzoryx → {settings} থেকে চালু করতে পারেন।',
    },
    'records_off': {
        'en': 'I have understood: {draft}. To save entries from chat, please turn on “chat entries” in Capzoryx → '
              '{settings}. Until then, you can add it in the app.',
        'bl': 'Bujhechi: {draft}. Chat theke entry save korte Capzoryx → {settings} e “chat entries” chalu korun.',
        'bn': 'বুঝেছি: {draft}। চ্যাট থেকে এন্ট্রি সেভ করতে Capzoryx → {settings} এ “chat entries” চালু করুন।',
    },
    'draft': {
        'en': 'I have noted: {draft}. Shall I save this entry?', 'bl': 'Noted: {draft}. Entry-ti save korbo?',
        'bn': 'লিখে নিলাম: {draft}। এন্ট্রিটি সেভ করব?',
    },
    'draft_unclear': {
        'en': 'It appears you wish to record a payment, but the amount or direction is not clear. Please write, for '
              'example, “Received 500 from Rahim” or “Paid 1,200 to Karim”.',
        'bl': 'Mone hocche apni entry korte chan, kintu amount ba dik (pelam/dilam) spashto na. Erokom likhun: '
              '“Rahim theke 500 taka pelam” ba “Karim ke 1200 dilam”.',
        'bn': 'মনে হচ্ছে আপনি এন্ট্রি করতে চান, কিন্তু টাকার পরিমাণ বা দিক স্পষ্ট নয়। এভাবে লিখুন: '
              '“রহিম থেকে ৫০০ টাকা পেলাম” বা “করিমকে ১২০০ দিলাম”।',
    },
    'draft_currency': {
        'en': 'Chat entries are available for taka (BDT) books at present. Please add this one in the app.',
        'bl': 'Chat entry ekhon shudhu taka (BDT) hisab-e chole. Eti app theke jog korun.',
        'bn': 'চ্যাট এন্ট্রি এখন শুধু টাকার (BDT) হিসাবে চলে। এটি অ্যাপ থেকে যোগ করুন।',
    },
    'saved': {
        'en': 'Saved: {draft}. Your cash in hand is now {balance}.', 'bl': 'Save kora hoyeche: {draft}. Ekhon hate ache {balance}.',
        'bn': 'সেভ করা হয়েছে: {draft}। এখন হাতে আছে {balance}।',
    },
    'save_failed': {
        'en': 'I could not save that entry ({reason}). Nothing was changed; please add it in the app.',
        'bl': 'Entry-ti save kora jayni ({reason}). Kichu bodlay ni; app theke jog korun.',
        'bn': 'এন্ট্রিটি সেভ করা যায়নি ({reason})। কিছু বদলায়নি; অ্যাপ থেকে যোগ করুন।',
    },
    'cancelled': {'en': 'Understood. The entry was not saved.', 'bl': 'Thik ache, bad dilam. Kichu save hoy ni.',
                  'bn': 'ঠিক আছে, বাদ দিলাম। কিছু সেভ হয়নি।'},
    'nothing_pending': {'en': "There's nothing waiting to be saved.", 'bl': 'Save korar moto kichu nei.',
                        'bn': 'সেভ করার মতো কিছু নেই।'},
}
BUTTONS = {'save': {'en': 'Save', 'bl': 'Save', 'bn': 'সেভ'}, 'cancel': {'en': 'Cancel', 'bl': 'Bad dao', 'bn': 'বাদ দিন'},
           'list': {'en': 'Show list', 'bl': 'List dekhao', 'bn': 'তালিকা দেখাও'},
           'summary': {'en': 'Full summary', 'bl': 'Pura hisab', 'bn': 'পুরো হিসাব'}}


def say(key, lang, **values):
    return pick(T[key][lang]).format(**values)


def hello(lang):
    hour = datetime.now(understand_tz()).hour
    if lang == 'en':
        return 'Good morning' if 5 <= hour < 12 else 'Good afternoon' if 12 <= hour < 17 else 'Good evening' if hour < 22 else 'Hello'
    if lang == 'bl':
        return 'Assalamu alaikum'
    return 'আসসালামু আলাইকুম'


def understand_tz():
    from api.app.notifications.service import BD
    return BD


# ---------- state ----------
def load_state(db, ctx):
    row = db.execute('SELECT lang,last_kind,last_window,pending_enc,pending_tag,updated_at FROM chat_state WHERE chat_hash=?',
                     (ctx.chat_hash,)).fetchone()
    if not row or row[5] < sec.now() - STATE_TTL:
        return dict(lang=None, last_kind=None, last_window=None, pending=None)
    pending = None
    if row[3]:
        from api.app.services.receivables import customer_key, key_tag, unseal
        key = customer_key(db, ctx.business)
        if key and key_tag(key) == row[4]:
            data = json.loads(unseal(key, row[3], ctx.chat_hash + '/draft'))
            if data.get('expires', 0) > sec.now():
                pending = data
    return dict(lang=row[0], last_kind=row[1], last_window=row[2], pending=pending)


def save_state(db, ctx, lang=None, kind=None, window=None, pending=..., keep_topic=False):
    from api.app.services.receivables import customer_key, key_tag, seal
    current = db.execute('SELECT last_kind,last_window,pending_enc,pending_tag FROM chat_state WHERE chat_hash=?',
                         (ctx.chat_hash,)).fetchone()
    if keep_topic and current:
        kind, window = current[0], current[1]
    enc, tag = (current[2], current[3]) if current else (None, None)
    if pending is None:
        enc = tag = None
    elif pending is not ...:
        key = customer_key(db, ctx.business, create=True)
        enc, tag = seal(key, sec.canonical(pending).encode(), ctx.chat_hash + '/draft'), key_tag(key)
    db.execute("""INSERT INTO chat_state(chat_hash,channel,business_id,lang,last_kind,last_window,pending_enc,pending_tag,
                  updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_hash) DO UPDATE SET
                  channel=excluded.channel,business_id=excluded.business_id,lang=excluded.lang,last_kind=excluded.last_kind,
                  last_window=excluded.last_window,pending_enc=excluded.pending_enc,pending_tag=excluded.pending_tag,
                  updated_at=excluded.updated_at""",
               (ctx.chat_hash, ctx.channel, ctx.business, lang, kind, window, enc, tag, sec.now()))


def forget_chat(db, chat_hash):
    db.execute('DELETE FROM chat_state WHERE chat_hash=?', (chat_hash,))


# ---------- figures ----------
def figure(db, business, kind, window=None, ds=None):
    from api.app.copilot import books
    return books.compute(db, business, kind, window, None, ds)


def answer_topic(db, ctx, kind, window, lang, ds):
    result = figure(db, ctx.business, kind, window, ds)
    code = result['currency']
    amount = money(result['amount_minor'], code, lang)
    n = len(result['source_ids'])
    w = WINDOW_TEXT[lang][window]
    buttons = []
    if kind == 'balance':
        text = {'en': f'You have {amount} in cash right now.', 'bl': f'Ekhon apnar hate {amount} cash ache.',
                'bn': f'এখন আপনার হাতে {amount} নগদ আছে।'}[lang]
    elif kind == 'income':
        text = {'en': f'You took in {amount} {w}' + (f' across {n} entries.' if n else '.'),
                'bl': f'{w.capitalize()} income hoyeche {amount}' + (f' ({n} ta entry).' if n else '.'),
                'bn': f'{w} আয় হয়েছে {amount}' + (f' ({count(n, lang)}টি এন্ট্রি)।' if n else '।')}[lang]
        if not result['amount_minor']:
            text = {'en': f'No money came in {w}.', 'bl': f'{w.capitalize()} kono income ashe ni.',
                    'bn': f'{w} কোনো আয় আসেনি।'}[lang]
    elif kind == 'expenses':
        text = {'en': f'You spent {amount} {w}' + (f' across {n} entries.' if n else '.'),
                'bl': f'{w.capitalize()} khoroch hoyeche {amount}' + (f' ({n} ta entry).' if n else '.'),
                'bn': f'{w} খরচ হয়েছে {amount}' + (f' ({count(n, lang)}টি এন্ট্রি)।' if n else '।')}[lang]
        if not result['amount_minor']:
            text = {'en': f'Nothing was spent {w}.', 'bl': f'{w.capitalize()} kono khoroch hoy ni.',
                    'bn': f'{w} কোনো খরচ হয়নি।'}[lang]
    elif kind in ('payables', 'overdue_bills'):
        late = kind == 'overdue_bills'
        if not n:
            text = {'en': 'No overdue supplier bills.' if late else "You don't owe any suppliers right now.",
                    'bl': 'Kono bokeya bill nei.' if late else 'Ekhon kono supplier-er dena nei.',
                    'bn': 'কোনো বকেয়া বিল নেই।' if late else 'এখন কোনো সাপ্লায়ারের দেনা নেই।'}[lang]
        else:
            text = {'en': f'{n} supplier bill{"s are" if n > 1 else " is"} overdue — {amount} in total.' if late
                    else f'You owe suppliers {amount} on {n} open bill{"s" if n > 1 else ""}.',
                    'bl': f'{n} ta bill er meyad periye geche — mot {amount}.' if late
                    else f'Supplier-der dite hobe {amount} ({n} ta khola bill).',
                    'bn': f'{count(n, lang)}টি বিলের মেয়াদ পেরিয়েছে — মোট {amount}।' if late
                    else f'সাপ্লায়ারদের দিতে হবে {amount} ({count(n, lang)}টি খোলা বিল)।'}[lang]
            buttons = [(BUTTONS['list'][lang], '__list_payables__')]
    elif kind in ('receivables', 'overdue_receipts'):
        late = kind == 'overdue_receipts'
        if not n:
            text = {'en': 'No customer payments are overdue.' if late else 'No customer owes you anything right now.',
                    'bl': 'Kono customer er paona meyad periye jay ni.' if late else 'Ekhon kono customer er kache paona nei.',
                    'bn': 'কোনো কাস্টমারের পাওনা মেয়াদ পেরোয়নি।' if late else 'এখন কোনো কাস্টমারের কাছে পাওনা নেই।'}[lang]
        else:
            text = {'en': f'{amount} from customers is overdue ({n} due{"s" if n > 1 else ""}). A courteous reminder is advisable.'
                    if late else f'Customers owe you {amount} across {n} open due{"s" if n > 1 else ""}.',
                    'bl': f'Customer-der {amount} paona meyad periye geche ({n} ta). Ekta bhodro reminder dewa uchit.'
                    if late else f'Customer-der kache apnar paona {amount} ({n} ta).',
                    'bn': f'কাস্টমারদের {amount} পাওনার মেয়াদ পেরিয়েছে ({count(n, lang)}টি)। একটি সৌজন্যমূলক রিমাইন্ডার দেওয়া উচিত।'
                    if late else f'কাস্টমারদের কাছে আপনার পাওনা {amount} ({count(n, lang)}টি)।'}[lang]
            buttons = [(BUTTONS['list'][lang], '__list_receivables__')]
    else:
        days = 7 if kind == 'week' else 30
        now = figure(db, ctx.business, 'balance', None, ds)['amount_minor']
        diff = result['amount_minor'] - now
        change = money(abs(diff), code, lang)
        d = count(days, lang)
        text = {'en': f'If nothing changes, you should have about {amount} in {days} days'
                + (f' — {change} {"more" if diff > 0 else "less"} than today.' if diff else ', same as today.'),
                'bl': f'Kichu na bodlale {days} din pore hate thakbe pray {amount}'
                + (f' — ajker theke {change} {"beshi" if diff > 0 else "kom"}.' if diff else ', ajker motoi.'),
                'bn': f'কিছু না বদলালে {d} দিন পর হাতে থাকবে প্রায় {amount}'
                + (f' — আজকের চেয়ে {change} {"বেশি" if diff > 0 else "কম"}।' if diff else ', আজকের মতোই।')}[lang]
        if result['amount_minor'] < 0:
            text += {'en': ' ⚠️ This indicates a cash shortfall. Would you like to see whom you can collect from?',
                     'bl': ' ⚠️ Mane cash-e ghatti — ke ke theke tola jay dekhben?',
                     'bn': ' ⚠️ মানে নগদে ঘাটতি — কার কাছ থেকে টাকা তোলা যায় দেখবেন?'}[lang]
            buttons = [(BUTTONS['list'][lang], '__list_receivables__')]
    return text, buttons


def top_list(db, ctx, which, lang, ds):
    from api.app.services.finance import invoice_key
    rows = []
    code = ds.currency
    if which == 'payables':
        unique = {}
        for inv in sorted(ds.invoices, key=lambda i: i.invoice_id):
            unique.setdefault(invoice_key(inv), inv)
        totals = {}
        for inv in unique.values():
            if inv.status == 'open':
                late = inv.due_on < ds.as_of
                name = inv.vendor_id
                amount, overdue, due = totals.get(name, (0, False, inv.due_on))
                totals[name] = (amount + inv.amount_minor, overdue or late, min(due, inv.due_on))
        rows = sorted(totals.items(), key=lambda item: -item[1][0])[:5]
        if not rows:
            return {'en': 'No open supplier bills.', 'bl': 'Kono khola bill nei.', 'bn': 'কোনো খোলা বিল নেই।'}[lang]
        head = {'en': 'Biggest open bills:', 'bl': 'Sobcheye boro khola bill:', 'bn': 'সবচেয়ে বড় খোলা বিল:'}[lang]
        lines = [f'• {name} — {money(a, code, lang)}' + (' (overdue)' if late and lang == 'en' else ' (বকেয়া)' if late and lang == 'bn'
                 else ' (bokeya)' if late else f' · due {day_label(due, lang)}' if lang == 'en' else '') for name, (a, late, due) in rows]
        return head + '\n' + '\n'.join(lines)
    if ctx.role != 'owner':  # customer names stay with the owner
        return answer_topic(db, ctx, 'receivables', None, lang, ds)[0]
    from api.app.receivables import customer_book
    book = customer_book(db, ctx.business)
    names = {c['id']: c['name'] or '—' for c in book['customers']}
    totals = {}
    for debt in book['receivables']:
        if debt['open_minor'] > 0:
            amount, late = totals.get(debt['customer_id'], (0, False))
            totals[debt['customer_id']] = (amount + debt['open_minor'], late or debt['due_on'] < str(ds.as_of))
    rows = sorted(totals.items(), key=lambda item: -item[1][0])[:5]
    if not rows:
        return {'en': 'No customer owes you right now.', 'bl': 'Ekhon kono customer er kache paona nei.',
                'bn': 'এখন কোনো কাস্টমারের কাছে পাওনা নেই।'}[lang]
    head = {'en': 'Who owes you the most:', 'bl': 'Ke sobcheye beshi paona:', 'bn': 'কার কাছে সবচেয়ে বেশি পাওনা:'}[lang]
    late_tag = {'en': ' (overdue)', 'bl': ' (bokeya)', 'bn': ' (বকেয়া)'}[lang]
    return head + '\n' + '\n'.join(f'• {names.get(cid, "—")} — {money(a, code, lang)}' + (late_tag if late else '')
                                   for cid, (a, late) in rows)


def summary(db, ctx, lang, ds):
    from api.app.contracts import ScenarioRequest
    from api.app.services.finance import project_cashflow
    from api.app.services.receivables import open_receipts
    code = ds.currency
    m = lambda kind, window=None: figure(db, ctx.business, kind, window, ds)
    bal, inc, exp = m('balance'), m('income', 'this_month'), m('expenses', 'this_month')
    owe, owed, late_in = m('payables'), m('receivables'), m('overdue_receipts')
    late_out = m('overdue_bills')
    policy = db.execute('SELECT reserve_minor FROM security_policies WHERE business_id=?', (ctx.business,)).fetchone()
    month = project_cashflow(ds, ScenarioRequest(as_of=ds.as_of, horizon_days=30, reserve_minor=policy[0] if policy else 0,
                                                 delay_inflows_days=0), open_receipts(db, ctx.business))
    f = lambda r: money(r['amount_minor'], code, lang)
    week = m('week')
    if lang == 'en':
        lines = [f'Here’s where things stand ({day_label(ds.as_of, lang)}):', f'• Cash in hand: {f(bal)}',
                 f'• This month: {f(inc)} in, {f(exp)} out', f'• You owe suppliers: {f(owe)}'
                 + (f' ({f(late_out)} overdue)' if late_out['amount_minor'] else ''),
                 f'• Customers owe you: {f(owed)}' + (f' ({f(late_in)} overdue)' if late_in['amount_minor'] else ''),
                 f'• In 7 days: about {f(week)}']
    elif lang == 'bl':
        lines = [f'Apnar hisab ({day_label(ds.as_of, lang)}):', f'• Hate cash: {f(bal)}',
                 f'• Ei mashe: aay {f(inc)}, khoroch {f(exp)}', f'• Supplier-der dena: {f(owe)}'
                 + (f' ({f(late_out)} bokeya)' if late_out['amount_minor'] else ''),
                 f'• Customer-der kache paona: {f(owed)}' + (f' ({f(late_in)} bokeya)' if late_in['amount_minor'] else ''),
                 f'• 7 din pore: pray {f(week)}']
    else:
        lines = [f'আপনার হিসাব ({day_label(ds.as_of, lang)}):', f'• হাতে নগদ: {f(bal)}',
                 f'• এই মাসে: আয় {f(inc)}, খরচ {f(exp)}', f'• সাপ্লায়ারদের দেনা: {f(owe)}'
                 + (f' ({f(late_out)} বকেয়া)' if late_out['amount_minor'] else ''),
                 f'• কাস্টমারদের কাছে পাওনা: {f(owed)}' + (f' ({f(late_in)} বকেয়া)' if late_in['amount_minor'] else ''),
                 f'• ৭ দিন পর: প্রায় {f(week)}']
    if month.get('first_shortfall_on'):
        day = day_label(datetime.fromisoformat(str(month['first_shortfall_on'])).date(), lang)
        tip = {'en': f'⚠️ Please note: cash may fall below your safety reserve around {day}.',
               'bl': f'⚠️ Shabdhan: {day} er dike cash safety level er niche jete pare.',
               'bn': f'⚠️ সাবধান: {day} নাগাদ নগদ নিরাপদ সীমার নিচে নামতে পারে।'}[lang]
    elif late_in['amount_minor']:
        tip = {'en': f'Recommendation: collecting the overdue {f(late_in)} would strengthen your cash position.',
               'bl': f'Poramorsho: bokeya {f(late_in)} aday korle apnar nogod obostha shokto hobe.',
               'bn': f'পরামর্শ: বকেয়া {f(late_in)} তুলতে পারলে হাতে আরও টাকা থাকবে।'}[lang]
    else:
        tip = {'en': 'Your position looks stable.', 'bl': 'Apnar obostha sthitishil.', 'bn': 'আপনার অবস্থা স্থিতিশীল।'}[lang]
    return '\n'.join(lines + ['', tip])


# ---------- drafts ----------
def draft_text(draft, lang):
    amount = money(draft['amount_minor'], 'BDT', lang)
    party = draft.get('party') or ''
    day = datetime.fromisoformat(draft['day']).date()
    today = understand_today()
    when = {'en': 'today' if day == today else 'yesterday' if day == today - timedelta(days=1) else day_label(day, lang),
            'bl': 'aj' if day == today else 'gotokal' if day == today - timedelta(days=1) else day_label(day, lang),
            'bn': 'আজ' if day == today else 'গতকাল' if day == today - timedelta(days=1) else day_label(day, lang)}[lang]
    if draft['direction'] == 'inflow':
        return {'en': f'received {amount}' + (f' from {party}' if party else '') + f' {when}',
                'bl': (f'{party} theke ' if party else '') + f'{amount} pelen ({when})',
                'bn': (f'{party} থেকে ' if party else '') + f'{amount} পেলেন ({when})'}[lang]
    return {'en': f'paid {amount}' + (f' to {party}' if party else '') + f' {when}',
            'bl': (f'{party} ke ' if party else '') + f'{amount} dilen ({when})',
            'bn': (f'{party}কে ' if party else '') + f'{amount} দিলেন ({when})'}[lang]


def understand_today():
    from api.app.accounts import today_bd
    return today_bd()


def make_draft(text, ds):
    from api.app.hisab_hosted import facts, verified_party
    found = facts(text, understand_today())
    if not found:
        return None
    amount, direction, day = found
    party = verified_party(text, {})[:60].strip() if verified_party(text, {}) else ''
    if party.casefold() in ('ami', 'আমি', 'i', 'cash', 'taka', 'tk'):
        party = ''
    if day < ds.opening_on:
        return None
    return dict(amount_minor=int(amount * 100), direction=direction, day=str(day), party=party,
                request_id=str(uuid.uuid4()), expires=sec.now() + DRAFT_TTL)


# ---------- main entry ----------
def decide(db, ctx, text):
    """Understand one message inside the caller's transaction and return what to say next."""
    reply = _decide(db, ctx, text)
    if reply.text and not text.strip().startswith(('__', 'sms:')):
        remember(db, ctx, text, reply.text)
    return reply


SMS_BUTTON = re.compile(r'sms:(save|skip):([a-f0-9]{32})')


def _decide(db, ctx, text):
    sms = SMS_BUTTON.fullmatch(text.strip())
    if sms:
        from api.app.notifications import sms_inbox
        entry = sms_inbox.item(db, ctx.business, sms[2])
        if not entry:
            return Reply('That SMS was already handled.')
        if sms[1] == 'skip':
            sms_inbox.mark(db, ctx.business, sms[2], 'skipped')
            return Reply('Skipped — nothing was saved.')
        return Reply('', save=sms_inbox.as_draft(entry))
    p = understand.parse(text)
    state = load_state(db, ctx)
    lang = p.lang if p.lang != 'en' or not state['lang'] or len(p.tokens) > 2 else state['lang']
    normalized = text.strip()  # button payloads are matched raw; normalisation would strip the underscores
    if normalized in ('__list_receivables__', '__list_payables__', '__summary__', '__save__', '__cancel__'):
        lang = state['lang'] or 'en'

    if state['pending'] and (p.yes or p.no):
        if p.no:
            save_state(db, ctx, lang, pending=None, keep_topic=True)
            return Reply(say('cancelled', lang), lang=lang)
        if not ctx.records_enabled:
            return Reply(say('records_off', lang, draft=draft_text(state['pending'], lang), settings=ctx.settings_hint), lang=lang)
        return Reply('', save=state['pending'], lang=lang)
    if normalized in ('__save__', '__cancel__'):
        return Reply(say('nothing_pending', lang), lang=lang)

    if p.guide:
        from api.app.chat.guide import GUIDE, PAGE_LABEL
        _, page, answers = GUIDE[p.guide]
        buttons = [(f'Open {PAGE_LABEL[page]}', f'__open:{page}__')] if page and ctx.channel == 'web' else []
        return Reply(answers[lang], buttons=buttons, lang=lang)

    ask = None if p.small_talk else advisor.question(text)
    if ask:  # "what if the price drops 10%?", "minimum selling price?": deterministic answers from the owner's own figures
        if not ctx.chat_enabled:
            return Reply(say('off', lang, settings=ctx.settings_hint), lang=lang)
        if ctx.role != 'owner':
            return Reply('Only the owner can see product costs and break-even prices.', lang=lang)
        return Reply(advisor.answer(db, ctx.business, ask), lang=lang)

    about = None if p.small_talk or p.record_like else business_answers.detect(text)
    if about and not (p.topic and about in ('market', 'products')):  # "what is my business?", "give me ideas", "who are my competitors?"
        if not ctx.chat_enabled:
            return Reply(say('off', lang, settings=ctx.settings_hint), lang=lang)
        facts = context.gather(db, ctx.business, ctx.role)  # already inside the caller's transaction
        # Without a profile only "what is my business?" gets the set-up hint; other questions can still use the model.
        if facts.get('profile') or about == 'profile' or not facts.get('owner') or not (ctx.allow_llm and llm_available(db, ctx.business)):
            return Reply(business_answers.answer(about, facts, lang), lang=lang)

    if p.small_talk:
        save_state(db, ctx, lang, keep_topic=True)
        if p.small_talk == 'greet':
            reply = say('greet', lang, hello=hello(lang))
            if ctx.chat_enabled:
                ds = sec.runtime_dataset(db, ctx.business)
                bal = money(figure(db, ctx.business, 'balance', None, ds)['amount_minor'], ds.currency, lang)
                reply += ' ' + {'en': f'Cash in hand right now: {bal}.', 'bl': f'Ekhon hate ache {bal}.',
                                'bn': f'এখন হাতে আছে {bal}।'}[lang]
            return Reply(reply, buttons=[(BUTTONS['summary'][lang], '__summary__')] if ctx.chat_enabled else [], lang=lang)
        return Reply(say(p.small_talk, lang), lang=lang)

    wants_books = p.topic or p.record_like or normalized.startswith('__list') or normalized == '__summary__' \
        or ((p.window or p.listing) and state['last_kind'])
    if wants_books and not ctx.chat_enabled:
        return Reply(say('off', lang, settings=ctx.settings_hint), lang=lang)

    if p.record_like and p.topic not in ('summary',) and not (p.question and p.topic):
        if ctx.channel == 'web':  # the app reviews entries in its own draft card
            return Reply('', lang=lang, understood=False)
        ds = sec.runtime_dataset(db, ctx.business)
        if ds.currency != 'BDT':
            return Reply(say('draft_currency', lang), lang=lang)
        draft = make_draft(text, ds)
        if not draft:
            return Reply(say('draft_unclear', lang), lang=lang)
        if not ctx.records_enabled:
            return Reply(say('records_off', lang, draft=draft_text(draft, lang), settings=ctx.settings_hint), lang=lang)
        save_state(db, ctx, lang, pending=draft, keep_topic=True)
        return Reply(say('draft', lang, draft=draft_text(draft, lang)),
                     buttons=[(BUTTONS['save'][lang], '__save__'), (BUTTONS['cancel'][lang], '__cancel__')], lang=lang)

    ds = None
    if normalized in ('__list_receivables__', '__list_payables__'):
        ds = sec.runtime_dataset(db, ctx.business)
        which = normalized[7:-2]
        save_state(db, ctx, lang, kind=which)
        return Reply(top_list(db, ctx, which, lang, ds), lang=lang)
    if normalized == '__summary__' or p.topic == 'summary':
        ds = sec.runtime_dataset(db, ctx.business)
        save_state(db, ctx, lang, kind='summary')
        return Reply(summary(db, ctx, lang, ds), lang=lang)

    kind = p.topic
    window = p.window
    if not kind and (p.window or p.listing) and state['last_kind']:
        kind = state['last_kind']
    if kind == 'forecast':
        kind = 'month' if understand.HORIZON_30.search(p.text) else 'week'
    if kind in ('overdue_bills', 'overdue_receipts'):
        kind = 'payables' if kind == 'overdue_bills' else 'receivables'
    if kind in ('payables', 'receivables') and p.listing:
        ds = sec.runtime_dataset(db, ctx.business)
        save_state(db, ctx, lang, kind=kind)
        return Reply(top_list(db, ctx, kind, lang, ds), lang=lang)
    if kind == 'payables' and p.overdue:
        kind = 'overdue_bills'
    elif kind == 'receivables' and p.overdue:
        kind = 'overdue_receipts'
    if kind in ('balance', 'income', 'expenses', 'payables', 'receivables', 'overdue_bills', 'overdue_receipts', 'week', 'month'):
        ds = ds or sec.runtime_dataset(db, ctx.business)
        text_out, buttons = answer_topic(db, ctx, kind, window, lang, ds)
        save_state(db, ctx, lang, kind=kind, window=window)
        return Reply(text_out, buttons=buttons, lang=lang)

    save_state(db, ctx, lang, keep_topic=True)
    if ctx.chat_enabled and ctx.allow_llm and llm_available(db, ctx.business):
        return Reply('', llm=text, lang=lang)
    return Reply(say('unknown', lang), lang=lang, understood=False)


def finish(db, ctx, reply):
    """Run the slow or writing part of a decision outside the read transaction."""
    if reply.save is not None:
        return save_draft(db, ctx, reply.save, reply.lang)
    if reply.llm is not None:
        return ask_llm(db, ctx, reply.llm, reply.lang)
    return reply


def respond(db, ctx, text):
    with sec.transaction(db):
        reply = decide(db, ctx, text)
    return finish(db, ctx, reply)


def save_draft(db, ctx, draft, lang):
    from api.app.inputs import InputRecord, append_record
    from api.app.services.finance import fingerprint
    try:
        with sec.transaction(db):
            row = db.execute("""SELECT 1 FROM effective_security_users u JOIN workspace_meta w ON w.business_id=u.business_id
                                WHERE u.user_id=? AND u.business_id=? AND u.active=1 AND u.role='owner' AND w.archived_at IS NULL""",
                             (ctx.user, ctx.business)).fetchone()
            if not row:
                sec.fail(403, 'OWNER_REQUIRED')
            sec.verify_audit_head(db, ctx.business)
            ds = sec.original_dataset(db, ctx.business)
            if ds.currency != 'BDT':
                sec.fail(409, 'WORKSPACE_CURRENCY_CHANGED')
            body = InputRecord(request_id=draft['request_id'], kind='cash', amount_minor=draft['amount_minor'],
                               effective_on=draft['day'], direction=draft['direction'], status='cleared',
                               memo=(draft.get('party') or '') + f' · via {ctx.channel}', expected_revision=fingerprint(ds))
            actor = sec.Principal(ctx.user, ctx.business, 'owner', '')
            append_record(db, actor, body, manual_entry=True)
            sec.audit(db, actor, 'chat-' + draft['request_id'], 'CHAT_RECORD_CONFIRMED', {'channel': ctx.channel})
            if draft.get('sms_id'):
                from api.app.notifications import sms_inbox
                sms_inbox.mark(db, ctx.business, draft['sms_id'], 'saved')
            save_state(db, ctx, lang, pending=None, keep_topic=True)
            balance = figure(db, ctx.business, 'balance')['amount_minor']
        return Reply(say('saved', lang, draft=draft_text(draft, lang), balance=money(balance, 'BDT', lang)), lang=lang)
    except sec.PolicyError as error:
        return Reply(say('save_failed', lang, reason=str(getattr(error, 'code', '') or error.status)), lang=lang)
    except Exception:  # noqa: BLE001 - a chat must always get an answer; the ledger transaction already rolled back.
        log.exception('Chat record save failed.')
        return Reply(say('save_failed', lang, reason='error'), lang=lang)


# ---------- conversation memory and language model ----------
HISTORY_TURNS = 6


def load_history(db, ctx):
    from api.app.services.receivables import customer_key, key_tag, unseal
    row = db.execute('SELECT history_enc,pending_tag,updated_at FROM chat_state WHERE chat_hash=?', (ctx.chat_hash,)).fetchone()
    key = customer_key(db, ctx.business)
    if not row or not row[0] or not key or row[2] < sec.now() - STATE_TTL:
        return []
    try:
        box = json.loads(row[0])
        if box.get('tag') != key_tag(key):
            return []
        return [tuple(turn) for turn in json.loads(unseal(key, box['data'], ctx.chat_hash + '/history'))]
    except (ValueError, KeyError, TypeError):
        return []


def remember(db, ctx, user_text, bot_text):
    """Keep the last few turns, sealed with the workspace key, so follow-up questions have context."""
    from api.app.services.receivables import customer_key, key_tag, seal
    if not bot_text or not db.execute('SELECT 1 FROM chat_state WHERE chat_hash=?', (ctx.chat_hash,)).fetchone():
        return
    turns = (load_history(db, ctx) + [('user', user_text[:300]), ('assistant', bot_text[:300])])[-HISTORY_TURNS:]
    key = customer_key(db, ctx.business, create=True)
    box = dict(tag=key_tag(key), data=seal(key, sec.canonical(turns).encode(), ctx.chat_hash + '/history'))
    db.execute('UPDATE chat_state SET history_enc=?,updated_at=? WHERE chat_hash=?', (sec.canonical(box), sec.now(), ctx.chat_hash))


def llm_available(db, business):
    from api.app.chat import llm
    return os.getenv('SAFELEDGER_CHAT_LLM', '1') == '1' and llm.configured()


def ask_llm(db, ctx, question, lang):
    from api.app.assist import owner_allows_cloud
    from api.app.chat import llm
    try:
        with sec.transaction(db):
            facts = context.text(db, ctx.business, ctx.role)  # the whole business, not only the cash books
            history = load_history(db, ctx)
            share = owner_allows_cloud(db, ctx.business)
        answer, provider = llm.reply(question, history, facts, share_facts=share)
        log.info('chat answer: provider=%s', provider or 'none')  # which model answered; never the content
        if answer:
            with sec.transaction(db):
                remember(db, ctx, question, answer)
            return Reply(answer, lang=lang)
    except Exception:  # noqa: BLE001 - provider failures fall back to the built-in reply.
        log.warning('Chat language model unavailable; using the built-in reply.')
    return Reply(say('unknown', lang), lang=lang, understood=False)
