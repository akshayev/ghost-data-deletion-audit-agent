"""State + policy + planner, end to end on real sample cases."""

import pytest

from evaluation.main import GOLD, load_rows
from ghost import linker, loaders, planner, policy, state
from ghost.serialize import load_citable_ids, validate_row

REQUESTS = {r.case_id: r for r in loaders.load_requests("sample")}


@pytest.fixture(scope="module")
def results():
    lk = linker.build_linker()
    return {c: planner.plan_case(r, lk.link(c), lk) for c, r in REQUESTS.items()}


def steps_of(result, resource_id):
    return [
        (s["operation"], s["evidence_id"]) for s in result.steps if s["resource_id"] == resource_id
    ]


def decision(result, resource_id):
    (d,) = [d for d in result.decisions if d.resource_id == resource_id]
    return d


# ---------- policy / state units ----------


def test_rules_are_loaded_from_csv():
    assert policy.CATEGORY_ACTION["USER_PROFILE"] == "DELETE"
    assert policy.CATEGORY_ACTION["SUPPORT_MESSAGE"] == "REDACT"
    assert policy.CATEGORY_ACTION["ANALYTICS_PROFILE"] == "ANONYMIZE"
    assert policy.CATEGORY_ACTION["SECURITY_AUDIT_LOG"] == "RETAIN"
    assert policy.CATEGORY_ACTION["BACKUP_ARCHIVE"] == "BLOCK"


def test_prohibited_fields_redact_vs_anonymize():
    fields = {"display_name": "A", "customer_reference": "user_1", "lookup_key": "a@b.c"}
    assert policy.prohibited(fields, "REDACT") == ["display_name"]
    assert policy.prohibited(fields, "ANONYMIZE") == ["customer_reference", "display_name"]
    assert policy.prohibited({"owner_email": "x@y.z", "requester_phone": "123"}, "REDACT") == [
        "owner_email",
        "requester_phone",
    ]


def test_pure_mention_never_decides():
    from datetime import UTC, datetime

    from ghost.observation import Observation

    t = datetime(2026, 1, 1, tzinfo=UTC)
    mention = Observation("e", t, t, "backup_service", "", "backup_1", None, "image", "x",
                          attrs={"row_source": "activity_feed"})
    assert state.coerce_present(mention) is None
    returned = Observation("e", t, t, "account_service", "", "account_1", None, "log", "x",
                           attrs={"http_status": "200"})
    assert state.coerce_present(returned) is True


def test_reference_mention_never_decides_even_with_copied_fields():
    from datetime import UTC, datetime

    from ghost.observation import Observation

    t = datetime(2026, 1, 1, tzinfo=UTC)
    ref = Observation("e", t, t, "account_service", "", "account_6", None, "primary", "docs",
                      fields={"email": "a@b.co"},
                      attrs={"referenced_by": "search_6", "reference_field": "source_resource_id"})
    assert state.coerce_present(ref) is None


# ---------- spec cases ----------


def test_case_05_replica_drift(results):
    r = results["case_05"]
    assert r.status == "INCOMPLETE"
    assert steps_of(r, "account_005") == [
        ("REPORT_REMAINING_RECORD", "evidence_54"),
        ("DELETE_RECORD", ""),
        ("VERIFY_RECORD_ABSENT", ""),
    ]


def test_case_26_complete_ignores_later_evidence(results):
    r = results["case_26"]
    assert r.status == "COMPLETE"
    assert {s["evidence_id"] for s in r.steps} == {"evidence_92"}
    assert steps_of(r, "account_5684") == [("VERIFY_RECORD_ABSENT", "evidence_92")]


def test_case_26_with_oracle_links_has_all_six_steps_on_evidence_92():
    # analytics_4566 is a same-minute linker tie (assigned to case_25); with the gold
    # resource set the planner must produce all six verification steps from evidence_92.
    from evaluation.main import oracle_predictions

    status, steps = oracle_predictions()["case_26"]
    assert status == "COMPLETE" and len(steps) == 6
    assert {s["evidence_id"] for s in steps} == {"evidence_92"}


def test_case_24_status_200_with_email_is_remaining(results):
    steps = steps_of(results["case_24"], "account_5334")
    assert ("REPORT_REMAINING_RECORD", "evidence_92") in steps


def test_case_12_worker_delete_beats_analytics_default(results):
    r = results["case_12"]
    d = decision(r, "analytics_9914")
    assert d.treatment.action == "DELETE" and d.treatment.source == "worker"
    ops = [op for op, _ in steps_of(r, "analytics_9914")]
    assert "REPORT_REMAINING_RECORD" in ops and "REPORT_EXPOSED_PII" not in ops


def test_case_03_null_lookup_key_is_not_exposure(results):
    r = results["case_03"]
    assert r.status == "COMPLETE"
    assert steps_of(r, "analytics_9406") == [("VERIFY_RECORD_ANONYMIZED", "evidence_94")]


def test_case_31_coverage_citation_and_clean_tombstone(results):
    r = results["case_31"]
    assert r.status == "COMPLETE"
    assert steps_of(r, "account_4857") == [("VERIFY_RECORD_ABSENT", "evidence_51")]
    assert steps_of(r, "tombstone_440") == [("VERIFY_RECORD_REDACTED", "evidence_51")]


def test_case_32_tombstone_with_identifiers_is_exposed(results):
    ops = steps_of(results["case_32"], "tombstone_9178")
    assert ("REPORT_EXPOSED_PII", "evidence_51") in ops


def test_case_11_full_row_equals_gold(results):
    gold_status, gold_steps = load_rows(GOLD)["case_11"]
    r = results["case_11"]
    assert (r.status, r.steps) == (gold_status, gold_steps)


def test_case_04_known_link_miss_still_valid_complete(results):
    r = results["case_04"]
    assert "account_004" not in {s["resource_id"] for s in r.steps}
    assert r.status == "COMPLETE"


def test_every_sample_row_passes_the_validator(results):
    citable = load_citable_ids()
    for case_id, r in results.items():
        req = REQUESTS[case_id]
        assert validate_row(r.status, r.steps, req.audit_at_raw, citable) == [], case_id


def test_no_cited_evidence_after_audit_at(results):
    captured = {ef.file_id: ef.captured_at for ef in loaders.load_evidence_files()}
    for case_id, r in results.items():
        for s in r.steps:
            if s["evidence_id"]:
                assert captured[s["evidence_id"]] <= REQUESTS[case_id].audit_at, case_id


def _obs(present, copy, observed_hour, fields=None, attrs=None, file_id="evidence_1"):
    from datetime import UTC, datetime

    from ghost.observation import Observation

    cap = datetime(2026, 5, 1, 12, tzinfo=UTC)
    seen = datetime(2026, 5, 1, observed_hour, tzinfo=UTC)
    return Observation(file_id, cap, seen, "account_service", "", "account_1", present, copy,
                       "t", fields=fields or {}, attrs=attrs or {})


def test_replica_present_while_primary_absent_is_present():
    from datetime import UTC, datetime

    audit = datetime(2026, 6, 1, tzinfo=UTC)
    obs = (_obs(False, "primary", 9), _obs(True, "replica:customer_profiles_failover_replica", 10))
    s = state.resource_state(obs, audit, "DELETE")
    assert s.present and s.decisive_file == "evidence_1"


def test_query_key_alone_is_not_exposure():
    from datetime import UTC, datetime

    audit = datetime(2026, 6, 1, tzinfo=UTC)
    q = _obs(True, "log", 9, fields={"lookup_key": "a@b.co", "customer_reference": None},
             attrs={"http_status": "200"})
    assert not state.resource_state((q,), audit, "ANONYMIZE").exposed
    leaked = _obs(True, "log", 9, fields={"lookup_key": "a@b.co", "email": "a@b.co"})
    assert state.resource_state((leaked,), audit, "ANONYMIZE").exposed


def test_oracle_links_reproduce_every_sample_row_exactly():
    from evaluation.main import oracle_predictions

    gold = load_rows(GOLD)
    pred = oracle_predictions()
    wrong = [c for c in gold if pred[c] != gold[c]]
    assert wrong == []


def test_query_line_alone_is_not_a_returned_record():
    q = _obs(None, "log", 9, fields={"lookup_key": "a@b.co"}, attrs={"trace_id": "t1"})
    assert state.coerce_present(q) is None
