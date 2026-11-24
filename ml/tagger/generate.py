"""Generate labelled, fully fictional training sentences from a fixed seed."""
import argparse
import json
import random
from pathlib import Path

from .features import tokenize

NAMES = {
    "en":["Lily Traders","Mango Shop","Nova Paper","River Goods","Niloy","Sumi","Azad","Karim","Rafi","Rupa"],
    "bn-Latn":["Shapla Store","Nila Bazar","Mita","Bijoy","Polash","Jamal","Neela","Tamal","Rina","Shuvo"],
    "bn":["শাপলা দোকান","নীল হাট","মিতা","বিজয়","পলাশ","জামাল","নীলা","তমাল","রিনা","শুভ"],
}
TERMS = {
    "en":{"out":["paid","gave","spent"], "in":["received","collected","earned"], "due":["owe","due"],"date":["today","yesterday","tomorrow","22 Nov"],
          "item":["rent","paper","rice","cartons"],"to":"to","from":"from","for":"for","unit":"taka","k":"thousand","l":"lakh"},
    "bn-Latn":{"out":["dilam","dilm","disi"],"in":["pelam","pailam","nilam"],"due":["baki","dite hobe"],"date":["aj","gotokal","kal","porshu"],
          "item":["chal","dal","bhara","kagoj"],"to":"ke","from":"theke","for":"babad","unit":"taka","k":"hajar","l":"lakh"},
    "bn":{"out":["দিলাম","দিয়েছি"],"in":["পেলাম","নিলাম"],"due":["বাকি","দিতে হবে"],"date":["আজ","গতকাল","কাল","পরশু","২২ তারিখ"],
          "item":["চাল","ডাল","ভাড়া","কাগজ"],"to":"কে","from":"থেকে","for":"বাবদ","unit":"টাকা","k":"হাজার","l":"লাখ"},
}


def samples(count, seed):
    rng, seen = random.Random(seed), set()
    while len(seen)<count:
        lang = ("bn","bn-Latn","en")[len(seen)%3]
        terms = TERMS[lang]
        flow = rng.choice(("out","in","due"))
        party = (rng.choice(NAMES[lang]),"PARTY")
        date = (rng.choice(terms["date"]),"DATE")
        verb = (rng.choice(terms[flow]),{"out":"DIR_OUT","in":"DIR_IN","due":"DUE"}[flow])
        item = (rng.choice(terms["item"]),"ITEM")
        marker = (terms["from"] if flow=="in" else terms["to"],"O")
        scaled = rng.randrange(11)
        number = lambda n:str(n).translate(str.maketrans("0123456789","০১২৩৪৫৬৭৮৯")) if lang=="bn" and rng.random()<0.75 else str(n)
        n = rng.randint(1,99)
        if scaled == 0:
            parts = [(number(n),"AMOUNT"),(terms["l"],"MULT"),(number(rng.randint(1,99)),"AMOUNT"),(terms["k"],"MULT")]
        elif scaled == 1:
            parts = [(number(n),"AMOUNT"),(terms["k"],"MULT"),(number(rng.randint(1,999)),"AMOUNT")]
        elif scaled == 2 and lang!="bn":
            parts = [(number(n)+"k","AMOUNT")]
        elif scaled == 3:
            words = {"en":["one","two","five","ten"],"bn":["এক","দুই","পাঁচ","দশ"],"bn-Latn":["ek","dui","pach","dosh"]}
            parts = [(rng.choice(words[lang]),"AMOUNT"),(terms["k"],"MULT")]
        elif scaled == 5:
            decimal=number(n)+"."+number(rng.randint(10,99))
            parts=[(decimal+("k" if lang!="bn" and rng.randrange(2) else ""),"AMOUNT")]
        elif scaled == 6:
            parts=[(number(rng.randint(1,99))+","+number(rng.randint(100,999)),"AMOUNT")]
        elif scaled == 4:
            parts = [(number(n),"AMOUNT"),("কোটি" if lang=="bn" else "crore","MULT")]
        else:
            parts = [(number(rng.randint(1,99999)),"AMOUNT")]
        amount = [*parts,(terms["unit"],"O")]
        if lang=="en":
            order = rng.choice(([date,verb,*amount,marker,party],[verb,party,*amount,date],
                               [verb,*amount,marker,party,(terms["for"],"O"),item,date],
                               [("I","O"),verb,*amount,date],[verb,*amount,("on","O"),item,date]))
        else:
            order = rng.choice(([date,party,marker,*amount,verb],[party,marker,item,(terms["for"],"O"),*amount,verb,date],
                               [date,*amount,party,marker,verb],[(("ami" if lang=="bn-Latn" else "আমি"),"O"),*amount,verb]))
        if lang=="bn" and party in order and marker[0]=="কে" and rng.randrange(2):
            idx=order.index(party)
            if idx+1<len(order) and order[idx+1]==marker:
                order[idx:idx+2]=[(party[0]+"কে","PARTY")]
        text = " ".join(word for word,_ in order)
        if text in seen:
            continue
        seen.add(text)
        tokens, labels = [], []
        for word,label in order:
            span = tokenize(word)
            tokens.extend(span)
            labels.extend([label]*len(span))
        yield dict(text=text, tokens=tokens, labels=labels, source="synthetic", language=lang)


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--count",type=int,default=30000)
    args.add_argument("--seed",type=int,default=20261121)
    args.add_argument("--output",type=Path,required=True)
    opts = args.parse_args()
    if not 1 <= opts.count <= 1000000:
        args.error("count must be between 1 and 1000000")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    with opts.output.open("w",encoding="utf-8") as output:
        for row in samples(opts.count,opts.seed):
            print(json.dumps(row, ensure_ascii=False,separators=(",",":")),file=output)


if __name__=="__main__":
    main()
