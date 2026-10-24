"""Evaluation harness: compare predictions with dataset/sample_output.csv.

    python code/evaluation/main.py               # predict the sample split, then score it
    python code/evaluation/main.py --pred PATH   # score an existing prediction CSV
    python code/evaluation/main.py --custom      # also run custom_cases/ (not built yet)
    python code/evaluation/main.py --oracle-links  # gold resource ids instead of the linker

Sample answers are read only here; nothing in code/ghost imports from this module.
Writes code/evaluation/latest_metrics.md, appends a row to evaluation_report.md and prints
a short summary.
"""

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ghost import paths  # noqa: E402
from ghost.serialize import STATUSES, write_output  # noqa: E402

GOLD = paths.DATASET / "sample_output.csv"
METRICS_MD = paths.EVALUATION / "latest_metrics.md"
SAMPLE_PRED = paths.EVAL_RUNS / "sample_output_pred.csv"
EVAL_REPORT = paths.EVALUATION / "evaluation_report.md"

FULL_FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")
ALIGN_FIELDS = ("phase", "system_id", "resource_id", "operation")

Rows = dict[str, tuple[str, list[dict]]]


def load_rows(path: Path | str) -> Rows:
    """Read an output-format CSV into {case_id: (status, steps)}."""
    with open(path, encoding="utf-8", newline="") as f:
        return {
            r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
            for r in csv.DictReader(f)
        }


def _tuples(steps: list[dict], fields: tuple[str, ...]) -> list[tuple]:
    return [tuple(s[k] for k in fields) for s in steps]


def _prf(tp: int, n_pred: int, n_gold: int) -> dict[str, float]:
    precision = tp / n_pred if n_pred else (1.0 if n_gold == 0 else 0.0)
    recall = tp / n_gold if n_gold else (1.0 if n_pred == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _lcs(a: list, b: list) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def _multiset_minus(a: list[tuple], b: list[tuple]) -> list[tuple]:
    """Items of a not covered by b (multiset), in a's order."""
    budget = Counter(b)
    out = []
    for x in a:
        if budget[x]:
            budget[x] -= 1
        else:
            out.append(x)
    return out


def failure_reasons(gold_steps: list[dict], pred_steps: list[dict]) -> list[tuple]:
    """(kind, step tuple, reason) for every missing/extra step. Reasons: link miss, link
    extra, treatment (different verification op), state (same verification, different
    findings/remediation), evidence (same operations, different evidence_id)."""
    g_full, p_full = _tuples(gold_steps, FULL_FIELDS), _tuples(pred_steps, FULL_FIELDS)
    g_res, p_res = {t[2] for t in g_full}, {t[2] for t in p_full}

    def ops(tuples, rid):
        return Counter(t[:5] for t in tuples if t[2] == rid)

    def verifications(tuples, rid):
        return {t[3] for t in tuples if t[2] == rid and t[0] == "VERIFICATION"}

    out = []
    for kind, steps in (("missing", _multiset_minus(g_full, p_full)),
                        ("extra", _multiset_minus(p_full, g_full))):
        for t in steps:
            rid = t[2]
            if rid not in p_res:
                why = "link miss"
            elif rid not in g_res:
                why = "link extra"
            elif ops(g_full, rid) == ops(p_full, rid):
                why = "evidence"
            elif verifications(g_full, rid) != verifications(p_full, rid):
                why = "treatment"
            else:
                why = "state"
            out.append((kind, t, why))
    return out


def evaluate(pred: Rows, gold: Rows) -> dict:
    confusion = {g: {p: 0 for p in STATUSES} for g in STATUSES}
    correct_status = 0
    missing_predictions = []
    tp = {"full": 0, "no_ev": 0}
    n_pred = n_gold = 0
    aligned = state_ok = ev_ok = 0
    exact = 0
    lcs_sum = 0.0
    cases = []

    for case_id in sorted(gold, key=lambda c: int("".join(ch for ch in c if ch.isdigit()) or -1)):
        g_status, g_steps = gold[case_id]
        if case_id in pred:
            p_status, p_steps = pred[case_id]
            confusion[g_status][p_status] += 1
        else:
            p_status, p_steps = None, []
            missing_predictions.append(case_id)
        correct_status += p_status == g_status

        g_full, p_full = _tuples(g_steps, FULL_FIELDS), _tuples(p_steps, FULL_FIELDS)
        g_noev, p_noev = [t[:-1] for t in g_full], [t[:-1] for t in p_full]
        tp["full"] += sum((Counter(g_full) & Counter(p_full)).values())
        tp["no_ev"] += sum((Counter(g_noev) & Counter(p_noev)).values())
        n_gold += len(g_full)
        n_pred += len(p_full)

        # field-level: pair steps sharing the alignment key, in order of appearance
        p_by_key: dict[tuple, list[dict]] = defaultdict(list)
        for s in p_steps:
            p_by_key[tuple(s[k] for k in ALIGN_FIELDS)].append(s)
        for s in g_steps:
            bucket = p_by_key[tuple(s[k] for k in ALIGN_FIELDS)]
            if bucket:
                match = bucket.pop(0)
                aligned += 1
                state_ok += match["state"] == s["state"]
                ev_ok += match["evidence_id"] == s["evidence_id"]

        exact += p_full == g_full
        longest = max(len(g_full), len(p_full))
        lcs_sum += _lcs(p_full, g_full) / longest if longest else 1.0

        cases.append(
            {
                "case_id": case_id,
                "gold_status": g_status,
                "pred_status": p_status,
                "missing": _multiset_minus(g_full, p_full),
                "extra": _multiset_minus(p_full, g_full),
                "reasons": failure_reasons(g_steps, p_steps),
            }
        )

    n = len(gold)
    return {
        "n_cases": n,
        "status_accuracy": correct_status / n if n else 1.0,
        "confusion": confusion,
        "missing_predictions": missing_predictions,
        "step_full": _prf(tp["full"], n_pred, n_gold),
        "step_no_evidence": _prf(tp["no_ev"], n_pred, n_gold),
        "steps_gold": n_gold,
        "steps_pred": n_pred,
        "alignment_rate": aligned / n_gold if n_gold else 1.0,
        "field_accuracy": {
            "state": state_ok / aligned if aligned else None,
            "evidence_id": ev_ok / aligned if aligned else None,
        },
        "sequence_exact": exact / n if n else 1.0,
        "sequence_lcs": lcs_sum / n if n else 1.0,
        "cases": cases,
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render_markdown(m: dict) -> str:
    sf, se, fa = m["step_full"], m["step_no_evidence"], m["field_accuracy"]
    lines = [
        "# Latest evaluation metrics (sample split)",
        "",
        f"Cases: {m['n_cases']} | gold steps: {m['steps_gold']} | "
        f"predicted steps: {m['steps_pred']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Status accuracy | {_pct(m['status_accuracy'])} |",
        f"| Step P / R / F1 (full tuple) | {_pct(sf['precision'])} / {_pct(sf['recall'])} / "
        f"{_pct(sf['f1'])} |",
        f"| Step P / R / F1 (no evidence_id) | {_pct(se['precision'])} / {_pct(se['recall'])} / "
        f"{_pct(se['f1'])} |",
        f"| Alignment rate (phase, system, resource, operation) | {_pct(m['alignment_rate'])} |",
        f"| Field accuracy: state | {_pct(fa['state'])} |",
        f"| Field accuracy: evidence_id | {_pct(fa['evidence_id'])} |",
        f"| Sequence exact match | {_pct(m['sequence_exact'])} |",
        f"| Sequence mean normalized LCS | {_pct(m['sequence_lcs'])} |",
        "",
        "## Status confusion matrix (rows = gold, columns = predicted)",
        "",
        "| gold \\ pred | " + " | ".join(STATUSES) + " |",
        "|---" * (len(STATUSES) + 1) + "|",
    ]
    for g in STATUSES:
        lines.append(f"| {g} | " + " | ".join(str(m["confusion"][g][p]) for p in STATUSES) + " |")
    if m["missing_predictions"]:
        lines += ["", "Missing predictions: " + ", ".join(m["missing_predictions"])]

    lines += ["", "## Per-case diff", ""]
    wrong = [
        c
        for c in m["cases"]
        if c["missing"] or c["extra"] or c["gold_status"] != c["pred_status"]
    ]
    if not wrong:
        lines.append("All cases match exactly.")
    for c in wrong:
        lines.append(f"### {c['case_id']}: gold {c['gold_status']}, predicted {c['pred_status']}")
        for kind, t, why in c.get("reasons", []):
            lines.append(f"- {kind} ({why}): `{' | '.join(t)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


HISTORY_HEADER = (
    "| date (UTC) | mode | cases | status acc | step F1 full | step F1 no-evidence | "
    "state acc | evidence acc | seq exact | mean LCS |\n"
    "|---|---|---|---|---|---|---|---|---|---|"
)
REPORT_TEMPLATE = f"""# Evaluation report

## Metrics history

{HISTORY_HEADER}

## Failures found & fixes

## Custom cases

(placeholder: custom cases are added in a later step)
"""


def append_history(m: dict, mode: str, path: Path = EVAL_REPORT) -> None:
    """Add one dated metrics row to the history table (creating the report if needed)."""
    text = path.read_text(encoding="utf-8") if path.exists() else REPORT_TEMPLATE
    fa = m["field_accuracy"]
    row = (
        f"| {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} | {mode} | {m['n_cases']} | "
        f"{_pct(m['status_accuracy'])} | {_pct(m['step_full']['f1'])} | "
        f"{_pct(m['step_no_evidence']['f1'])} | {_pct(fa['state'])} | "
        f"{_pct(fa['evidence_id'])} | {_pct(m['sequence_exact'])} | {_pct(m['sequence_lcs'])} |"
    )
    head, sep, tail = text.partition("\n## Failures found & fixes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head.rstrip("\n") + "\n" + row + "\n" + sep + tail, encoding="utf-8")


def oracle_predictions() -> Rows:
    """Plan every sample case from its GOLD resource ids instead of the linker's, so that
    state/policy/planner errors show up without linker noise."""
    from ghost import linker, loaders, planner

    lk = linker.build_linker()
    requests = {r.case_id: r for r in loaders.load_requests("sample")}
    out: Rows = {}
    for case_id, (_, steps) in load_rows(GOLD).items():
        resources = [lk.resource(rid) for rid in sorted({s["resource_id"] for s in steps})]
        result = planner.plan_case(requests[case_id], resources, lk)
        out[case_id] = (result.status, result.steps)
    return out


def _load_cli():
    """Import code/main.py under a distinct name (this file is also called main.py)."""
    spec = importlib.util.spec_from_file_location("ghost_cli", CODE_DIR / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score predictions against sample_output.csv")
    parser.add_argument("--pred", type=Path, help="prediction CSV (default: predict sample split)")
    parser.add_argument("--custom", action="store_true", help="also run custom_cases/")
    parser.add_argument("--no-llm", action="store_true", help="predict without LLM calls")
    parser.add_argument("--out", type=Path, default=METRICS_MD)
    parser.add_argument("--oracle-links", action="store_true",
                        help="plan from gold resource ids (separates linker from state/policy)")
    parser.add_argument("--history", type=Path, default=EVAL_REPORT,
                        help="report whose history table gets a new row")
    args = parser.parse_args(argv)

    mode = "real"
    if args.pred:
        pred, mode = load_rows(args.pred), f"pred:{args.pred.name}"
    elif args.oracle_links:
        pred, mode = oracle_predictions(), "oracle-links"
    else:
        rows, context = _load_cli().run_split("sample", use_llm=not args.no_llm)
        write_output(rows, SAMPLE_PRED, context.citable)
        pred = {r.case_id: (r.status, r.steps) for r in rows}

    m = evaluate(pred, load_rows(GOLD))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(m), encoding="utf-8")
    append_history(m, mode, args.history)

    sf, se, fa = m["step_full"], m["step_no_evidence"], m["field_accuracy"]
    print(f"[{mode}] cases={m['n_cases']}  status_acc={_pct(m['status_accuracy'])}")
    for label, prf in (("full", sf), ("no-evidence", se)):
        print(
            f"step F1 {label}={_pct(prf['f1'])} "
            f"(P {_pct(prf['precision'])} R {_pct(prf['recall'])})"
        )
    print(
        f"alignment={_pct(m['alignment_rate'])}  state_acc={_pct(fa['state'])}  "
        f"evidence_acc={_pct(fa['evidence_id'])}"
    )
    print(f"sequence exact={_pct(m['sequence_exact'])}  mean LCS={_pct(m['sequence_lcs'])}")
    print(f"details: {args.out}")
    if args.custom:
        print("custom cases: no custom cases yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
