"""Full-split blind-spot report: the data it builds must be internally consistent."""

import pytest

from evaluation import fullsplit_report as fr


@pytest.fixture(scope="module")
def data():
    return fr.collect()


def test_collect_covers_every_deletion_case(data):
    assert len(data.full_cases) == 508  # 518 requests minus 10 NOT_DELETION
    assert len(data.sample_cases) == 32
    assert all(c.status in ("COMPLETE", "INCOMPLETE") for c in data.full_cases.values())


def test_state_label_vocabulary(data):
    labels = {r.label for r in data.full_records} | {r.label for r in data.sample_records}
    assert labels <= {"UNKNOWN", "ABSENT", "PRESENT", "EXPOSED", "CLEAN", "ACTIVE", "DISABLED",
                      "RISK", "BLOCKED", "RETAINED"}


def test_type_table_counts_add_up(data):
    table = fr.type_table(data)
    assert sum(row["full"] for row in table) == len(data.full_records)
    assert sum(row["sample"] for row in table) == len(data.sample_records)
    new = [row for row in table if row["new_in_full"]]
    assert all(row["sample"] == 0 and row["full"] > 0 for row in new)
    assert all(row["sample"] > 0 or row["full"] == 0 for row in table if not row["new_in_full"])


def test_assumed_compliant_breakdown_partitions_unknowns(data):
    ac = fr.assumed_compliant(data)
    unknown = [r for r in data.full_records if r.label == "UNKNOWN"]
    assert ac["total"] == len(unknown)
    assert ac["no_observation"] + ac["only_after_audit"] + ac["only_mentions"] == ac["total"]
    assert sum(sum(v.values()) for v in ac["by_prefix"].values()) == ac["total"]
    assert ac["later_violation"] <= ac["only_after_audit"] + ac["only_mentions"]
    assert len(ac["examples"]) <= 15
    for ex in ac["examples"]:
        assert ex["file_id"] and ex["shows"]


def test_unused_activity_counts(data):
    ua = fr.unused_activity(data)
    assert sum(t["total"] for t in ua["types"].values()) + ua["used_total"] == ua["all_events"]
    for t in ua["types"].values():
        assert t["touch_linked"] <= t["total"] * 5  # pairs may repeat across cases
        assert t["after_delete"] <= t["touch_pairs"]
    assert len(ua["examples"]) <= 10


def test_status_sanity_examples(data):
    ss = fr.status_sanity(data)
    assert ss["complete_with_later_violation"] >= len(ss["complete_examples"]) <= 5
    assert ss["ledger_only_incomplete"] >= len(ss["ledger_examples"]) <= 5
    for case_id in ss["complete_examples"]:
        assert data.full_cases[case_id].status == "COMPLETE"
    for case_id in ss["ledger_examples"]:
        assert data.full_cases[case_id].status == "INCOMPLETE"


def test_retention_rows_are_audit_or_legal_hold_only(data):
    rows = fr.retention_rows(data)
    assert all(r["resource_id"].startswith(("audit_record_", "legal_hold_")) for r in rows)
    assert all(r["max_retention"] is None or r["max_retention"] <= r["audit_at"] for r in rows)


def test_render_has_every_section_and_is_deterministic(data):
    md = fr.render(data)
    for n in range(1, 9):
        assert f"## {n}. " in md
    assert md == fr.render(data)
