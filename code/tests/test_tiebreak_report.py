"""Tie-break comparison harness (evaluation side)."""

import pytest

from evaluation import tiebreak_report as tb
from ghost import linker


@pytest.fixture(scope="module")
def rows():
    return tb.compare()


def test_one_row_per_strategy_with_every_metric(rows):
    assert [r["strategy"] for r in rows] == list(linker.TIEBREAKS)
    for r in rows:
        for key in ("tp", "fp", "fn", "step_f1", "status_acc", "ties_sample", "ties_sample_correct",
                    "ties_full", "steps_changed_vs_order", "cases_changed_vs_order"):
            assert key in r, key
        assert r["tp"] + r["fn"] == 111
        assert r["ties_sample_correct"] <= r["ties_sample"] <= r["ties_full"]


def test_order_is_its_own_baseline_and_skip_all_guesses_nothing(rows):
    by = {r["strategy"]: r for r in rows}
    assert by["ORDER"]["steps_changed_vs_order"] == 0 == by["ORDER"]["cases_changed_vs_order"]
    assert by["SKIP_ALL"]["ties_full"] == 0 == by["SKIP_ALL"]["ties_sample"]
    assert by["SKIP_MULTI"]["ties_full"] <= by["ORDER"]["ties_full"]


def test_choice_rule_prefers_best_f1_then_fewest_guesses():
    rows = [
        {"strategy": "A", "step_f1": 0.970, "ties_full": 50},
        {"strategy": "B", "step_f1": 0.968, "ties_full": 10},  # within 0.5 pp, fewer guesses
        {"strategy": "C", "step_f1": 0.950, "ties_full": 0},   # clearly worse
    ]
    assert tb.choose(rows)["strategy"] == "B"
    rows[1]["step_f1"] = 0.960  # now more than 0.5 pp behind A
    assert tb.choose(rows)["strategy"] == "A"
    rows[1].update(step_f1=0.970, ties_full=50)  # exact tie on both: keep the declared order
    assert tb.choose(rows)["strategy"] == "A"


def test_render_has_the_table_and_the_reasoning(rows):
    md = tb.render(rows, tb.choose(rows))
    assert "| strategy |" in md and "ORDER" in md and "SKIP_MULTI" in md
    assert "0.5 pp" in md and tb.choose(rows)["strategy"] in md
