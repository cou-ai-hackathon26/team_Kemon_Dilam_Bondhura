"""Extract explicit quote terms. Unlabelled, ambiguous and unsupported terms stay empty."""
from decimal import Decimal
import re
import unicodedata
from api.app.currencies import CURRENCIES

NUMBER=r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
BN_DIGITS=str.maketrans('০১২৩৪৫৬৭৮৯','0123456789')


def explicit(pattern,text):
    values={m[1].strip() for m in re.finditer(pattern,text,re.I)}
    return values.pop() if len(values)==1 else None


def read_offer(text,currency):
    text=unicodedata.normalize('NFC',text).translate(BN_DIGITS)
    text=re.sub(r'[;।]|(?<!\d)\.(?=\s+[A-Z])','\n',text)
    terms={}
    digits=CURRENCIES[currency]['digits']
    symbols={'BDT':r'Tk\.?|৳|টাকা','USD':r'\$','EUR':'€','GBP':'£'}
    units='|'.join([re.escape(currency)]+([symbols[currency]] if currency in symbols else []))
    currency_tokens=set(re.findall(r'\b[A-Z]{3}\b',text))
    currency_tokens.intersection_update(CURRENCIES)
    for code,pattern in symbols.items():
        if re.search(pattern,text,re.I):currency_tokens.add(code)
    if currency_tokens and currency_tokens!={currency}:
        return dict(terms={},warnings=['CURRENCY_MISMATCH'])

    def money(labels):
        pattern=rf'(?:^|\n)\s*(?:{labels})\s*[:=]?\s*(?:(?:{units})\s*)?({NUMBER})\s*(?:(?:{units})\s*)?(?=$|\n)'
        raw=explicit(pattern,text)
        if raw is None:return None
        n=Decimal(raw.replace(',',''))*10**digits
        return int(n) if n==n.to_integral_value() and 0<=n<=10**12 else None

    murabaha=bool(re.search(r'murabaha|মুরাবাহা',text,re.I))
    for field,labels in [('principal_minor',r'principal|loan amount|financed amount|cash price|asset cost|ঋণের পরিমাণ|নগদ মূল্য'),
                         ('upfront_fee_minor',r'(?:upfront |processing )?fee(?:s)?|প্রসেসিং ফি|ফি'),
                         ('markup_minor',r'(?:fixed )?markup|profit amount|মুনাফা|মার্কআপ')]:
        value=money(labels)
        if value is not None and (field!='markup_minor' or murabaha):
            terms[field]=value
    if re.search(r'(?:^|\n)\s*(?:no (?:upfront |processing )?fees?|ফি নেই)\s*(?:$|\n)',text,re.I):
        if 'upfront_fee_minor' in terms and terms['upfront_fee_minor']!=0:
            terms.pop('upfront_fee_minor')
        else:terms['upfront_fee_minor']=0
    lender=explicit(r'(?:^|\n)\s*(?:lender|bank|provider|ঋণদাতা|ব্যাংক)\s*[:=]\s*([^\n]{1,100})',text)
    if lender:terms['lender_name']=lender
    rates=set()
    for line in text.splitlines():
        if re.search(r'\b(?:interest|rate|markup|reducing|flat)\b|হার|মুনাফা|মার্কআপ',line,re.I) and not re.search(r'\bfee|চার্জ|ফি',line,re.I):
            rates.update(re.findall(r'(?<![\d.])([0-9]+(?:\.[0-9]{1,2})?)\s*%',line))
    if len(rates)==1:
        rate=Decimal(rates.pop())*100
        if 0<=rate<=20000:terms['rate_bps']=int(rate)
    if murabaha:
        terms['product_type']='murabaha';terms['rate_type']='murabaha_markup'
        sale=money(r'(?:total )?sale price|deferred price|বিক্রয়মূল্য')
        principal=terms.get('principal_minor')
        if sale is not None and principal is not None and sale>=principal:
            inferred=sale-principal
            if 'markup_minor' not in terms or inferred==terms['markup_minor']:
                terms['markup_minor']=inferred
            else:
                terms.pop('markup_minor')
                return dict(terms=terms,warnings=['INCONSISTENT_MARKUP'])
        if principal and 'markup_minor' in terms and 'rate_bps' in terms:
            if abs(principal*terms['rate_bps']-terms['markup_minor']*10000)>10000:
                terms.pop('markup_minor')
                return dict(terms=terms,warnings=['INCONSISTENT_MARKUP'])
        if principal and 'markup_minor' not in terms and 'rate_bps' in terms and not re.search(r'annual|per year|per month|বার্ষিক|মাসিক হার',text,re.I):
            terms['markup_minor']=(principal*terms['rate_bps']+5000)//10000
    else:
        flat=bool(re.search(r'\bflat\b|ফ্ল্যাট',text,re.I))
        reducing=bool(re.search(r'\breducing balance\b|\bdeclining balance\b|ক্রমহ্রাসমান',text,re.I))
        annual=bool(re.search(r'\bannual(?:ly)?\b|per annum|per year|বার্ষিক',text,re.I))
        # The comparison engine supports a whole-term flat rate or an annual reducing rate.
        if flat and not reducing and not annual:terms['rate_type']='flat_per_term'
        if reducing and annual and not flat:terms['rate_type']='annual_reducing'
    matches=set(re.findall(r'(?:tenor|term|মেয়াদ|মেয়াদ)\s*[:=]?\s*([0-9]{1,4})\s*(days?|months?|দিন|মাস)',text,re.I))
    if len(matches)==1:
        n,u=matches.pop();u='days' if u.lower().startswith('day') or u=='দিন' else 'months'
        if 0<int(n)<=(1825 if u=='days' else 60):terms.update(term=int(n),unit=u)
    if re.search(r'(?:monthly|প্রতি মাসে|মাসিক).{0,10}(?:installments?|instalments?|কিস্তি)|equal monthly',text,re.I):
        counts=re.findall(r'([0-9]+)\s+(?:equal\s+)?monthly (?:instalments?|installments?)',text,re.I)
        if not counts or (terms.get('unit')=='months' and all(int(n)==terms.get('term') for n in counts)):
            terms['repayment']='equal_installments'
    elif re.search(r'single (?:payment|repayment)|at maturity|one payment|এক কিস্তি',text,re.I):
        terms['repayment']='single_payment'
    return dict(terms=terms,warnings=[])
