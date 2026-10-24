"""Evaluation-only diagnostics (may read sample_output.csv; never imported by ghost).

    python code/evaluation/diagnostics.py

Parse recall = gold resource_ids in sample_output.csv that appear in at least
one parsed Observation (all evidence, PNG screenshots via the cached vision extraction).
Link recall/precision = per sample case, gold resource set vs the linker's set.
Writes code/evaluation/runs/link_report.md (learned windows + per-case diff).
"""

import csv
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from collections import Counter  # noqa: E402

from ghost import linker, loaders, paths  # noqa: E402
from ghost.parsers.registry import parse_all  # noqa: E402

GOLD = paths.DATASET / "sample_output.csv"
LINK_REPORT = paths.EVAL_RUNS / "link_report.md"


def gold_resource_ids() -> set[str]:
    with open(GOLD, encoding="utf-8", newline="") as f:
        return {
            s["resource_id"] for row in csv.DictReader(f) for s in json.loads(row["steps_json"])
        }


def parse_recall() -> tuple[int, int, list[str]]:
    gold = gold_resource_ids()
    seen = {o.resource_id for o in parse_all()[0]}
    missing = sorted(gold - seen)
    return len(gold) - len(missing), len(gold), missing


def gold_by_case() -> dict[str, set[str]]:
    with open(GOLD, encoding="utf-8", newline="") as f:
        return {
            row["case_id"]: {s["resource_id"] for s in json.loads(row["steps_json"])}
            for row in csv.DictReader(f)
        }


def link_metrics() -> dict:
    lk = linker.build_linker()
    cases, channels, tp, fp, fn = {}, Counter(), 0, 0, 0
    for case_id, gold in sorted(gold_by_case().items(), key=lambda kv: linker.case_num(kv[0])):
        got = {r.resource_id: r for r in lk.link(case_id)}
        for r in got.values():
            channels.update({v[0] for v in r.via})
        hit = gold & set(got)
        tp, fp, fn = tp + len(hit), fp + len(set(got) - gold), fn + len(gold - set(got))
        cases[case_id] = {"missing": sorted(gold - set(got)), "extra": sorted(set(got) - gold)}
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "cases": cases,
        "channels": dict(channels),
        "tie_breaks": lk.tie_breaks,
    }


def full_split_stats() -> dict:
    lk = linker.build_linker()
    ids = [r.case_id for r in loaders.load_requests("full")]
    linked = {c: [r.resource_id for r in lk.link(c)] for c in ids}
    accounts = {c: [r for r in rs if r.startswith("account_")] for c, rs in linked.items()}
    return {
        "cases": len(ids),
        "zero_linked": [c for c in ids if not linked[c]],
        "no_account": [c for c in ids if not accounts[c]],
        "multi_account": {c: a for c, a in accounts.items() if len(a) > 1},
        "mean_resources": sum(len(v) for v in linked.values()) / len(ids),
    }


def window_rows() -> list[tuple[str, float, float, str, int]]:
    lk = linker.build_linker()
    return sorted((t, lo, hi, src, n) for t, (lo, hi, src, n) in lk.windows.items())


def render_link_report(m: dict, s: dict) -> str:
    lines = ["# Link diagnostics", "", "## Learned worker windows (hours vs reported_at)", ""]
    lines += ["| template | lo | hi | source | samples |", "|---|---|---|---|---|"]
    for t, lo, hi, src, n in window_rows():
        lines.append(f"| {t} | {lo:+.2f} | {hi:+.2f} | {src} | {n} |")
    lines += ["", "## Sample split", "", f"tp={m['tp']} fp={m['fp']} fn={m['fn']}", ""]
    lines += [f"- channels: {m['channels']}  tie_breaks: {m['tie_breaks']}", ""]
    for case_id, d in m["cases"].items():
        if d["missing"] or d["extra"]:
            lines.append(f"- {case_id}: missing={d['missing']} extra={d['extra']}")
    lines += ["", "## Full split", ""]
    lines += [f"- {k}: {v}" for k, v in s.items()]
    return "\n".join(lines) + "\n"


def main() -> int:
    found, total, missing = parse_recall()
    print(f"parse recall: {found}/{total} gold resources appear in >=1 observation")
    print(f"missing ({len(missing)}): {', '.join(missing)}")
    print()
    print("learned worker windows (hours vs reported_at):")
    for t, lo, hi, src, n in window_rows():
        print(f"  [{lo:+.2f}, {hi:+.2f}] {src:<8} n={n:<4} {t}")
    m = link_metrics()
    print()
    print(f"link (sample): tp={m['tp']} fp={m['fp']} fn={m['fn']}")
    print(f"channels: {m['channels']}  tie_breaks: {m['tie_breaks']}")
    for case_id, d in m["cases"].items():
        if d["missing"] or d["extra"]:
            print(f"  {case_id}: missing={d['missing']} extra={d['extra']}")
    s = full_split_stats()
    print()
    print(f"full split: {s['cases']} cases, mean resources/case {s['mean_resources']:.2f}")
    print(f"  zero linked ({len(s['zero_linked'])}): {s['zero_linked']}")
    print(f"  no account_* ({len(s['no_account'])}): {s['no_account']}")
    print(f"  >1 account_* ({len(s['multi_account'])}): {s['multi_account']}")
    LINK_REPORT.parent.mkdir(parents=True, exist_ok=True)
    LINK_REPORT.write_text(render_link_report(m, s), encoding="utf-8")
    print(f"written: {LINK_REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
