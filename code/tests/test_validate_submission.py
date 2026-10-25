"""The independent submission validator must catch each class of contract violation."""

import csv
import json

import pytest

from evaluation import validate_submission as vs
from ghost import paths

GOLDEN = paths.CODE / "evaluation" / "golden" / "output_full.csv"


def rows():
    with open(GOLDEN, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write(tmp_path, data, header=vs.HEADER):
    path = tmp_path / "out.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(data)
    return path


def failed(path) -> set[str]:
    return {name for name, ok, _ in vs.validate(path) if not ok}


def first(data, status):
    return next(r for r in data if r["deletion_status"] == status)


def edit_steps(row, fn):
    steps = json.loads(row["steps_json"])
    fn(steps)
    row["steps_json"] = json.dumps(steps)


def test_golden_output_passes_every_check(tmp_path):
    assert failed(write(tmp_path, rows())) == set()


def test_op_table_is_parsed_from_the_problem_statement():
    table = vs.allowed_operations(vs.statement_path())
    assert len(table) == 15 and ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE") in table


def test_shipped_table_copy_matches_the_problem_statement():
    real = paths.REPO_ROOT / "problem_statement.md"
    if not real.exists():
        pytest.skip("problem_statement.md is not shipped in code.zip")
    assert vs.allowed_operations(vs.STATEMENT_COPY) == vs.allowed_operations(real)


@pytest.mark.parametrize("mutate,check", [
    (lambda d: d.pop(), "exactly"),
    (lambda d: d.insert(0, d.pop()), "exactly"),
    (lambda d: first(d, "COMPLETE").update(deletion_status="DONE"), "statuses"),
    (lambda d: first(d, "INCOMPLETE").update(steps_json="{}"), "parses"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: s[0].pop("state")), "7 keys"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: s[0].update(step_no=7)), "step_no"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: s.reverse()), "phases"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: s[0].update(state="ABSENT")),
     "triples"),
    (lambda d: first(d, "NOT_APPLICABLE").update(steps_json=first(d, "COMPLETE")["steps_json"]),
     "if and only if"),
    (lambda d: edit_steps(first(d, "COMPLETE"), lambda s: s[0].update(evidence_id="")),
     "COMPLETE:"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: s[0].update(evidence_id="")),
     "INCOMPLETE:"),
    (lambda d: edit_steps(first(d, "COMPLETE"), lambda s: s[0].update(evidence_id="evidence_x")),
     "exists"),
    (lambda d: edit_steps(first(d, "INCOMPLETE"), lambda s: [x for x in s if x["phase"] ==
                          "VERIFICATION" and x.update(resource_id="zzz_1")]), "REMEDIATION has"),
])
def test_each_violation_is_caught(tmp_path, mutate, check):
    data = rows()
    mutate(data)
    assert any(check in name for name in failed(write(tmp_path, data))), check


def test_wrong_header_and_late_evidence_are_caught(tmp_path):
    data = rows()
    assert any("header" in n for n in failed(write(tmp_path, data,
                                                   ["case_id", "status", "steps_json"])))
    requests = {r["case_id"]: r for r in vs.read_csv(vs.DATASET / "deletion_requests.csv")}
    times = vs.evidence_times()
    row = first(data, "COMPLETE")
    late = next(e for e, t in sorted(times.items()) if t > vs.ts(requests[row["case_id"]]
                                                                   ["audit_at"]))
    edit_steps(row, lambda s: s[0].update(evidence_id=late))
    assert any("audit_at" in n for n in failed(write(tmp_path, data)))
