import copy

import pytest

from evaluation.main import evaluate, load_rows, render_markdown
from ghost import paths


@pytest.fixture(scope="module")
def gold():
    return load_rows(paths.DATASET / "sample_output.csv")


def test_load_rows_reads_all_sample_cases(gold):
    assert len(gold) == 32
    status, steps = gold["case_11"]
    assert status == "INCOMPLETE"
    assert len(steps) == 14


def test_gold_against_itself_is_perfect(gold):
    m = evaluate(gold, gold)
    assert m["n_cases"] == 32
    assert m["status_accuracy"] == 1.0
    for key in ("step_full", "step_no_evidence"):
        assert m[key] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert m["alignment_rate"] == 1.0
    assert m["field_accuracy"] == {"state": 1.0, "evidence_id": 1.0}
    assert m["sequence_exact"] == 1.0
    assert m["sequence_lcs"] == 1.0
    assert all(not c["missing"] and not c["extra"] for c in m["cases"])
    assert sum(m["confusion"][g][p] for g in m["confusion"] for p in m["confusion"][g]) == 32


def test_dropped_step_hurts_recall_and_sequence(gold):
    pred = copy.deepcopy(gold)
    status, steps = pred["case_11"]
    dropped = steps.pop(0)
    m = evaluate(pred, gold)
    assert m["step_full"]["precision"] == 1.0
    assert m["step_full"]["recall"] < 1.0
    assert m["sequence_exact"] == pytest.approx(31 / 32)
    assert m["sequence_lcs"] < 1.0
    diff = next(c for c in m["cases"] if c["case_id"] == "case_11")
    assert diff["missing"] == [
        (
            dropped["phase"],
            dropped["system_id"],
            dropped["resource_id"],
            dropped["operation"],
            dropped["state"],
            dropped["evidence_id"],
        )
    ]
    assert diff["extra"] == []


def test_wrong_evidence_only_hurts_evidence_metrics(gold):
    pred = copy.deepcopy(gold)
    pred["case_11"][1][0]["evidence_id"] = "evidence_01"
    m = evaluate(pred, gold)
    assert m["step_full"]["f1"] < 1.0
    assert m["step_no_evidence"]["f1"] == 1.0
    assert m["field_accuracy"]["state"] == 1.0
    assert m["field_accuracy"]["evidence_id"] < 1.0
    assert m["alignment_rate"] == 1.0


def test_baseline_not_applicable_everywhere(gold):
    pred = {cid: ("NOT_APPLICABLE", []) for cid in gold}
    m = evaluate(pred, gold)
    assert m["status_accuracy"] == 0.0
    assert m["confusion"]["INCOMPLETE"]["NOT_APPLICABLE"] == 22
    assert m["confusion"]["COMPLETE"]["NOT_APPLICABLE"] == 10
    assert m["step_full"]["recall"] == 0.0
    assert m["alignment_rate"] == 0.0
    assert m["field_accuracy"] == {"state": None, "evidence_id": None}


def test_missing_prediction_counts_as_wrong(gold):
    pred = dict(gold)
    del pred["case_11"]
    m = evaluate(pred, gold)
    assert m["status_accuracy"] == pytest.approx(31 / 32)
    assert m["missing_predictions"] == ["case_11"]


def test_render_markdown_mentions_headline_metrics(gold):
    md = render_markdown(evaluate(gold, gold))
    assert "Status accuracy" in md
    assert "100.0%" in md
