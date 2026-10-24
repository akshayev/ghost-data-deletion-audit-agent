from evaluation.diagnostics import gold_resource_ids, parse_recall
from ghost.parsers.report import coverage_rows, render_report


def test_coverage_rows_one_per_manifest_file():
    rows = coverage_rows()
    assert len(rows) == 94
    assert rows[0]["file_id"] == "evidence_01"
    png = [r for r in rows if r["family"] == "image:vision"]
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


def test_link_metrics_counts_add_up():
    from evaluation.diagnostics import link_metrics

    m = link_metrics()
    assert m["tp"] + m["fn"] == 111
    assert len(m["cases"]) == 32
    assert sum(len(c["missing"]) for c in m["cases"].values()) == m["fn"]
    assert sum(len(c["extra"]) for c in m["cases"].values()) == m["fp"]
    assert set(m["channels"]) <= {"key", "catalog", "worker_window"}
    assert m["tie_breaks"] >= 0


def test_full_split_link_stats():
    from evaluation.diagnostics import full_split_stats

    order = full_split_stats("ORDER")
    assert order["cases"] == 518
    assert order["no_account"] == ["case_45", "case_53", "case_61", "case_63"]
    assert 3.0 < order["mean_resources"] < 4.2
    default = full_split_stats()  # SKIP_MULTI leaves 3-way account ties unassigned
    assert set(order["no_account"]) <= set(default["no_account"])
    assert default["mean_resources"] <= order["mean_resources"]


def test_exclusion_effect_counts_changed_assignments():
    from evaluation.diagnostics import exclusion_effect

    e = exclusion_effect()
    assert e["excluded"] == [f"case_{n}" for n in range(533, 543)]
    assert e["assignments_with"] >= e["assignments_without"] >= 0
    assert e["changed"] >= e["removed_from_excluded"] >= 0


def test_link_metrics_match_the_pipeline_linker_and_the_tiebreak_table():
    from evaluation import tiebreak_report
    from evaluation.diagnostics import link_metrics
    from ghost import linker

    m = link_metrics()
    row = next(r for r in tiebreak_report.compare() if r["strategy"] == linker.default_tiebreak())
    assert (m["tp"], m["fp"], m["fn"]) == (row["tp"], row["fp"], row["fn"])
