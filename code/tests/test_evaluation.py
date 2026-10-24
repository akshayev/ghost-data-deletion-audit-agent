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


def _step(rid, op, ev="", phase="VERIFICATION", system="account_service", state="ABSENT"):
    return {"step_no": 1, "phase": phase, "system_id": system, "resource_id": rid,
            "operation": op, "state": state, "evidence_id": ev}


def test_failure_reasons_per_wrong_step():
    from evaluation.main import failure_reasons

    gold = [
        _step("account_1", "VERIFY_RECORD_ABSENT", "evidence_1"),
        _step("account_2", "VERIFY_RECORD_ABSENT", "evidence_1"),
        _step("search_3", "VERIFY_RECORD_ABSENT", "evidence_1", system="search_service"),
        _step("tombstone_4", "VERIFY_RECORD_REDACTED", "evidence_1", state="REDACTED"),
    ]
    pred = [
        _step("account_1", "VERIFY_RECORD_ABSENT", "evidence_9"),  # evidence
        _step("search_3", "VERIFY_RECORD_ABSENT", "evidence_1", system="search_service"),
        _step("tombstone_4", "VERIFY_RECORD_ABSENT", "evidence_1"),  # treatment
        _step("cache_5", "VERIFY_RECORD_ABSENT", "evidence_1", system="cache_service"),  # extra
    ]
    reasons = {(kind, t[2]): why for kind, t, why in failure_reasons(gold, pred)}
    assert reasons[("missing", "account_1")] == "evidence"
    assert reasons[("missing", "account_2")] == "link miss"
    assert reasons[("missing", "tombstone_4")] == "treatment"
    assert reasons[("extra", "cache_5")] == "link extra"


def test_failure_reason_state_when_finding_differs():
    from evaluation.main import failure_reasons

    gold = [
        _step("account_1", "REPORT_REMAINING_RECORD", "evidence_1", "FINDING", state="PRESENT"),
        _step("account_1", "DELETE_RECORD", "", "REMEDIATION"),
        _step("account_1", "VERIFY_RECORD_ABSENT", ""),
    ]
    pred = [_step("account_1", "VERIFY_RECORD_ABSENT", "evidence_1")]
    reasons = {why for _, _, why in failure_reasons(gold, pred)}
    assert reasons == {"state"}


def test_oracle_predictions_cover_every_sample_case():
    from evaluation.main import oracle_predictions

    pred = oracle_predictions()
    assert len(pred) == 32
    assert all(status in ("COMPLETE", "INCOMPLETE") for status, _ in pred.values())


def test_history_row_appends(tmp_path):
    from evaluation.main import append_history

    report = tmp_path / "evaluation_report.md"
    gold = load_rows(paths.DATASET / "sample_output.csv")
    m = evaluate(gold, gold)
    append_history(m, "real", report)
    append_history(m, "oracle-links", report)
    text = report.read_text()
    for section in ("## Metrics history", "## Failures found & fixes", "## Custom cases"):
        assert section in text
    assert text.count("| real |") == 1 and text.count("| oracle-links |") == 1
