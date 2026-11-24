"""Bounded statement decoding. No OCR, network calls, account numbers or descriptions in results."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import csv
import io
import json
import re
import sys
import unicodedata
import zipfile


MAX_ROWS=20000
MAX_COLUMNS=80
MAX_PDF_PAGES=30
BN_DIGITS=str.maketrans('০১২৩৪৫৬৭৮৯','0123456789')
ALIASES={
    'day': {'date','transaction date','posting date','booking date','value date','txn date','তারিখ','লেনদেনের তারিখ'},
    'credit': {'credit','credit amount','money in','deposit','deposits','paid in','cr','জমা'},
    'debit': {'debit','debit amount','money out','withdrawal','withdrawals','paid out','dr','উত্তোলন'},
    'amount': {'amount','transaction amount','net amount','পরিমাণ','টাকা'},
    'direction': {'direction','type','transaction type','dr cr','credit debit','লেনদেনের ধরন'},
    'balance': {'balance','running balance','closing balance','account balance','ব্যালেন্স','স্থিতি'},
    'ref': {'reference','reference no','ref','txn id','trxid','transaction id','transaction number','chq no','রেফারেন্স'},
    'currency': {'currency','ccy','মুদ্রা'},
}
DIRECTIONS={'cr':'inflow','credit':'inflow','deposit':'inflow','in':'inflow','received':'inflow','জমা':'inflow',
            'dr':'outflow','debit':'outflow','withdrawal':'outflow','out':'outflow','sent':'outflow','উত্তোলন':'outflow'}


def normal(value):
    if value is None:return ''
    return unicodedata.normalize('NFC',str(value)).translate(BN_DIGITS).strip()


def column_name(value):
    return ' '.join(re.sub(r'[^\w\u0980-\u09ff]+',' ',normal(value).casefold()).split())


def parse_day(value,order):
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    text=normal(value)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):
        return date.fromisoformat(text).isoformat()
    seen=re.fullmatch(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})',text)
    if seen:
        first,second,year=map(int,seen.groups())
        month,day=(second,first) if order=='DMY' else (first,second)
        return date(year,month,day).isoformat()
    raise ValueError


def parse_minor(value,digits):
    s=normal(value)
    if not s or s in {'-','—'}:return 0
    if s[:1]=='(' and s[-1:]==')':s='-'+s[1:-1]
    if not re.fullmatch(r'[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?',s):
        raise ValueError
    number=Decimal(s.replace(',',''))*10**digits
    if number!=number.to_integral_value() or abs(number)>10**12:
        raise ValueError
    return int(number)


def column_map(row):
    indices={}
    for idx,value in enumerate(row):
        key=column_name(value)
        for field,options in ALIASES.items():
            if key in options:
                if field in indices:raise ValueError('STATEMENT_AMBIGUOUS_HEADERS')
                indices[field]=idx
    if 'day' in indices and ('amount' in indices or 'debit' in indices or 'credit' in indices):
        return indices
    return None


def csv_rows(raw):
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:raise ValueError('STATEMENT_CSV_ENCODING') from None
    first=text[:8192]
    try:dialect=csv.Sniffer().sniff(first,delimiters=',;\t')
    except csv.Error:
        counts={d:first.splitlines()[0].count(d) for d in ',;\t'} if first.splitlines() else {}
        if not counts:raise ValueError('STATEMENT_HEADERS_UNKNOWN')
        yield from csv.reader(io.StringIO(text,newline=''),delimiter=max(counts,key=counts.get),strict=True)
        return
    yield from csv.reader(io.StringIO(text,newline=''), dialect=dialect,strict=True)


def xlsx_rows(raw):
    from openpyxl import load_workbook
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items=archive.infolist()
        if len(items)>512 or sum(x.file_size for x in items)>32*1024**2:
            raise ValueError('STATEMENT_ARCHIVE_LIMIT')
        if any(n.startswith('xl/externalLinks/') or n.endswith('vbaProject.bin') for n in archive.namelist()):
            raise ValueError('STATEMENT_WORKBOOK_UNSUPPORTED')
    book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False)
    try:
        sheets=[s for s in book.worksheets if s.sheet_state=='visible']
        if len(sheets)!=1:raise ValueError('STATEMENT_USE_ONE_SHEET')
        sheet=sheets[0]
        if (sheet.max_row or 0)>MAX_ROWS+30 or (sheet.max_column or 0)>MAX_COLUMNS:
            raise ValueError('STATEMENT_SIZE_LIMIT')
        # Worksheet dimensions may be missing. Iterate the actual used range independently.
        sheet.reset_dimensions()
        for row in sheet.iter_rows(values_only=True):yield row
    finally:book.close()


def pdf_rows(raw):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(raw)) as doc:
        if not 1<=len(doc.pages)<=MAX_PDF_PAGES:
            raise ValueError('STATEMENT_PDF_PAGE_LIMIT')
        for page in doc.pages:
            if len(page.chars)>60000:
                raise ValueError('STATEMENT_SIZE_LIMIT')
            tables=page.find_tables()
            if tables:
                bounds=[table.bbox for table in tables]
                for word in page.extract_words():
                    x=(word['x0']+word['x1'])/2;y=(word['top']+word['bottom'])/2
                    if not any(x0<=x<=x1 and y0<=y<=y1 for x0,y0,x1,y1 in bounds):
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}',normal(word['text'])):
                            raise ValueError('STATEMENT_UNRECOGNIZED_SECTION')
                for table in tables:yield from table.extract()
            else:
                # For borderless text tables preserve physical spacing between columns.
                lines=(page.extract_text(layout=True) or '').splitlines()
                if not any(line.strip() for line in lines):
                    raise ValueError('STATEMENT_PDF_TEXT_REQUIRED')
                for line in lines:
                    if line.strip():yield re.split(r'\s{2,}',line.strip())
            page.close()


def parse_statement(raw,layout,currency,digits,order):
    source={'csv':csv_rows,'xlsx':xlsx_rows,'pdf':pdf_rows}[layout](raw)
    rows=[];columns=None
    for number,cells in enumerate(source,1):
        if number>MAX_ROWS+30 or len(cells)>MAX_COLUMNS:
            raise ValueError('STATEMENT_SIZE_LIMIT')
        if not any(normal(v) for v in cells):continue
        header=column_map(cells)
        if header:
            columns=header
            continue
        if columns is None:
            if number>=25 or any(re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}',normal(v)) for v in cells):
                raise ValueError('STATEMENT_HEADERS_UNKNOWN')
            continue
        if len(rows)>=MAX_ROWS:raise ValueError('STATEMENT_SIZE_LIMIT')
        def cell(field):
            idx=columns.get(field)
            return cells[idx] if idx is not None and idx<len(cells) else None
        row={'line':number}
        try:
            if not cells or any(isinstance(v,str) and v.startswith('=') for v in cells):
                raise ValueError
            day=parse_day(cell('day'),order)
            if normal(cell('currency')).upper() not in ('',currency):
                raise ValueError
            if 'debit' in columns or 'credit' in columns:
                debit=parse_minor(cell('debit'),digits);credit=parse_minor(cell('credit'),digits)
                if debit<0 or credit<0 or bool(debit)==bool(credit):raise ValueError
                signed=credit-debit
            else:
                signed=parse_minor(cell('amount'),digits)
                if 'direction' in columns:
                    direction=DIRECTIONS.get(normal(cell('direction')).casefold())
                    if not direction or (direction=='inflow' and signed<0):raise ValueError
                    signed=abs(signed) if direction=='inflow' else -abs(signed)
            if not signed:raise ValueError
            balance=parse_minor(cell('balance'),digits) if normal(cell('balance')) else None
            reference=normal(cell('ref')).casefold()
            if len(reference)>128:raise ValueError
            row.update(day=day,signed=signed,balance=balance,ref=reference)
        except (ValueError,InvalidOperation,OverflowError):
            row['reason']='UNREADABLE_ROW'
        rows.append(row)
    if not columns or not rows:
        raise ValueError('STATEMENT_HEADERS_UNKNOWN')
    # Infer posting order by dates. With a same-day-only statement, validate either direction.
    usable=[r for r in rows if 'day' in r]
    order_signs={(a['day']<b['day'])-(a['day']>b['day']) for a,b in zip(usable,usable[1:]) if a['day']!=b['day']}
    def breaks(group):
        previous=None;failures=set()
        for row in group:
            if 'signed' not in row:
                previous=None
                continue
            if previous is not None and row['balance'] is not None and previous+row['signed']!=row['balance']:
                failures.add(row['line'])
            previous=row['balance']
        return failures
    if len(order_signs)>1:
        for row in usable:row['reason']='DATES_OUT_OF_ORDER'
    else:
        forward=breaks(rows);backward=breaks(reversed(rows))
        failed=backward if order_signs=={-1} else forward if order_signs=={1} else min((forward,backward),key=len)
        for row in rows:
            if row['line'] in failed:row['reason']='BALANCE_DISCONTINUITY'
    return rows


def worker():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU,(8,9))
    resource.setrlimit(resource.RLIMIT_AS,(320*1024**2,320*1024**2))
    layout,currency,digits,order=sys.argv[1:]
    try:
        rows=parse_statement(sys.stdin.buffer.read(),layout,currency,int(digits),order)
        print(json.dumps({'rows':rows},separators=(',',':')))
    except Exception as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch(r'STATEMENT_[A-Z_]+',str(exc)) else 'STATEMENT_UNREADABLE_FILE'
        print(json.dumps({'error':code}))


if __name__=='__main__':
    worker()
