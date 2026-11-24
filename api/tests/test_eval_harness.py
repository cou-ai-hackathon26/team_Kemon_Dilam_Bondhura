"""Data provenance and accounting checks for the offline extraction evaluation."""
import json
from types import SimpleNamespace

import pytest

from evals.run import evaluate_records, leakage_check, predictions, records, score


def write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return path


def example():
    return {"id":"team-1","source":"team","contributor_ref":"p01","language":"bn-Latn",
            "text":"Nila ke 80 taka dilam","as_of":"2026-11-21","amount_minor":8000,"direction":"outflow","party":"Nila"}


def test_provenance_schema_and_normalized_training_overlap(tmp_path):
    row = example()
    path = write(tmp_path/"set", [row])
    assert records(path,"team")[0] == [row]
    with pytest.raises(ValueError, match="provenance"):
        records(path,"synthetic")
    with pytest.raises(ValueError, match="overlap"):
        leakage_check([row], write(tmp_path/"train",[{"text":row["text"].upper()+"  "}]))
    write(path, [row,dict(row,id="team-2",text=" "+row["text"]+" ")])
    with pytest.raises(ValueError, match="Duplicate"):
        records(path,"team")


def test_missing_abstention_and_party_denominator():
    cases = [example(), dict(example(), id="team-2",party=None)]
    ids = {c["id"] for c in cases}
    model = [{"id":c["id"],"party":None,"amount_minor":None,"direction":None} for c in cases]
    with pytest.raises(ValueError, match="Missing"):
        predictions(model[:1],ids)
    actual = score(cases,predictions(model,ids))
    assert actual["party"] == dict(total=1,correct=0,miss_ids=["team-1"])
    assert actual["amount_minor"]["total"] == 2
    with pytest.raises(ValueError, match="exactly one"):
        predictions(model+model[:1],ids)


def test_small_team_set_cannot_pass_even_with_perfect_predictions(tmp_path):
    row = example()
    test = write(tmp_path/"cases",[row])
    candidate = {k:row[k] for k in ("id","amount_minor","direction","party")}
    outputs = write(tmp_path/"predictions",[candidate])
    args = SimpleNamespace(suite="team", dataset=test, training_data=None, predictions=outputs, baseline=outputs)
    result, passed = evaluate_records(args)
    assert not passed and result["accuracy_gate"] == "insufficient_evidence"
    assert len(result["missing"]) == 2
    assert row["text"] not in json.dumps(result)
