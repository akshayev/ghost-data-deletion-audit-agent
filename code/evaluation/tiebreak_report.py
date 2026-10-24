"""Compare the worker-window tie-break strategies by measurement (evaluation side).

    python code/evaluation/tiebreak_report.py [--write-report]

Each strategy runs the whole pipeline (classifier cache, linker, planner) on both splits.
The default is chosen by sample step F1; strategies within 0.5 pp of the best are ranked by
fewer guessed (tie-broken) assignments on the full split, for precision and explainability.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation.diagnostics import gold_by_case  # noqa: E402
from evaluation.main import GOLD, evaluate, load_rows  # noqa: E402
from ghost import classifier, linker, loaders, paths, planner  # noqa: E402

TOLERANCE = 0.005  # 0.5 percentage points of step F1
REPORT = paths.EVAL_RUNS / "tiebreak_report.md"


def _predictions(lk, requests, non_deletion) -> dict[str, tuple[str, list[dict]]]:
    out = {}
    for req in requests:
        if req.case_id in non_deletion:
            out[req.case_id] = ("NOT_APPLICABLE", [])
            continue
        plan = planner.plan_case(req, lk.link(req.case_id), lk)
        out[req.case_id] = (plan.status, plan.steps)
    return out


def _step_bag(steps: list[dict]) -> Counter:
    """Steps as a multiset; step_no is dropped because an inserted step renumbers the rest."""
    return Counter(tuple(v for k, v in s.items() if k != "step_no") for s in steps)


def _ties(lk, case_ids) -> list[tuple[str, str]]:
    """(case_id, resource_id) of every tie-broken worker-window assignment."""
    return [
        (c, rid)
        for c in case_ids
        for rid, vias in lk.worker_links.get(c, {}).items()
        if any(v[2] for v in vias)
    ]


def compare() -> list[dict]:
    non_deletion = classifier.non_deletion_cases(classifier.classify_requests(use_llm=False))
    sample, full = loaders.load_requests("sample"), loaders.load_requests("full")
    sample_ids, full_ids = [r.case_id for r in sample], [r.case_id for r in full]
    gold, gold_rows = gold_by_case(), load_rows(GOLD)
    rows, baseline = [], None
    for strategy in linker.TIEBREAKS:
        lk = linker.build_linker(non_deletion, strategy)
        tp = fp = fn = 0
        for case_id in sample_ids:
            got = {r.resource_id for r in lk.link(case_id)}
            tp, fp, fn = (tp + len(gold[case_id] & got), fp + len(got - gold[case_id]),
                          fn + len(gold[case_id] - got))
        m = evaluate(_predictions(lk, sample, non_deletion), gold_rows)
        sample_ties = _ties(lk, sample_ids)
        full_pred = _predictions(lk, full, non_deletion)
        if baseline is None:
            baseline = full_pred
        changed_steps = changed_cases = 0
        for case_id, (status, steps) in full_pred.items():
            mine, base = _step_bag(steps), _step_bag(baseline[case_id][1])
            diff = sum(((mine - base) + (base - mine)).values())
            diff += status != baseline[case_id][0]
            changed_steps += diff
            changed_cases += bool(diff)
        rows.append({
            "strategy": strategy, "tp": tp, "fp": fp, "fn": fn,
            "step_f1": m["step_full"]["f1"], "status_acc": m["status_accuracy"],
            "ties_sample": len(sample_ties),
            "ties_sample_correct": sum(rid in gold[c] for c, rid in sample_ties),
            "ties_full": len(_ties(lk, full_ids)),
            "steps_changed_vs_order": changed_steps, "cases_changed_vs_order": changed_cases,
        })
    return rows


def choose(rows: list[dict]) -> dict:
    best = max(r["step_f1"] for r in rows)
    near = [r for r in rows if best - r["step_f1"] <= TOLERANCE + 1e-12]
    return min(near, key=lambda r: r["ties_full"])  # min keeps the first of equals


def render(rows: list[dict], chosen: dict) -> str:
    L = ["| strategy | link tp/fp/fn | step F1 | status acc | ties (sample) | sample ties correct "
         "| ties (full) | steps changed vs ORDER (full) | cases changed |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(
            f"| {r['strategy']} | {r['tp']}/{r['fp']}/{r['fn']} | {100 * r['step_f1']:.1f}% "
            f"| {100 * r['status_acc']:.1f}% | {r['ties_sample']} | {r['ties_sample_correct']} "
            f"| {r['ties_full']} | {r['steps_changed_vs_order']} "
            f"| {r['cases_changed_vs_order']} |"
        )
    best = max(r["step_f1"] for r in rows)
    near = [r["strategy"] for r in rows if best - r["step_f1"] <= TOLERANCE + 1e-12]
    L += ["", f"Chosen default: **{chosen['strategy']}**. Rule: highest sample step F1; "
          f"strategies within 0.5 pp of the best ({', '.join(near)}) are ranked by fewer "
          "guessed (tie-broken) assignments on the full split."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args(argv)
    rows = compare()
    chosen = choose(rows)
    md = render(rows, chosen)
    print(md)
    if args.write_report:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
