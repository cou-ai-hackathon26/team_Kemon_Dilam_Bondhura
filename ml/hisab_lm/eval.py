"""Score recorded responses or run a local Ollama candidate on synthetic test inputs."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import time
import urllib.request
from zipfile import ZipFile


def read_eval_split(archive,split="test"):
    if split not in ("validation","test"):
        raise ValueError("Select validation or test examples")
    with ZipFile(archive) as z:
        manifest=json.loads(z.read("manifest.json"))
        data=z.read(split+".jsonl")
        if hashlib.sha256(data).hexdigest()!=manifest["splits"][split]["sha256"]:
            raise ValueError(f"{split} split checksum mismatch")
    cases=[json.loads(line) for line in data.splitlines()]
    if not cases or any(r["source"]!="synthetic" for r in cases):
        raise ValueError("Public model evaluations require synthetic examples")
    return cases


def numeric_tokens(s):
    normalized=s.translate(str.maketrans("০১২৩৪৫৬৭৮৯","0123456789"))
    return {Decimal(n.replace(",","")) for n in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", normalized)}


def parse_predictions(rows, cases):
    by_id={}
    for r in rows:
        ref=r["id"]
        if ref in by_id or not isinstance(r["text"],str):
            raise ValueError("Duplicate ID or malformed prediction")
        latency=r.get("latency_ms")
        if latency is not None and (type(latency) not in (float,int) or not math.isfinite(latency) or latency<0):
            raise ValueError("Invalid latency")
        by_id[ref]=r
    if set(by_id)!={c["id"] for c in cases}:
        raise ValueError("Predictions must cover the selected test split exactly")
    return by_id


def same_schema(value,target):
    if type(value) is not type(target):
        return False
    if isinstance(target,dict):
        return value.keys()==target.keys() and all(same_schema(value[k],t) for k,t in target.items())
    return True


def evaluate(cases, predictions):
    counters=Counter();by_task={};times=[];missed=[]
    for case in cases:
        task=case["task"]
        stats=by_task.setdefault(task,Counter())
        pred=predictions[case["id"]]
        raw=pred["text"]
        gold=json.loads(case["messages"][-1]["content"])
        try:
            decoded=json.loads(raw)
            valid=same_schema(decoded,gold)
        except ValueError:
            valid=False;decoded=None
        # Gold contains only verified financial facts for these tasks. Even malformed
        # answers and extra text contribute to the invented-number count.
        invented=numeric_tokens(raw)-numeric_tokens(case["messages"][-1]["content"])
        counters["cases"]+=1; counters["schema_valid"]+=int(valid)
        counters["invented_number_count"]+=len(invented)
        stats["cases"]+=1;stats["schema_valid"]+=int(valid)
        stats["invented_number_count"]+=len(invented)
        if task in {"extract_text","bill_text","offer"}:
            totals=len(gold)
            hits=sum(1 for key,expected in gold.items() if isinstance(decoded,dict) and type(decoded.get(key)) is type(expected) and decoded[key]==expected)
            counters["extraction_fields"]+=totals;counters["correct_extraction_fields"]+=hits
            stats["fields"]+=totals;stats["correct"]+=hits
        if task=="agent_plan":
            stats["tool_and_args_correct"]+=int(decoded==gold)
        if task in {"briefing","message"}:
            stats["required_numbers_present"]+=int(numeric_tokens(case["messages"][-1]["content"])<=numeric_tokens(raw))
        if pred.get("latency_ms") is not None:times.append(pred["latency_ms"])
        if not valid or invented or (task not in {"briefing","message"} and decoded!=gold):missed.append(case["id"])
    times.sort()
    return {
        "samples":len(cases),"schema_valid_pct":100*counters["schema_valid"]/len(cases),
        "extraction_field_pct":100*counters["correct_extraction_fields"]/counters["extraction_fields"] if counters["extraction_fields"] else None,
        "invented_number_count":counters["invented_number_count"],"latency_measurements":len(times),
        "p95_latency_ms":times[math.ceil(len(times)*0.95)-1] if times else None,"by_task":by_task,"miss_ids":missed,
    }


def local_predictions(model,cases,destination):
    # Fixed loopback endpoint. Never transmit evaluation data to a hosted service.
    with destination.open("x",encoding="utf-8") as f:
        for case in cases:
            payload={"model":model,"messages":case["messages"][:2],"stream":False,"think":False,
                     "format":"json","options":{"temperature":0,"seed":20261121,"num_predict":300}}
            req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=json.dumps(payload).encode(),
                                       headers={"Content-Type":"application/json"})
            start=time.monotonic()
            with urllib.request.urlopen(req,timeout=120) as response:
                text=json.load(response)["message"]["content"]
            print(json.dumps({"id":case["id"],"text":text,"latency_ms":round(1000*(time.monotonic()-start),2)},ensure_ascii=False),file=f,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True);p.add_argument("--predictions",type=Path,required=True)
    p.add_argument("--ollama-model",help="Run a locally installed model and write new predictions at the supplied path")
    p.add_argument("--reference",type=Path,help="Predictions for the best approved provider, on the identical sample")
    p.add_argument("--split",choices=("validation","test"),default="test")
    p.add_argument("--limit",type=int);args=p.parse_args()
    cases=read_eval_split(args.archive,args.split)
    if args.limit is not None and not 1<=args.limit<=len(cases):p.error("Invalid sample limit")
    cases=cases[:args.limit]
    if args.ollama_model:local_predictions(args.ollama_model,cases,args.predictions)
    def read(path):
        return parse_predictions([json.loads(line) for line in path.read_bytes().splitlines()], cases)
    result=evaluate(cases,read(args.predictions))
    result["split"]=args.split
    result["prediction_sha256"]=hashlib.sha256(args.predictions.read_bytes()).hexdigest()
    result["archive_sha256"]=hashlib.sha256(args.archive.read_bytes()).hexdigest()
    result["comparison_gate"]="missing_reference"
    if args.reference:
        ref=evaluate(cases,read(args.reference))
        result["reference_extraction_field_pct"]=ref["extraction_field_pct"]
        result["reference_sha256"]=hashlib.sha256(args.reference.read_bytes()).hexdigest()
        task_checks={}
        for task,stats in result["by_task"].items():
            valid=stats["schema_valid"]/stats["cases"]>=0.99 and stats["invented_number_count"]==0
            if stats.get("fields"):
                reference=ref["by_task"][task]
                valid=valid and stats["correct"]/stats["fields"]>=reference["correct"]/reference["fields"]-0.05
            elif task=="agent_plan":
                valid=valid and stats["tool_and_args_correct"]==stats["cases"]
            else:
                valid=valid and stats["required_numbers_present"]==stats["cases"]
            task_checks[task]="passed" if valid else "failed"
        result["task_automatic_checks"]=task_checks
        ok=result["schema_valid_pct"]>=99 and not result["invented_number_count"] and result["extraction_field_pct"] is not None and ref["extraction_field_pct"] is not None and result["extraction_field_pct"]>=ref["extraction_field_pct"]-5
        result["comparison_gate"]="passed" if ok and "failed" not in task_checks.values() else "failed"
    print(json.dumps(result,indent=2))
    return 1 if result["comparison_gate"]=="failed" else 0

if __name__=="__main__":
    raise SystemExit(main())
