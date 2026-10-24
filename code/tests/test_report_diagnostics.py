from evaluation.diagnostics import gold_resource_ids, parse_recall
from ghost.parsers.report import coverage_rows, render_report


def test_coverage_rows_one_per_manifest_file():
    rows = coverage_rows()
    assert len(rows) == 94
    assert rows[0]["file_id"] == "evidence_01"
    png = [r for r in rows if r["family"] == "deferred:P4"]
    assert len(png) == 11
    assert all(r["unparsed"] == 0 for r in rows)


def test_render_report_has_table_and_totals():
    md = render_report(coverage_rows())
    assert "| evidence_54 |" in md
    assert "Total observations" in md


def test_gold_has_111_resources():
    assert len(gold_resource_ids()) == 111


def test_parse_recall_matches_verified_target():
    found, total, missing = parse_recall()
    assert (found, total) == (95, 111)
    assert missing == sorted(
        [
            "account_001",
            "account_002",
            "account_004",
            "account_007",
            "account_008",
            "account_010",
            "account_410",
            "account_4163",
            "account_4857",
            "account_4990",
            "account_5037",
            "account_5521",
            "account_7012",
            "account_8858",
            "audit_record_5569",
            "audit_record_6645",
        ]
    )
