"""Reproducible local extraction and assistant evaluations. See evals/README.md."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/fixtures/records.jsonl"
METRICS = {"amount_minor": 95, "direction": 95, "party": 85}
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")
LANGUAGES = {"en", "bn", "bn-Latn"}
TEAM = "team"
ASSISTANT_SUITES = ("heldout_eval", "heldout_eval_v2", "heldout_eval_v3")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def normalize(text):
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def read_rows(path):
    raw = path.read_bytes()
    rows = []
    for line_num, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            raise ValueError(f"Invalid JSON on line {line_num}") from None
        if not isinstance(row, dict):
            raise ValueError(f"Expected an object on line {line_num}")
        rows.append(row)
    if not rows:
        raise ValueError("Dataset is empty")
    return rows, digest(raw)


def records(path, source):
    rows, checksum = read_rows(path)
    seen_ids, seen_text = set(), set()
    fields = {"id","source","language","text","as_of","amount_minor","direction","party"}
    if source == TEAM:
        fields.add("contributor_ref")
    for index, row in enumerate(rows, start=1):
        if set(row) != fields or row["source"] != source or row["language"] not in LANGUAGES:
            raise ValueError(f"Unexpected schema, language or provenance on line {index}")
        if not isinstance(row["id"], str) or not TOKEN.fullmatch(row["id"]):
            raise ValueError(f"Invalid ID on line {index}")
        if source == TEAM and not (isinstance(row["contributor_ref"], str) and TOKEN.fullmatch(row["contributor_ref"])):
            raise ValueError(f"Expected a pseudonymous contributor reference on line {index}")
        if not isinstance(row["text"], str) or not 4 <= len(row["text"].strip()) <= 400:
            raise ValueError(f"Invalid sentence on line {index}")
        if (type(row["amount_minor"]) is not int or not 0 < row["amount_minor"] <= 10**12
                or row["direction"] not in ("inflow","outflow","due")
                or (row["party"] is not None and not (isinstance(row["party"], str) and row["party"].strip()))):
            raise ValueError(f"Invalid expected field on line {index}")
        try:
            if date.fromisoformat(row["as_of"]).isoformat() != row["as_of"]:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(f"Invalid reference date on line {index}") from None
        key = normalize(row["text"])
        if row["id"] in seen_ids or key in seen_text:
            raise ValueError(f"Duplicate ID or sentence on line {index}")
        seen_ids.add(row["id"])
        seen_text.add(key)
    return rows, checksum


def predictions(rows, expected):
    indexed = {}
    for row in rows:
        if set(row) != {"id", *METRICS} or row["id"] not in expected or row["id"] in indexed:
            raise ValueError("Predictions must contain exactly one result per evaluation ID")
        if (row["amount_minor"] is not None and type(row["amount_minor"]) is not int
                or row["direction"] not in (None, "inflow", "outflow", "due")
                or row["party"] is not None and not isinstance(row["party"], str)):
            raise ValueError("Invalid prediction fields")
        indexed[row["id"]] = row
    if set(indexed) != expected:
        raise ValueError("Missing predictions; supply null values for an abstention")
    return indexed


def score(cases, outputs):
    summary = {}
    for field in METRICS:
        correct = 0
        misses = []
        applicable = [c for c in cases if field != "party" or c[field] is not None]
        for case in applicable:
            actual = outputs[case["id"]][field]
            expected = case[field]
            match = (isinstance(actual,str) and normalize(actual) == normalize(expected)) if field == "party" else actual == expected
            if match:
                correct += 1
            else:
                misses.append(case["id"])
        summary[field] = {"correct": correct, "total": len(applicable), "miss_ids": misses}
    return summary


def leakage_check(cases, training_path):
    training_rows, checksum = read_rows(training_path)
    texts = set()
    for row in training_rows:
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError("Expected training examples with a text field")
        texts.add(normalize(row["text"]))
    if any(normalize(case["text"]) in texts for case in cases):
        raise ValueError("Training/evaluation sentence overlap")
    return checksum


def node(script, args=(), stdin=None):
    result = subprocess.run(["node", str(ROOT/script), *args], cwd=ROOT, input=stdin,
                            capture_output=True, encoding="utf-8", env={**os.environ, "TZ": "Asia/Dhaka"})
    if result.returncode:
        # Some legacy scripts print source sentences on failure. Keep them out of reports.
        raise ValueError(f"{script} failed (status {result.returncode}). Check the installed web dependencies and run the suite locally.")
    return json.loads(result.stdout)


def evaluate_records(args):
    source = TEAM if args.suite == "team" else "synthetic"
    cases, checksum = records(args.dataset or FIXTURE, source)
    train_sha = leakage_check(cases, args.training_data) if args.training_data else None
    ids = {case["id"] for case in cases}
    inputs = [{k:case[k] for k in ("id","text","as_of")} for case in cases]
    if args.baseline:
        baseline_rows, baseline_sha = read_rows(args.baseline)
    else:
        baseline_rows = node("evals/extract.cjs", stdin=json.dumps(inputs))
        baseline_sha = digest((ROOT/"web/src/recordParser.ts").read_bytes())
    baseline = score(cases, predictions(baseline_rows, ids))
    report = {
        "suite": args.suite, "source": source, "cases": len(cases), "dataset_sha256": checksum,
        "language_counts": dict(Counter(case["language"] for case in cases)),
        "baseline_source": "supplied_predictions" if args.baseline else "browser_record_parser",
        "baseline_sha256": baseline_sha, "baseline": baseline, "training_data_sha256": train_sha,
    }
    if args.predictions:
        model_rows, prediction_sha = read_rows(args.predictions)
        candidate = score(cases, predictions(model_rows, ids))
        report.update(candidate=candidate, predictions_sha256=prediction_sha)
    if source == TEAM:
        missing = []
        if len(cases) < 300:
            missing.append("300 independently written sentences")
        if not train_sha:
            missing.append("training/evaluation overlap check")
        if not args.predictions:
            missing.append("candidate predictions")
        if not baseline["party"]["total"]:
            missing.append("named-party examples")
        report["accuracy_gate"] = "insufficient_evidence"
        report["missing"] = missing
        if not missing:
            success = all(candidate[k]["correct"] * 100 >= METRICS[k] * candidate[k]["total"]
                          and candidate[k]["correct"] > baseline[k]["correct"] for k in METRICS)
            report["accuracy_gate"] = "passed" if success else "failed"
        return report, report["accuracy_gate"] == "passed"
    # This committed synthetic corpus is an engineering regression gate only.
    regression_passed = all(result["correct"] == result["total"] for result in baseline.values())
    return report, regression_passed if not args.dataset else True


def assistant():
    results = {}
    for name in ASSISTANT_SUITES:
        scored = node("scripts/evaluate_local_assistant.cjs", [name, "--check"])
        results[name] = {k:scored[k] for k in ("cases","corpus_sha256","overall","confidently_wrong_answers")}
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke","records","assistant","team"), required=True)
    parser.add_argument("--dataset", type=Path, help="Labelled extraction JSONL")
    parser.add_argument("--training-data", type=Path, help="JSONL containing the text of every training example")
    parser.add_argument("--predictions", type=Path, help="Candidate predictions JSONL keyed by case ID")
    parser.add_argument("--baseline", type=Path, help="Frozen rule-only baseline predictions JSONL")
    args = parser.parse_args(argv)
    if args.suite == "team" and not args.dataset:
        parser.error("team requires a dataset of independently authored fictional transactions")
    if args.suite in ("smoke","assistant") and any((args.dataset,args.training_data,args.predictions,args.baseline)):
        parser.error("use records or team with custom extraction inputs")
    try:
        commit = subprocess.run(["git","rev-parse","HEAD"], cwd=ROOT, capture_output=True, text=True)
        parser_sources = ["evals/run.py","evals/extract.cjs","web/src/recordParser.ts","web/package-lock.json"]
        report = {"git_sha": commit.stdout.strip() or None, "external_calls": 0,
                  "implementation_sha256": {s:digest((ROOT/s).read_bytes()) for s in parser_sources}}
        ok = True
        if args.suite != "assistant":
            report["extraction"], ok = evaluate_records(args)
        if args.suite in ("smoke","assistant"):
            report["assistant"] = assistant()
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if ok else 1
    except (ValueError, OSError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
