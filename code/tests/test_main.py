import csv

import main as cli
from ghost import paths


def test_baseline_predicts_not_applicable():
    req = {"case_id": "case_33", "request_text": "Please delete my account."}
    assert cli.predict_case(req, cli.build_context(use_llm=False)) == ("NOT_APPLICABLE", [])


def test_default_output_paths():
    assert cli.DEFAULT_OUT["full"] == paths.OUTPUT
    assert cli.DEFAULT_OUT["sample"] == paths.EVAL_RUNS / "sample_output_pred.csv"


def test_run_split_covers_every_request():
    rows, _ = cli.run_split("sample", use_llm=False)
    assert len(rows) == 32
    rows, _ = cli.run_split("full", use_llm=False)
    with open(paths.DATASET / "deletion_requests.csv", encoding="utf-8", newline="") as f:
        expected = [r["case_id"] for r in csv.DictReader(f)]
    assert [r.case_id for r in rows] == expected
    assert all(r.audit_at for r in rows)


def test_main_writes_valid_csv(tmp_path):
    out = tmp_path / "pred.csv"
    assert cli.main(["--split", "sample", "--out", str(out), "--no-llm"]) == 0
    with open(out, encoding="utf-8", newline="") as f:
        assert len(list(csv.DictReader(f))) == 32
