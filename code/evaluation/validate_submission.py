"""Independent validation of a submission file against the challenge contract.

    python code/evaluation/validate_submission.py [output.csv]

Written separately from ghost/serialize.py on purpose: the operation/phase/state table is
parsed from problem_statement.md ("Allowed Operations"), and the evidence ids and their times
are read directly from the dataset CSVs. (code.zip ships a verbatim copy of that section,
code/evaluation/allowed_operations.md, used when problem_statement.md is absent.)
Prints PASS/FAIL per check; exit code 1 if any fails.
"""

import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "dataset"
HEADER = ["case_id", "deletion_status", "steps_json"]
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


STATEMENT_COPY = Path(__file__).with_name("allowed_operations.md")  # ships in code.zip


def statement_path() -> Path:
    """problem_statement.md when present (the repository), else the verbatim shipped copy."""
    real = REPO / "problem_statement.md"
    return real if real.exists() else STATEMENT_COPY


def allowed_operations(statement: Path) -> list[tuple[str, str, str]]:
    """(phase, operation, state) rows of the Allowed Operations table, in table order."""
    text = statement.read_text(encoding="utf-8").split("## Allowed Operations", 1)[1]
    text = text.split("\n## ", 1)[0]
    return re.findall(r"^\| `([A-Z]+)` \| `([A-Z_]+)` \| `([A-Z]+)` \|", text, re.M)


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def evidence_times() -> dict[str, datetime]:
    out = {}
    for r in read_csv(DATASET / "evidence_files.csv"):
        out[r["file_id"]] = ts(r["captured_at"])
    for r in read_csv(DATASET / "system_activity.csv"):
        out[r["activity_id"]] = ts(r["event_time"])
    for r in read_csv(DATASET / "user_audit_log.csv"):
        out[r["audit_id"]] = ts(r["event_time"])
    return out


def validate(path: Path) -> list[tuple[str, bool, str]]:
    table = allowed_operations(statement_path())
    op_state = {op: (phase, state) for phase, op, state in table}
    op_rank = {op: i for i, (_, op, _) in enumerate(table)}
    requests = read_csv(DATASET / "deletion_requests.csv")
    audit_at = {r["case_id"]: ts(r["audit_at"]) for r in requests}
    times = evidence_times()

    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        # by position, so a wrong header is reported (below) instead of breaking every check
        rows = [dict(zip(HEADER, r, strict=False)) for r in reader]

    results: list[tuple[str, bool, str]] = []

    def check(name: str, problems: list[str]) -> None:
        results.append((name, not problems, "; ".join(problems[:5]) + (
            f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")))

    check("header is exactly case_id,deletion_status,steps_json",
          [] if header == HEADER else [f"got {header}"])
    ids = [r["case_id"] for r in rows]
    expected = [r["case_id"] for r in requests]
    dupes = [c for c, n in Counter(ids).items() if n > 1]
    check(f"exactly {len(expected)} rows, same set and order as deletion_requests.csv, no "
          "duplicates",
          ([f"{len(ids)} rows"] if len(ids) != len(expected) else [])
          + ([f"duplicates {dupes}"] if dupes else [])
          + ([] if ids == expected else ["case ids or order differ"]))
    check("statuses in {COMPLETE, INCOMPLETE, NOT_APPLICABLE}",
          [f"{r['case_id']}: {r.get('deletion_status')}" for r in rows
           if r.get("deletion_status") not in STATUSES])

    parsed, bad_json = {}, []
    for r in rows:
        try:
            steps = json.loads(r.get("steps_json") or "")
            if not isinstance(steps, list):
                raise ValueError("not a list")
            parsed[r["case_id"]] = steps
        except (ValueError, TypeError) as exc:
            bad_json.append(f"{r['case_id']}: {exc}")
    check("every steps_json parses to a JSON list", bad_json)
    steps_of = [(r["case_id"], r.get("deletion_status"), parsed.get(r["case_id"], []))
                for r in rows]

    keys = [f"{c}#{i}: {sorted(set(s) ^ STEP_KEYS) if isinstance(s, dict) else type(s).__name__}"
            for c, _, steps in steps_of for i, s in enumerate(steps, 1)
            if not isinstance(s, dict) or set(s) != STEP_KEYS]
    check("each step has exactly the 7 keys", keys)
    steps_of = [(c, st, [s for s in steps if isinstance(s, dict) and set(s) == STEP_KEYS])
                for c, st, steps in steps_of]

    check("step_no is 1..n consecutive",
          [c for c, _, steps in steps_of if [s["step_no"] for s in steps]
           != list(range(1, len(steps) + 1))])
    check("phases in order FINDING < REMEDIATION < VERIFICATION",
          [c for c, _, steps in steps_of
           if [PHASES.index(s["phase"]) if s["phase"] in PHASES else -1 for s in steps]
           != sorted(PHASES.index(s["phase"]) if s["phase"] in PHASES else -1 for s in steps)
           or any(s["phase"] not in PHASES for s in steps)])
    check("operation/phase/state triples come from the problem statement table",
          [f"{c}#{s['step_no']}: {s['phase']} {s['operation']} {s['state']}"
           for c, _, steps in steps_of for s in steps
           if op_state.get(s["operation"]) != (s["phase"], s["state"])])
    check("NOT_APPLICABLE if and only if steps_json is []",
          [c for c, st, steps in steps_of if (st == "NOT_APPLICABLE") != (steps == [])])
    check("COMPLETE: only VERIFICATION steps, each with a non-empty evidence_id",
          [c for c, st, steps in steps_of if st == "COMPLETE" and (
              not steps or any(s["phase"] != "VERIFICATION" or not s["evidence_id"]
                               for s in steps))])
    check("INCOMPLETE: >=1 FINDING, every FINDING cites evidence, REMEDIATION/VERIFICATION use ''",
          [c for c, st, steps in steps_of if st == "INCOMPLETE" and (
              not any(s["phase"] == "FINDING" for s in steps)
              or any(s["phase"] == "FINDING" and not s["evidence_id"] for s in steps)
              or any(s["phase"] != "FINDING" and s["evidence_id"] != "" for s in steps))])
    unknown, late = [], []
    for c, _, steps in steps_of:
        for s in steps:
            ev = s["evidence_id"]
            if not ev:
                continue
            if ev not in times:
                unknown.append(f"{c}: {ev}")
            elif times[ev] > audit_at[c]:
                late.append(f"{c}: {ev} at {times[ev].isoformat()} > audit_at")
    check("every evidence_id exists (file_id / activity_id / audit_id)", unknown)
    check("every cited evidence time <= the case's audit_at", late)

    def order_key(s):
        return (PHASES.index(s["phase"]), op_rank.get(s["operation"], 99), s["system_id"],
                s["resource_id"])
    check("within a phase: operation table order, then system_id, then resource_id",
          [c for c, _, steps in steps_of if [order_key(s) for s in steps]
           != sorted(order_key(s) for s in steps)])
    unmatched = []
    for c, _, steps in steps_of:
        verifies = {(s["resource_id"], s["state"]) for s in steps if s["phase"] == "VERIFICATION"}
        for s in steps:
            if s["phase"] == "REMEDIATION" and (s["resource_id"], s["state"]) not in verifies:
                unmatched.append(f"{c}: {s['operation']} {s['resource_id']}")
    check("every REMEDIATION has a VERIFICATION of the same resource and end state", unmatched)
    return results


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else REPO / "output.csv"
    results = validate(path)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    failed = sum(not ok for _, ok, _ in results)
    print(f"{len(results) - failed}/{len(results)} checks passed for {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
