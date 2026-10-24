import csv
import json
import random

import pytest

from ghost import paths
from ghost.serialize import (
    ALLOWED,
    OP_ORDER,
    STEP_KEYS,
    OutputRow,
    load_citable_ids,
    sort_steps,
    to_json,
    validate_row,
    write_output,
)

AUDIT_AT = "2026-09-10T12:00:00Z"
TIMES = {
    "evidence_01": "2026-09-10T11:00:00Z",  # before audit_at
    "evidence_02": "2026-09-10T12:00:00Z",  # exactly audit_at (allowed)
    "evidence_99": "2026-09-11T00:00:00Z",  # after audit_at
    "activity_01": "2026-09-01T00:00:00Z",
    "audit_01": "2024-12-14T16:31:44Z",
}


def step(no, phase, op, state, system="account_service", resource="account_1", ev=""):
    return {
        "step_no": no,
        "phase": phase,
        "system_id": system,
        "resource_id": resource,
        "operation": op,
        "state": state,
        "evidence_id": ev,
    }


def incomplete_steps():
    return [
        step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", ev="evidence_01"),
        step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT"),
        step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ]


def complete_steps():
    return [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", ev="evidence_01")]


# ---------- real dataset fixtures ----------


def _sample_requests():
    with open(paths.DATASET / "sample_deletion_requests.csv", encoding="utf-8", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def _sample_gold():
    with open(paths.DATASET / "sample_output.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def citable():
    return load_citable_ids()


@pytest.fixture(scope="module")
def case_11():
    row = next(r for r in _sample_gold() if r["case_id"] == "case_11")
    return json.loads(row["steps_json"])


# ---------- constants ----------


def test_step_keys_order():
    assert STEP_KEYS == (
        "step_no",
        "phase",
        "system_id",
        "resource_id",
        "operation",
        "state",
        "evidence_id",
    )


def test_allowed_table_matches_problem_statement():
    assert len(ALLOWED) == 15
    assert ALLOWED["REPORT_RESTORE_RISK"] == ("FINDING", "ACTIVE")
    assert ALLOWED["BLOCK_RESTORE"] == ("REMEDIATION", "BLOCKED")
    assert ALLOWED["VERIFY_RECORD_PRESENT"] == ("VERIFICATION", "PRESENT")
    assert ALLOWED["ANONYMIZE_RECORD"] == ("REMEDIATION", "ANONYMIZED")


def test_op_order_is_table_order():
    assert OP_ORDER == (
        "REPORT_REMAINING_RECORD",
        "REPORT_EXPOSED_PII",
        "REPORT_ACTIVE_DATA_FLOW",
        "REPORT_RESTORE_RISK",
        "DISABLE_DATA_FLOW",
        "BLOCK_RESTORE",
        "DELETE_RECORD",
        "REDACT_RECORD",
        "ANONYMIZE_RECORD",
        "VERIFY_RECORD_ABSENT",
        "VERIFY_RECORD_PRESENT",
        "VERIFY_RECORD_REDACTED",
        "VERIFY_RECORD_ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED",
        "VERIFY_RESTORE_BLOCKED",
    )


# ---------- sort_steps ----------


@pytest.mark.parametrize("seed", range(5))
def test_sort_steps_restores_case_11_order(case_11, seed):
    shuffled = [dict(s) for s in case_11]
    random.Random(seed).shuffle(shuffled)
    for s in shuffled:
        s["step_no"] = 0
    assert sort_steps(shuffled) == case_11


def test_sort_steps_breaks_ties_by_system_then_resource():
    steps = [
        step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "search_service", "search_2"),
        step(2, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "search_service", "search_1"),
        step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", "account_service", "account_9"),
    ]
    got = sort_steps(steps)
    assert [(s["system_id"], s["resource_id"], s["step_no"]) for s in got] == [
        ("account_service", "account_9", 1),
        ("search_service", "search_1", 2),
        ("search_service", "search_2", 3),
    ]


# ---------- to_json ----------


def test_to_json_is_compact_ordered_and_unicode():
    s = step(
        1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", resource="账户_1", ev="evidence_01"
    )
    reordered = {k: s[k] for k in reversed(STEP_KEYS)}
    out = to_json([reordered])
    assert out == (
        '[{"step_no":1,"phase":"FINDING","system_id":"account_service","resource_id":"账户_1",'
        '"operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_01"}]'
    )
    assert to_json([]) == "[]"


# ---------- validate_row: real gold rows ----------


def test_case_11_gold_is_valid(case_11, citable):
    audit_at = _sample_requests()["case_11"]["audit_at"]
    assert validate_row("INCOMPLETE", case_11, audit_at, citable) == []


def test_every_sample_gold_row_is_valid(citable):
    reqs = _sample_requests()
    for row in _sample_gold():
        errs = validate_row(
            row["deletion_status"],
            json.loads(row["steps_json"]),
            reqs[row["case_id"]]["audit_at"],
            citable,
        )
        assert errs == [], (row["case_id"], errs)


# ---------- validate_row: one pass + one fail per rule ----------


def test_status_must_be_known():
    assert validate_row("COMPLETE", complete_steps(), AUDIT_AT, TIMES) == []
    assert validate_row("DONE", complete_steps(), AUDIT_AT, TIMES)


def test_not_applicable_requires_empty_list():
    assert validate_row("NOT_APPLICABLE", [], AUDIT_AT, TIMES) == []
    assert validate_row("NOT_APPLICABLE", complete_steps(), AUDIT_AT, TIMES)


def test_step_numbers_consecutive_from_one():
    steps = incomplete_steps()
    assert validate_row("INCOMPLETE", steps, AUDIT_AT, TIMES) == []
    steps[1]["step_no"] = 5
    assert validate_row("INCOMPLETE", steps, AUDIT_AT, TIMES)
    starts_at_zero = complete_steps()
    starts_at_zero[0]["step_no"] = 0
    assert validate_row("COMPLETE", starts_at_zero, AUDIT_AT, TIMES)


def test_phases_must_be_in_order():
    steps = incomplete_steps()
    steps[1], steps[2] = steps[2], steps[1]
    steps[1]["step_no"], steps[2]["step_no"] = 2, 3
    assert validate_row("INCOMPLETE", steps, AUDIT_AT, TIMES)


def test_operation_state_pair_must_be_allowed():
    wrong_state = incomplete_steps()
    wrong_state[1]["state"] = "REDACTED"
    assert validate_row("INCOMPLETE", wrong_state, AUDIT_AT, TIMES)
    wrong_phase = incomplete_steps()
    wrong_phase[1]["phase"] = "VERIFICATION"
    wrong_phase[1]["operation"] = "DELETE_RECORD"
    assert validate_row("INCOMPLETE", wrong_phase, AUDIT_AT, TIMES)
    unknown_op = incomplete_steps()
    unknown_op[1]["operation"] = "SHRED_RECORD"
    assert validate_row("INCOMPLETE", unknown_op, AUDIT_AT, TIMES)


def test_step_must_have_exactly_the_step_keys():
    extra = complete_steps()
    extra[0]["note"] = "x"
    assert validate_row("COMPLETE", extra, AUDIT_AT, TIMES)
    missing = complete_steps()
    del missing[0]["system_id"]
    assert validate_row("COMPLETE", missing, AUDIT_AT, TIMES)


def test_complete_has_only_verification_steps():
    steps = incomplete_steps()
    steps[1]["evidence_id"] = steps[2]["evidence_id"] = "evidence_01"
    assert validate_row("COMPLETE", steps, AUDIT_AT, TIMES)


def test_complete_steps_must_cite_evidence():
    steps = complete_steps()
    steps[0]["evidence_id"] = ""
    assert validate_row("COMPLETE", steps, AUDIT_AT, TIMES)


def test_incomplete_needs_a_finding():
    steps = incomplete_steps()[1:]
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    assert validate_row("INCOMPLETE", steps, AUDIT_AT, TIMES)


def test_incomplete_finding_must_cite_evidence():
    steps = incomplete_steps()
    steps[0]["evidence_id"] = ""
    assert validate_row("INCOMPLETE", steps, AUDIT_AT, TIMES)


def test_incomplete_remediation_and_verification_have_empty_evidence():
    rem = incomplete_steps()
    rem[1]["evidence_id"] = "evidence_01"
    assert validate_row("INCOMPLETE", rem, AUDIT_AT, TIMES)
    ver = incomplete_steps()
    ver[2]["evidence_id"] = "evidence_01"
    assert validate_row("INCOMPLETE", ver, AUDIT_AT, TIMES)


@pytest.mark.parametrize("ev", ["evidence_01", "activity_01", "audit_01"])
def test_known_file_activity_and_audit_ids_are_citable(ev):
    steps = complete_steps()
    steps[0]["evidence_id"] = ev
    assert validate_row("COMPLETE", steps, AUDIT_AT, TIMES) == []


def test_unknown_evidence_id_fails():
    steps = complete_steps()
    steps[0]["evidence_id"] = "evidence_404"
    assert validate_row("COMPLETE", steps, AUDIT_AT, TIMES)


def test_cited_file_must_be_captured_by_audit_at():
    at_cutoff = complete_steps()
    at_cutoff[0]["evidence_id"] = "evidence_02"
    assert validate_row("COMPLETE", at_cutoff, AUDIT_AT, TIMES) == []
    late = complete_steps()
    late[0]["evidence_id"] = "evidence_99"
    assert validate_row("COMPLETE", late, AUDIT_AT, TIMES)


def test_load_citable_ids_covers_files_activities_and_audits(citable):
    assert citable["evidence_01"] == "2026-09-15T11:59:39Z"
    assert "activity_01" in citable
    assert "audit_01" in citable


# ---------- write_output ----------


def test_write_output_sorts_numerically_and_round_trips(tmp_path):
    rows = [
        OutputRow("case_10", "NOT_APPLICABLE", [], AUDIT_AT),
        OutputRow("case_9", "COMPLETE", complete_steps(), AUDIT_AT),
        OutputRow("case_100", "INCOMPLETE", incomplete_steps(), AUDIT_AT),
    ]
    out = tmp_path / "out.csv"
    write_output(rows, out, TIMES)
    with open(out, encoding="utf-8", newline="") as f:
        header = f.readline().strip()
        f.seek(0)
        got = list(csv.DictReader(f))
    assert header == "case_id,deletion_status,steps_json"
    assert [r["case_id"] for r in got] == ["case_9", "case_10", "case_100"]
    assert json.loads(got[0]["steps_json"]) == complete_steps()
    assert got[1]["steps_json"] == "[]"


def test_write_output_raises_on_invalid_row(tmp_path):
    out = tmp_path / "out.csv"
    rows = [OutputRow("case_1", "NOT_APPLICABLE", complete_steps(), AUDIT_AT)]
    with pytest.raises(ValueError, match="case_1"):
        write_output(rows, out, TIMES)
    assert not out.exists()


def test_paths_resolve_inside_repo():
    assert (paths.DATASET / "deletion_requests.csv").is_file()
    assert paths.OUTPUT.parent == paths.REPO_ROOT
    assert paths.EVIDENCE.is_dir()
