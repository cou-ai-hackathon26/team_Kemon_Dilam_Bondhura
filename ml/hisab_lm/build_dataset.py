"""Build a verified synthetic chat corpus and a deterministic downloadable archive."""
import argparse
import hashlib
import json
import random
import re
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from api.app.agents.models import TOOLS
from api.app.contracts import ScenarioRequest
from api.app.services.finance import Dataset, project_cashflow, snapshot

SEED = 20261121
TASKS = ("extract_text","bill_text","offer","briefing","message","agent_plan")
NAMES = [("শাপলা ট্রেডার্স","Shapla Traders"),("মেঘা দোকান","Megha Shop"),("নীল কাগজ","Neel Paper"),
         ("পদ্মা বাজার","Padma Bazar"),("বট স্টোর","Bott Store"),("তারা চাল","Tara Rice")]
BN = str.maketrans("0123456789","০১২৩৪৫৬৭৮৯")
LEVEL = {
    "extract_text":"Extract a cash record as JSON with kind, direction, amount (decimal taka string), currency, date and vendor.",
    "bill_text":"Read the supplied bill transcription. Return JSON with kind, amount (grand total in decimal taka), currency, date, due_date, vendor and invoice_number.",
    "offer":"Extract the quoted principal_minor in poisha, fee_minor in poisha, term_days and rate_bps as integers and currency as an ISO code.",
    "briefing":"Explain the verified cash figures in one sentence in the requested language. Return JSON with a text field. Use only the supplied figures.",
    "message":"Draft a courteous payment reminder in the requested language. Use only the stated amount and due date. Return JSON with a text field.",
    "agent_plan":"Select a read-only financial tool. Return one JSON object with tool and arguments.",
}


def encoded(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))


def money(minor):
    return f"{minor//100}.{minor%100:02}"


def create(i,rng):
    task=TASKS[i%len(TASKS)]
    lang=("bn","bn-Latn","en")[i//len(TASKS)%3]
    name=rng.choice(NAMES)[0 if lang=="bn" else 1]
    day=date(2026,1,1)+timedelta(days=rng.randrange(365))
    due=day+timedelta(days=rng.randint(1,30))
    amount=rng.randrange(5000,10000000)
    to_local=lambda s:str(s).translate(BN) if lang=="bn" else str(s)
    major=to_local(money(amount))
    kind="inflow" if rng.randrange(2) else "outflow"
    direction={"bn":("পেলাম","দিলাম"),"bn-Latn":("pailam","dilam"),"en":("received","paid")}[lang][kind=="outflow"]
    facts={"currency":"BDT","date":str(day),"amount_minor":amount,"vendor":name}
    if task=="extract_text":
        facts["direction"]=kind
        question={"bn":f"{day} তারিখে {name}{' থেকে' if kind=='inflow' else 'কে'} {major} টাকা {direction}।",
                  "bn-Latn":f"{day} {name} {'theke' if kind=='inflow' else 'ke'} {major} taka {direction}.",
                  "en":f"On {day} I {direction} BDT {major} {'from' if kind=='inflow' else 'to'} {name}."}[lang]
        gold=dict(kind="cash",direction=kind,amount=money(amount),currency="BDT",date=str(day),vendor=name)
    elif task=="bill_text":
        tax=amount//10; ref="SYN-"+str(i+1001)
        facts.update(net_minor=amount-tax,tax_minor=tax,due_date=str(due),invoice_number=ref)
        labels={"bn":("চালান","উপমোট","কর","মোট","পরিশোধের তারিখ"),"bn-Latn":("Chalan","Upomot","Kor","Mot","Dewar tarikh"),
                "en":("Invoice","Subtotal","Tax","Total due","Due on")}[lang]
        question=(f"{labels[0]} {ref} | {name} | {day}\n{labels[1]} {to_local(money(amount-tax))}\n"
                  f"{labels[2]} {to_local(money(tax))}\n{labels[3]} BDT {major}\n{labels[4]} {due}")
        gold=dict(kind="invoice",amount=money(amount),currency="BDT",date=str(day),due_date=str(due),vendor=name,invoice_number=ref)
    elif task=="offer":
        fee=rng.randrange(0,min(amount//10,8901));term=rng.choice([30,60,90,180,365]);rate=rng.choice([500,650,750,800,1000,1150])
        terms=(major,to_local(money(fee)),to_local(term),to_local(money(rate)))
        question={"bn":"মূলধন ৳%s, শুরুতে ফি ৳%s, মেয়াদ %s দিন, পুরো মেয়াদের সুদ %s%%।",
                  "bn-Latn":"Mul taka %s, fee %s taka, meyad %s din, flat rate %s%%.",
                  "en":"The quote offers BDT %s with an upfront fee of BDT %s for %s days at a flat rate of %s%%."}[lang]%terms
        facts.update(fee_minor=fee,term_days=term,rate_bps=rate)
        gold=dict(principal_minor=amount,fee_minor=fee,term_days=term,rate_bps=rate,currency="BDT")
    elif task=="briefing":
        reserve=rng.randint(1000,10000000)
        facts.update(reserve_minor=reserve)
        question=f"language={lang}; currency=BDT; cash={money(amount)}; reserve={money(reserve)}"
        diff=amount-reserve
        # The source explicitly includes the engine's difference so generation cannot invent a number.
        question+=f"; {'surplus' if diff>=0 else 'shortfall'}={money(abs(diff))}"
        word={"bn":("বেশি","কম"),"bn-Latn":("beshi","kom"),"en":("above","below")}[lang][diff<0]
        value=to_local(money(abs(diff)))
        text={"bn":f"নগদ {major} টাকা, যা সংরক্ষিত সীমার চেয়ে {value} টাকা {word}।",
              "bn-Latn":f"Nogod {major} taka, reserve er cheye {value} taka {word}.",
              "en":f"Cash is BDT {major}, which is BDT {value} {word} the reserve."}[lang]
        gold={"text":text}
    elif task=="message":
        facts["due_date"]=str(due)
        question=f"language={lang}; currency=BDT; open={money(amount)}; due_on={due}"
        text={"bn":f"আপনার কাছে {major} টাকা পাওনা আছে। অনুগ্রহ করে {due} তারিখের মধ্যে পরিশোধ করুন।",
              "bn-Latn":f"Apnar kache {major} taka paona ache. Doya kore {due} er moddhe porishodh korben.",
              "en":f"Your outstanding amount is BDT {major}. Please arrange payment by {due}."}[lang]
        gold={"text":text}
    else:
        horizon=rng.choice([7,30]); forecast=bool(rng.randrange(2))
        facts.update(horizon_days=horizon if forecast else None)
        base={"bn":"নগদ হিসাব দেখাও" if not forecast else f"{to_local(horizon)} দিনের নগদ পূর্বাভাস দেখাও",
              "bn-Latn":"Nogod hisab dekhao" if not forecast else f"{horizon} diner cash forecast dekhao",
              "en":"Show the cash snapshot" if not forecast else f"Project the next {horizon} days"}[lang]
        question=f"{base}. Reference date {day}, opening BDT {money(amount)}."
        gold={"tool":"project_cash_flow","arguments":{"horizon_days":horizon,"delay_inflows_days":0}} if forecast else {"tool":"get_business_snapshot","arguments":{}}

    return {"id":f"syn-{i+1:05}","task":task,"language":lang,"source":"synthetic","facts":facts,
            "messages":[{"role":"system","content":LEVEL[task]+" /no_think"},{"role":"user","content":question},
                        {"role":"assistant","content":encoded(gold)}]}


def numbers(text):
    normalized=text.translate(str.maketrans("০১২৩৪৫৬৭৮৯","0123456789"))
    return set(re.findall(r"[0-9]+(?:\.[0-9]+)?",normalized))


def verify(row):
    task,f=row["task"],row["facts"]
    answer=json.loads(row["messages"][-1]["content"])
    src=row["messages"][1]["content"]
    if task in ("extract_text","bill_text"):
        assert answer["amount"]==money(f["amount_minor"]) and answer["vendor"]==f["vendor"] and f["vendor"] in src
        assert answer["date"]==f["date"] and answer["currency"]==f["currency"]
        assert answer["amount"] in src.translate(str.maketrans("০১২৩৪৫৬৭৮৯","0123456789"))
        if task=="bill_text":
            assert f["amount_minor"]==f["net_minor"]+f["tax_minor"] and answer["due_date"]==f["due_date"]
        else:
            assert answer["direction"]==f["direction"] and answer["kind"]=="cash"
    elif task=="offer":
        assert answer==dict(principal_minor=f["amount_minor"],fee_minor=f["fee_minor"],term_days=f["term_days"],rate_bps=f["rate_bps"],currency=f["currency"])
    elif task in ("briefing","message"):
        assert numbers(answer["text"])<=numbers(src)
        assert (row["language"]=="bn")==bool(re.search("[\u0980-\u09ff]",answer["text"]))
    else:
        tool=answer["tool"]; args=TOOLS[tool].model_validate(answer["arguments"])
        ds=Dataset(opening_source_id="opening",opening_on=f["date"],as_of=f["date"],opening_balance_minor=f["amount_minor"],currency="BDT",events=[],invoices=[])
        if tool=="get_business_snapshot":
            assert snapshot(ds)["balance_minor"]==f["amount_minor"] and f["horizon_days"] is None
        else:
            assert tool=="project_cash_flow" and args.horizon_days==f["horizon_days"]
            result=project_cashflow(ds,ScenarioRequest(as_of=f["date"],reserve_minor=0,**args.model_dump()))
            assert result["closing_balance_minor"]==f["amount_minor"]
    return row


def build(out,count=3600,seed=SEED):
    if not 3000<=count<=6000 or count%10:
        raise ValueError("Use 3,000 to 6,000 examples divisible into exact 80/10/10 splits")
    rng=random.Random(seed)
    rows=[];seen=set()
    while len(rows)<count:
        row=verify(create(len(rows),rng))
        key=row["messages"][1]["content"]
        if key not in seen:
            rows.append(row);seen.add(key)
    rng.shuffle(rows)
    output={}
    manifest={"version":1,"seed":seed,"source":"synthetic","teacher":"deterministic project code",
              "license":"MIT","examples":count,"splits":{}}
    for name, subset in (("train",rows[:count*8//10]),("validation",rows[count*8//10:count*9//10]),("test",rows[count*9//10:])):
        payload="".join(encoded(r)+"\n" for r in subset).encode()
        output[name+".jsonl"]=payload
        manifest["splits"][name]={"examples":len(subset),"sha256":hashlib.sha256(payload).hexdigest()}
    output["manifest.json"]=(json.dumps(manifest,indent=2)+"\n").encode()
    output["LICENSE"]=Path(__file__).resolve().parents[2].joinpath("LICENSE").read_bytes()
    out.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(out,"w") as archive:
        for name,payload in sorted(output.items()):
            info=ZipInfo(name,(2026,11,21,0,0,0)); info.compress_type=ZIP_DEFLATED
            info.external_attr=0o644<<16
            archive.writestr(info,payload,compress_type=ZIP_DEFLATED,compresslevel=9)
    return {**manifest,"archive_sha256":hashlib.sha256(out.read_bytes()).hexdigest()}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True);parser.add_argument("--count",type=int,default=3600)
    options=parser.parse_args()
    print(json.dumps(build(options.output,options.count),indent=2))
