"""Pure message understanding for Bangla, Banglish and English money chat.

Nothing here touches the database. It turns one short message into a structured guess that the brain checks
against the ledger. Scores are explainable keyword weights with typo tolerance, so a wrong guess can be traced.
"""
import re
import unicodedata
from dataclasses import dataclass, field

BANGLA_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
BENGALI = re.compile(r'[ঀ-৿]')

# Words that mark romanised Bangla. A message with two or more of these is answered in Banglish.
BANGLISH = {
    'ami', 'amar', 'amake', 'apni', 'apnar', 'tumi', 'tomar', 'koto', 'kto', 'ache', 'ase', 'achhe', 'nai', 'nei', 'ki',
    'kemon', 'acho', 'achen', 'taka', 'tk', 'dilam', 'dilm', 'diyechi', 'pelam', 'pailam', 'peyechi', 'niyechi', 'theke',
    'ke', 'koro', 'koren', 'bolo', 'bolen', 'dekhao', 'dekhan', 'hisab', 'khoroch', 'khorch', 'aay', 'paona', 'pabo',
    'baki', 'dena', 'dite', 'hobe', 'ajke', 'aj', 'aaj', 'kal', 'gotokal', 'mash', 'mashe', 'shoptah', 'ei', 'goto',
    'bhai', 'vai', 'apu', 'dhonnobad', 'thik', 'acha', 'accha', 'hae', 'haa', 'na', 'kora', 'korechi', 'hoyeche',
    'hoise', 'beshi', 'kom', 'cholche', 'obostha', 'bikri', 'becha', 'kara', 'kader', 'kar', 'kon', 'gula', 'shob',
    'samne', 'thakbe', 'hate', 'dao', 'den', 'diben', 'paben', 'lagbe', 'holo', 'korlam', 'korsi', 'cholbe', 'koi',
    'kothay', 'ekhon', 'mot', 'dekhi', 'bolo', 'janao', 'jante', 'chai', 'amr', 'apnr', 'kno', 'keno',
    'kot', 'kotto', 'ace', 'asay', 'aso', 'kmn', 'hoilo', 'hoise', 'mase', 'vai', 'samner', 'soptahe', 'dibo', 'kase',
    'manush', 'sondha', 'mile', 'hbe', 'tka',
}
PHRASE_BOOST = (
    ('how is my business', 'summary', 2), ('how s business', 'summary', 2), ('how is business', 'summary', 2),
    ('how are things', 'summary', 2.5), ('how am i doing', 'summary', 2.5), ('what s new', 'summary', 1.5),
    ('how much do i have', 'balance', 2), ('koto ache', 'balance', 1), ('কত আছে', 'balance', 1),
    ('কত টাকা আছে', 'balance', 2.5), ('koto taka ache', 'balance', 2.5), ('koto taka ase', 'balance', 2.5),
    ('dite hobe', 'payables', 2), ('দিতে হবে', 'payables', 2), ('owe me', 'receivables', 1.5),
    ('pete hobe', 'receivables', 2), ('পেতে হবে', 'receivables', 2), ('cash flow', 'forecast', 1),
    ('hate koto', 'balance', 1.5), ('hate kot', 'balance', 1.5), ('cash position', 'balance', 2), ('current cash', 'balance', 1.5),
    ('obostha hobe', 'forecast', 2), ('ki hobe', 'forecast', 1), ('hisab dao', 'summary', 1.5), ('sob mile', 'summary', 1.5),
    ('পুরো হিসাব', 'summary', 2), ('hisab janate chai', 'summary', 2), ('hisab jante chai', 'summary', 2),
    ('dokaner hisab', 'summary', 2), ('দোকানের হিসাব', 'summary', 2), ('হিসাব জানতে চাই', 'summary', 2),
)

# Finance topics. Weights add up; a topic needs at least 2.5 to count.
TOPICS = {
    'balance': {
        'cash': 2, 'balance': 3, 'blance': 3, 'balanc': 3, 'hand': 1.5, 'hate': 2, 'wallet': 1, 'available': 1,
        'হাতে': 2.5, 'নগদ': 2.5, 'ব্যালেন্স': 3, 'ক্যাশ': 2.5, 'জমা': 1,
    },
    'income': {
        'income': 3, 'earned': 3, 'earn': 3, 'earning': 3, 'earnings': 3, 'revenue': 3, 'sales': 2.5, 'sale': 2.5,
        'sold': 2.5, 'sell': 2.5, 'sells': 2.5, 'selling': 2.5, 'received': 2, 'aay': 3, 'ay': 1.5, 'bikri': 3, 'becha': 2.5, 'beche': 2.5,
        'kamai': 3, 'আয়': 3, 'আয়': 3, 'বিক্রি': 3, 'বেচা': 2.5, 'কামাই': 3, 'ইনকাম': 3,
    },
    'expenses': {
        'expense': 3, 'expenses': 3, 'spent': 3, 'spend': 3, 'spending': 3, 'cost': 2, 'costs': 2, 'khoroch': 3,
        'khorch': 3, 'khoros': 3, 'kharach': 3, 'খরচ': 3, 'ব্যয়': 3, 'ব্যয়': 3,
    },
    'payables': {
        'owe': 3, 'payable': 3, 'payables': 3, 'bill': 2, 'bills': 2.5, 'supplier': 2, 'suppliers': 2, 'vendor': 2,
        'vendors': 2, 'creditor': 2, 'creditors': 2, 'dena': 3, 'deno': 2, 'dite': 1.5, 'deya': 1.5,
        'বিল': 2.5, 'দেনা': 3, 'দিতে': 1.5, 'সাপ্লায়ার': 2, 'সাপ্লাইয়ার': 2, 'পাওনাদার': 3,
    },
    'receivables': {
        'receivable': 3, 'receivables': 3, 'owed': 3, 'owes': 3, 'debtor': 3, 'debtors': 3, 'customer': 2,
        'customers': 2, 'dues': 2, 'due': 1, 'paona': 3, 'pawna': 3, 'pabo': 3, 'pawa': 1.5, 'baki': 2, 'bakir': 2,
        'udhar': 2.5, 'rakhse': 1, 'rakhche': 1, 'rekheche': 1, 'পাওনা': 3, 'পাব': 3, 'বাকি': 2.5, 'কাস্টমার': 2, 'খদ্দের': 2, 'উধার': 2.5, 'দেনাদার': 3,
    },
    'forecast': {
        'forecast': 3, 'projection': 3, 'projected': 3, 'future': 2, 'predict': 3, 'prediction': 3, 'next': 1,
        'will': 1, 'upcoming': 1.5, 'purbabhash': 3, 'agami': 2, 'samne': 1.5, 'samner': 2, 'থাকবে': 2, 'পূর্বাভাস': 3,
        'আগামী': 2, 'সামনে': 1.5, 'থাকব': 1.5, 'thakbe': 2,
    },
    'summary': {
        'summary': 3, 'summarise': 3, 'summarize': 3, 'finances': 2, 'overview': 3, 'report': 2.5, 'business': 1.5, 'briefing': 3, 'brief': 2.5, 'update': 2,
        'status': 2, 'situation': 2, 'cholche': 2.5, 'obostha': 2, 'abostha': 2, 'hisab': 1.5, 'হিসাব': 1.5,
        'অবস্থা': 2, 'চলছে': 2.5, 'ব্যবসা': 1.5, 'ব্যবসার': 1.5, 'সারাংশ': 3, 'রিপোর্ট': 2.5,
    },
}
OVERDUE = {'overdue', 'late', 'pastdue', 'expired', 'bokeya', 'bokea', 'meyad', 'meyadi', 'deri', 'বকেয়া', 'বকেয়া',
           'মেয়াদোত্তীর্ণ', 'মেয়াদোত্তীর্ণ', 'দেরি', 'পেরোনো'}
LIST_WORDS = {'who', 'which', 'list', 'top', 'biggest', 'largest', 'most', 'whom', 'kara', 'kader', 'kar', 'kon',
              'gula', 'gulo', 'list', 'details', 'detail', 'bistarito', 'কারা', 'কাদের', 'কার', 'কোন', 'গুলো', 'তালিকা',
              'বিস্তারিত', 'লিস্ট', 'shobcheye', 'সবচেয়ে', 'সবচেয়ে'}

WINDOWS = (
    ('today', ('today', 'aj', 'aaj', 'ajke', 'aajke', 'আজ', 'আজকে', 'আজকের')),
    ('yesterday', ('yesterday', 'gotokal', 'gatkal', 'kalke', 'গতকাল', 'গতকালের')),
    ('this_week', ('this week', 'ei shoptah', 'ei shoptahe', 'ei saptahe', 'ei week', 'এই সপ্তাহে', 'এই সপ্তাহ', 'চলতি সপ্তাহে')),
    ('last_month', ('last month', 'previous month', 'goto mash', 'goto mashe', 'gelo mash', 'gelo mashe',
                    'গত মাসে', 'গত মাস', 'গত মাসের', 'আগের মাসে')),
    ('this_month', ('this month', 'ei mash', 'ei mashe', 'ei month', 'cholti mash', 'এই মাসে', 'এই মাস', 'এ মাসে', 'চলতি মাসে')),
)
HORIZON_30 = re.compile(r'\b(?:30|thirty|month|months|mash|mashe|tirish)\b|৩০|ত্রিশ|মাস', re.I)

SMALL_TALK = (
    ('greet', ('hi', 'hello', 'hey', 'hlw', 'helo', 'hii', 'yo', 'salam', 'slm', 'assalamualaikum', 'assalamu alaikum',
               'asalamualaikum', 'salamualaikum', 'as salamu alaikum', 'good morning', 'good evening', 'good afternoon',
               'shuvo sokal', 'suprovat', 'হ্যালো', 'হাই', 'সালাম', 'আসসালামু আলাইকুম', 'আসসালামুয়ালাইকুম', 'শুভ সকাল',
               'নমস্কার', 'nomoskar', 'adab', 'আদাব', 'শুভ সন্ধ্যা', 'শুভ বিকাল', 'shuvo sondha', 'good day', 'hey there')),
    ('how_are_you', ('how are you', 'how r u', 'how are u', 'hows it going', 'how is it going', 'kemon acho',
                     'kemon achen', 'kemon aso', 'kmn acho', 'kmn aso', 'kmn achen', 'kmn asen', 'kemon asen', 'ki obostha',
                     'ki khobor', 'ki obosta', 'কেমন আছ',
                     'কেমন আছো', 'কেমন আছেন', 'কী খবর', 'কি খবর')),
    ('thanks', ('thanks', 'thank you', 'thx', 'thnx', 'thanx', 'tnx', 'ty', 'thank u', 'tq', 'dhonnobad', 'dhonnobaad', 'dhanyabad', 'shukriya',
                'ধন্যবাদ', 'শুকরিয়া', 'শুকরিয়া', 'jazakallah')),
    ('bye', ('bye', 'goodbye', 'good night', 'see you', 'allah hafez', 'khoda hafez', 'bye bye', 'tata', 'আল্লাহ হাফেজ',
             'খোদা হাফেজ', 'বিদায়', 'বিদায়', 'শুভ রাত্রি')),
    ('who', ('who are you', 'what are you', 'your name', 'tumi ke', 'apni ke', 'ke tumi', 'ke apni', 'tomar nam',
             'apnar nam', 'তুমি কে', 'আপনি কে', 'তোমার নাম', 'আপনার নাম', 'are you a bot', 'are you human',
             'are you ai', 'tumi ki bot', 'apni ki manush', 'tumi ki manush', 'are you real', 'tumi ki robot')),
    ('help', ('help', 'menu', 'commands', 'what can you do', 'how to use', 'ki korte paro', 'ki korte paren',
              'ki ki paro', 'sahajjo', 'shahajjo', 'help me', 'সাহায্য', 'কী করতে পার', 'কি করতে পারো', 'কী কী পারো',
              'কি কি পারেন', '/help', '/start')),
    ('ack', ('ok', 'okay', 'okk', 'k', 'acha', 'accha', 'achha', 'thik ache', 'thik ase', 'hmm', 'hm', 'nice', 'great',
             'good', 'cool', 'got it', 'বুঝেছি', 'ঠিক আছে', 'আচ্ছা', 'ওকে', 'bujhchi', 'bujhsi', 'valo', 'bhalo', 'ভালো')),
)
YES = {'yes', 'y', 'yeah', 'yep', 'yup', 'sure', 'save', 'confirm', 'ok save', 'ha', 'haa', 'hae', 'hea', 'hya',
       'ji', 'jee', 'ji ha', 'thik ache', 'koro', 'koren', 'save koro', 'save koren', 'হ্যাঁ', 'হাঁ', 'জি', 'জ্বি',
       'সেভ', 'সেভ করো', 'ঠিক আছে', 'করো', 'করেন', '__save__'}
NO = {'no', 'n', 'nope', 'cancel', 'stop it', 'dont', "don't", 'na', 'naa', 'nah', 'thak', 'bad dao', 'baad dao',
      'cancel koro', 'না', 'বাদ দাও', 'থাক', 'বাতিল', '__cancel__'}
RECORD_VERB = re.compile(r'\b(?:received|paid|spent|got|sold|bought|dilam|diyechi|disi|pelam|pailam|peyechi|niyechi|'
                         r'nilam|kinlam|bechlam|khoroch korlam)\b|দিলাম|দিয়েছি|দিয়েছি|পেলাম|পেয়েছি|পেয়েছি|নিলাম|'
                         r'নিয়েছি|নিয়েছি|কিনলাম|বেচলাম|খরচ করলাম', re.I)
HAS_AMOUNT = re.compile(r'\d')
# Safety comes before anything else: these get a fixed, careful reply and never reach a model or the ledger.
SAFETY = (
    ('secret', re.compile(r'\b(?:pin|otp|password|passcode|cvv)\b|পিন|ওটিপি|পাসওয়ার্ড', re.I)),
    ('transfer', re.compile(r'\b(?:send|transfer)\b(?!.*\b(?:sent|paid)\b)|\bpathao\b|\bpathiye dao\b|\bpathan\b|পাঠাও|পাঠিয়ে দাও|পাঠিয়ে দাও', re.I)),
    ('others', re.compile(r'\b(?:other|another|onner|onno)\b.*\b(?:shops?|business(?:es)?|dokan|data)\b|অন্যের|অন্য দোকান', re.I)),
    ('injection', re.compile(r'\bignore\b.*\b(?:instructions?|rules?|previous)\b|\byou are now\b|\bsystem prompt\b', re.I)),
)
FORGOT = re.compile(r'\b(?:forgot|forget|bhule|bhulay|reset|change|bodlabo|bodlate)\b|ভুলে|বদলা', re.I)
CLARIFY_ONLY = {'koto', 'kto', 'kot', 'kotto', 'how', 'much', 'taka', 'tk', 'কত', 'টাকা'}
# Tax and VAT are advice questions, not ledger look-ups ("VAT koto dite hoy" is not about supplier bills).
TAX = re.compile(r'\b(?:vat|tax|taxes|nbr|tin|ait)\b|ভ্যাট|আয়কর|আয়কর|কর কত', re.I)
FIGURE_QUESTION = re.compile(r'\b(?:koto|kto|kot|kotto|how much|how many)\b|কত', re.I)
QUESTION = re.compile(r'[?？]|\b(?:koto|kto|kot|kotto|how much|how many|what|when|which|who|kobe|ki|kindly|please)\b'
                      r'|কত|কী|কি|কবে|কখন|জানতে চাই|জানাবেন', re.I)


@dataclass
class Parsed:
    text: str
    normalized: str
    tokens: list
    lang: str
    topic: str | None = None
    score: float = 0.0
    window: str | None = None
    overdue: bool = False
    listing: bool = False
    small_talk: str | None = None
    guide: str | None = None
    yes: bool = False
    no: bool = False
    record_like: bool = False
    question: bool = False
    scores: dict = field(default_factory=dict)


def normalize(value):
    text = unicodedata.normalize('NFKC', value).translate(BANGLA_DIGITS).casefold()
    return ' '.join(''.join(c if unicodedata.category(c)[0] in 'LMN' or c == '/' else ' ' for c in text).split())


def language(text, tokens):
    if BENGALI.search(text):
        return 'bn'
    hits = sum(token in BANGLISH for token in tokens)
    return 'bl' if hits >= 2 or (hits == 1 and len(tokens) <= 3) else 'en'


def near(word, keyword):
    """One edit apart, for words long enough that a typo is unlikely to be another word."""
    if len(keyword) < 5 or abs(len(word) - len(keyword)) > 1:
        return False
    if len(word) == len(keyword):
        diffs = [i for i, (a, b) in enumerate(zip(word, keyword)) if a != b]
        return len(diffs) == 1 or (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                                   and word[diffs[0]] == keyword[diffs[1]] and word[diffs[1]] == keyword[diffs[0]])
    short, long = sorted((word, keyword), key=len)
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def has_phrase(normalized, phrase):
    phrase = normalize(phrase)
    return bool(phrase) and re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', normalized) is not None


def topic_scores(tokens):
    scores = {}
    for topic, weights in TOPICS.items():
        total = 0.0
        for token in tokens:
            if token in weights:
                total += weights[token]
            elif len(token) >= 5:
                best = max((w for k, w in weights.items() if near(token, k)), default=0)
                total += best * 0.85
        if total:
            scores[topic] = round(total, 2)
    return scores


def parse(text):
    normalized = normalize(text)
    tokens = normalized.split()
    lang = language(text, tokens)
    parsed = Parsed(text=text, normalized=normalized, tokens=tokens, lang=lang)
    if not tokens:
        return parsed
    parsed.yes = normalized in YES
    parsed.no = normalized in NO
    for intent, pattern in SAFETY:
        if pattern.search(text) and not (intent == 'secret' and FORGOT.search(text)):
            parsed.small_talk = intent
            return parsed
    if set(tokens) <= CLARIFY_ONLY:
        parsed.small_talk = 'clarify'
        return parsed
    for intent, phrases in SMALL_TALK:  # an exact greeting such as "ki obostha" is small talk, not a report request
        if normalized in {normalize(p) for p in phrases}:
            parsed.small_talk = intent
            return parsed
    for window, phrases in WINDOWS:
        if any(has_phrase(normalized, p) for p in phrases):
            parsed.window = window
            break
    parsed.overdue = any(t in OVERDUE or (len(t) >= 6 and any(near(t, o) for o in OVERDUE if len(o) >= 5)) for t in tokens) \
        or has_phrase(normalized, 'past due')
    parsed.listing = any(t in LIST_WORDS for t in tokens) or has_phrase(normalized, 'ke ke')
    parsed.question = bool(QUESTION.search(text))
    parsed.record_like = bool(RECORD_VERB.search(text) and HAS_AMOUNT.search(normalized))
    scores = topic_scores(tokens)
    for phrase, topic, weight in PHRASE_BOOST:
        if has_phrase(normalized, phrase):
            scores[topic] = scores.get(topic, 0) + weight
    if parsed.question:
        # "koto?" / "how much?" next to a topic word is a clear question about that topic.
        for topic in scores:
            scores[topic] += 1
    if parsed.overdue and not ({'payables', 'receivables'} & scores.keys()):
        scores['receivables'] = scores.get('receivables', 0) + 1
    parsed.scores = scores
    if scores and not TAX.search(text):
        topic, score = max(scores.items(), key=lambda item: (item[1], item[0] == 'summary'))
        if score >= 2.5 or (len(tokens) <= 2 and score >= 2):  # a bare "cash" or "balance" is still a question
            parsed.topic, parsed.score = topic, score
    if not FIGURE_QUESTION.search(text) and not parsed.record_like:  # "how much…" is always about the books
        from api.app.chat.guide import HOW_WORDS, match
        guide = match(normalized, tokens, text)
        # A how-to word wins over a books topic ("bill kivabe add korbo"); otherwise the books answer first.
        if guide and (HOW_WORDS.search(text) or not parsed.topic):
            parsed.guide, parsed.topic = guide, None
            return parsed
    if parsed.topic is None and not parsed.record_like:
        short = len(tokens) <= 6
        for intent, phrases in SMALL_TALK:
            if any(has_phrase(normalized, p) for p in phrases) and (short or intent in ('who', 'help', 'how_are_you')):
                parsed.small_talk = intent
                break
    return parsed
