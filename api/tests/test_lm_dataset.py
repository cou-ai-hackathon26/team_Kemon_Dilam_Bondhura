"""Regression checks for verified synthetic training inputs and model evaluation."""
from pathlib import Path

from ml.hisab_lm.build_dataset import build
from ml.hisab_lm.eval import evaluate, parse_predictions, read_eval_split
from ml.hisab_lm.train import split_rows


def test_published_corpus_reproducible_and_disjoint(tmp_path):
    public=Path(__file__).resolve().parents[2]/"web/public/datasets/hisab-lm-synthetic-v1.zip"
    rebuilt=tmp_path/"data.zip"
    evidence=build(rebuilt)
    assert rebuilt.read_bytes()==public.read_bytes()
    splits=split_rows(rebuilt);splits["test"]=read_eval_split(rebuilt)
    assert read_eval_split(rebuilt,"validation")==splits["validation"]
    assert [len(splits[k]) for k in ("train","validation","test")]==[2880,360,360]
    queries=[r["messages"][1]["content"] for rows in splits.values() for r in rows]
    assert len(set(queries))==evidence["examples"]==3600


def test_scorer_counts_numbers_in_invalid_answers():
    archive=Path(__file__).resolve().parents[2]/"web/public/datasets/hisab-lm-synthetic-v1.zip"
    cases=read_eval_split(archive)
    fixture=[{"id":c["id"],"text":c["messages"][-1]["content"]} for c in cases]
    selfcheck=evaluate(cases,parse_predictions(fixture,cases))
    assert selfcheck["schema_valid_pct"]==100 and selfcheck["extraction_field_pct"]==100
    assert selfcheck["invented_number_count"]==0 and selfcheck["latency_measurements"]==0
    fixture[0]["text"]="Extra invented total 9999999999999"
    failed=evaluate(cases,parse_predictions(fixture,cases))
    assert failed["invented_number_count"]==1 and failed["schema_valid_pct"]<100
    assert fixture[0]["id"] in failed["miss_ids"]
