"""Fit a linear-chain CRF on synthetic data, export sparse browser weights."""
import argparse
import hashlib
import json
import random
from pathlib import Path

import sklearn_crfsuite
from sklearn.model_selection import KFold

from .features import features, tokenize


def fit(X, y, c1, c2, iterations):
    return sklearn_crfsuite.CRF(algorithm="lbfgs", c1=c1, c2=c2, max_iterations=iterations,
                                all_possible_transitions=True).fit(X, y)


def train(source, output, max_cv, iterations, version):
    raw = source.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows)<25 or any(row["source"]!="synthetic" or tokenize(row["text"])!=row["tokens"]
                           or len(row["tokens"])!=len(row["labels"]) for row in rows):
        raise ValueError("Training expects at least 25 aligned synthetic examples")
    # A fixed sample bounds the cost of choosing hyperparameters; final fit uses every row.
    rng = random.Random(20261121)
    subset = rng.sample(rows, min(len(rows), max_cv))
    cand_X = [features(r["tokens"]) for r in subset]
    cand_y = [r["labels"] for r in subset]
    scores = []
    folds = list(KFold(5, shuffle=True, random_state=20261121).split(subset))
    for c1,c2 in ((0.05,0.1),(0.1,0.05),(0.1,0.2)):
        correct, total = 0, 0
        for train_ix,test_ix in folds:
            crf = fit([cand_X[i] for i in train_ix],[cand_y[i] for i in train_ix],c1,c2,iterations)
            for i in test_ix:
                pred = crf.predict_single(cand_X[i])
                for tag,gold in zip(pred,cand_y[i],strict=True):
                    correct += tag==gold
                    total += 1
        scores.append({"c1":c1,"c2":c2,"correct":correct,"total":total})
    best = max(scores,key=lambda s:s["correct"])
    X = [features(r["tokens"]) for r in rows]
    crf = fit(X,[r["labels"] for r in rows],best["c1"],best["c2"],iterations)
    weights = {}
    for (feature,label),weight in sorted(crf.state_features_.items()):
        if abs(weight)>=0.01:
            weights.setdefault(feature,{} )[label]=weight
    transition = {a:{b:w for (l,b),w in crf.transition_features_.items() if l==a and abs(w)>=0.01}
                  for a in crf.classes_}
    data = {"format":1,"version":version,"labels":list(crf.classes_),"weights":weights,"transitions":transition}
    blob = json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
    if len(blob)>5_000_000:
        raise ValueError("Pruned export exceeds 5 MB")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(blob)
    evidence = {"format":1, "model_sha256":hashlib.sha256(blob).hexdigest(), "model_bytes":len(blob),
                "training_sha256":hashlib.sha256(raw).hexdigest(),"training_examples":len(rows),
                "source":"synthetic","cv_examples":len(subset),"folds":5,"cv":scores,"selected":best,
                "iterations":iterations, "version":version}
    output.with_suffix(".report.json").write_text(json.dumps(evidence,indent=2)+"\n")
    return evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--cv-samples",type=int,default=1000)
    p.add_argument("--iterations",type=int,default=60)
    p.add_argument("--version",required=True)
    args=p.parse_args()
    if args.cv_samples<5 or args.iterations<1:
        p.error("Use at least five CV samples and one L-BFGS iteration")
    print(json.dumps(train(args.data,args.output,args.cv_samples,args.iterations,args.version)))


if __name__=="__main__":
    main()
