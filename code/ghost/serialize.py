"""Deterministic output writing and the output-contract validator.

The ALLOWED table is copied from problem_statement.md "Allowed Operations".
Its row order is also the required ordering of operations within a phase.
"""

import csv
import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from ghost import paths

STEP_KEYS = (
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
)

STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

# operation -> (phase, state), in problem-statement table order.
ALLOWED = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
OP_ORDER = tuple(ALLOWED)

_PHASE_RANK = {p: i for i, p in enumerate(PHASES)}
_OP_RANK = {op: i for i, op in enumerate(OP_ORDER)}


class OutputRow(NamedTuple):
    case_id: str
    status: str
    steps: list
    audit_at: str


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _case_number(case_id: str) -> int:
    m = re.search(r"\d+", case_id)
    return int(m.group()) if m else -1


def load_citable_ids(dataset_dir: Path | None = None) -> dict[str, str]:
    """Map every citable id (file_id, activity_id, audit_id) to its timestamp."""
    root = Path(dataset_dir) if dataset_dir else paths.DATASET
    sources = (
        ("evidence_files.csv", "file_id", "captured_at"),
        ("system_activity.csv", "activity_id", "event_time"),
        ("user_audit_log.csv", "audit_id", "event_time"),
    )
    out: dict[str, str] = {}
    for name, id_col, time_col in sources:
        with open(root / name, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out[row[id_col]] = row[time_col]
    return out


def sort_steps(steps: Iterable[Mapping]) -> list[dict]:
    """Sort by (phase, operation table order, system_id, resource_id); renumber from 1."""
    ordered = sorted(
        steps,
        key=lambda s: (
            _PHASE_RANK[s["phase"]],
            _OP_RANK[s["operation"]],
            s["system_id"],
            s["resource_id"],
        ),
    )
    return [{**{k: s[k] for k in STEP_KEYS}, "step_no": i} for i, s in enumerate(ordered, 1)]


def to_json(steps: Iterable[Mapping]) -> str:
    """Compact deterministic JSON with keys in STEP_KEYS order."""
    return json.dumps(
        [{k: s[k] for k in STEP_KEYS} for s in steps],
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def validate_row(
    status: str,
    steps: list,
    audit_at: str,
    evidence_capture_times: Mapping[str, str],
) -> list[str]:
    """Return every contract violation for one output row (empty list = valid)."""
    if status not in STATUSES:
        return [f"unknown status {status!r}"]
    if not isinstance(steps, list):
        return ["steps must be a list"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have steps == []"]

    errors: list[str] = []
    cutoff = _ts(audit_at)
    last_phase = -1
    for i, s in enumerate(steps, 1):
        where = f"step {i}"
        if not isinstance(s, dict) or set(s) != set(STEP_KEYS):
            errors.append(f"{where}: keys must be exactly {STEP_KEYS}")
            continue
        if type(s["step_no"]) is not int or s["step_no"] != i:
            errors.append(f"{where}: step_no {s['step_no']!r} should be {i}")
        phase, op, state, ev = s["phase"], s["operation"], s["state"], s["evidence_id"]
        if phase not in _PHASE_RANK:
            errors.append(f"{where}: unknown phase {phase!r}")
            continue
        if _PHASE_RANK[phase] < last_phase:
            errors.append(f"{where}: phase {phase} out of order")
        last_phase = max(last_phase, _PHASE_RANK[phase])
        if ALLOWED.get(op) != (phase, state):
            errors.append(f"{where}: {phase}/{op}/{state} is not an allowed combination")
        if not isinstance(ev, str):
            errors.append(f"{where}: evidence_id must be a string")
            continue

        if status == "COMPLETE":
            if phase != "VERIFICATION":
                errors.append(f"{where}: COMPLETE allows only VERIFICATION steps")
            if not ev:
                errors.append(f"{where}: COMPLETE steps must cite evidence")
        else:  # INCOMPLETE
            if phase == "FINDING" and not ev:
                errors.append(f"{where}: FINDING must cite evidence")
            if phase != "FINDING" and ev:
                errors.append(f"{where}: {phase} in INCOMPLETE must have empty evidence_id")

        if ev:
            if ev not in evidence_capture_times:
                errors.append(f"{where}: unknown evidence_id {ev!r}")
            elif _ts(evidence_capture_times[ev]) > cutoff:
                errors.append(f"{where}: {ev} captured after audit_at {audit_at}")

    if status == "INCOMPLETE" and not any(
        isinstance(s, dict) and s.get("phase") == "FINDING" for s in steps
    ):
        errors.append("INCOMPLETE needs at least one FINDING")
    return errors


def write_output(
    rows: Iterable[OutputRow],
    path: Path | str,
    citable: Mapping[str, str] | None = None,
) -> None:
    """Validate every row, then write the submission CSV sorted by case number."""
    rows = list(rows)
    if citable is None:
        citable = load_citable_ids()

    problems: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if r.case_id in seen:
            problems.append(f"{r.case_id}: duplicate case_id")
        seen.add(r.case_id)
        errors = validate_row(r.status, r.steps, r.audit_at, citable)
        problems += [f"{r.case_id}: {e}" for e in errors]
    if problems:
        raise ValueError("invalid output rows:\n" + "\n".join(problems))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in sorted(rows, key=lambda r: _case_number(r.case_id)):
            w.writerow([r.case_id, r.status, to_json(r.steps)])
