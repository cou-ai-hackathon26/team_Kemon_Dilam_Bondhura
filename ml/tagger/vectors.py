"""Write 200 token and feature parity vectors, interning repeated feature lists."""
import json
from pathlib import Path
from .features import features, tokenize
from .generate import samples


def main():
    text = ["য় য\u09bc", "৫k 4 লাখ ২০ হাজার", "৩️⃣ Tk. ১৭৫.৪০", "📦 পেলাম Neela-Shop 99!",
            "porshu ২২ তারিখ", "দিয়েছি ২৩ টাকা", "Latin,বাংলা/2", "ষ\u09cdট"]
    text.extend(row["text"] for row in samples(192,113))
    rows, indexes, vectors = [], {}, []
    for sentence in text:
        tokens = tokenize(sentence)
        ids=[]
        for row in features(tokens):
            key=tuple(row)
            if key not in indexes:
                indexes[key]=len(rows)
                rows.append(row)
            ids.append(indexes[key])
        vectors.append([sentence,tokens,ids])
    output = json.dumps({"rows":rows,"vectors":vectors},ensure_ascii=False,separators=(",",":"))+"\n"
    Path(__file__).with_name("vectors.json").write_text(output, encoding="utf-8")

if __name__=="__main__":
    main()
