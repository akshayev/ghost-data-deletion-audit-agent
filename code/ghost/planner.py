"""Build the steps_json for one case from its linked resources, treatments and states."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache

from ghost import policy, state
from ghost.linker import LinkedResource, Linker
from ghost.loaders import DeletionRequest
from ghost.policy import Treatment
from ghost.serialize import ALLOWED, sort_steps
from ghost.state import ResourceState

# treatment -> (finding op, remediation op, verification op)
OPS = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "DISABLE": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCK": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "RETAIN": (None, None, "VERIFY_RECORD_PRESENT"),
}


def violated(t: Treatment, s: ResourceState) -> bool:
    if s.unknown:
        return False
    if t.action == "DELETE":
        return s.present
    if t.action in ("REDACT", "ANONYMIZE"):
        return s.present and s.exposed
    if t.action == "DISABLE":
        return s.active
    if t.action == "BLOCK":
        return s.risk
    return False  # RETAIN


@dataclass(frozen=True)
class Decision:
    resource_id: str
    system_id: str
    treatment: Treatment
    state: ResourceState
    violation: bool
    citation: str | None = None  # evidence for a COMPLETE verification step
    citation_source: str | None = None  # decisive | coverage | coverage_any_system


@dataclass
class PlanResult:
    status: str
    steps: list[dict]
    decisions: list[Decision] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@cache
def _coverage_index():
    """system_id -> sorted [(captured_at, file_id)] of complete coverages in files that
    have at least one observation (empty snapshots like a 0-row inventory never prove)."""
    from ghost.parsers.registry import parse_results

    by_system: dict[str, list] = defaultdict(list)
    any_system: list = []
    for r in parse_results().values():
        if not r.observations:
            continue
        for c in r.coverages:
            if c.complete:
                by_system[c.system_id].append((c.captured_at, c.file_id))
                any_system.append((c.captured_at, c.file_id))
    return (
        {s: sorted(set(v)) for s, v in by_system.items()},
        sorted(set(any_system)),
    )


def _latest_before(entries: list, audit_at) -> str | None:
    i = bisect_right(entries, (audit_at, "￿"))
    return entries[i - 1][1] if i else None


def citation(system_id: str, s: ResourceState, audit_at) -> tuple[str | None, str | None]:
    if s.decisive_file:
        return s.decisive_file, "decisive"
    by_system, any_system = _coverage_index()
    if file_id := _latest_before(by_system.get(system_id, []), audit_at):
        return file_id, "coverage"
    if file_id := _latest_before(any_system, audit_at):
        return file_id, "coverage_any_system"
    return None, None


def _step(phase: str, op: str, d: Decision, evidence: str) -> dict:
    expected_phase, st = ALLOWED[op]
    assert expected_phase == phase
    return {
        "step_no": 0,
        "phase": phase,
        "system_id": d.system_id,
        "resource_id": d.resource_id,
        "operation": op,
        "state": st,
        "evidence_id": evidence,
    }


def decide(res: LinkedResource, request: DeletionRequest, lk: Linker) -> Decision:
    t = policy.treatment(res, request, lk)
    s = state.resource_state(res.observations, request.audit_at, t.action)
    return Decision(res.resource_id, res.system_id, t, s, violated(t, s))


def plan_case(
    request: DeletionRequest,
    linked: list[LinkedResource],
    lk: Linker,
    decisions: list[Decision] | None = None,
) -> PlanResult:
    """decisions: precomputed decide() results for `linked` (same order), else computed here."""
    if decisions is None:
        decisions = [decide(r, request, lk) for r in linked]
    flags: list[str] = []
    steps: list[dict] = []
    if any(d.violation for d in decisions):
        for d in decisions:
            finding, remediation, verification = OPS[d.treatment.action]
            if d.violation:
                assert d.state.decisive_file and finding and remediation
                steps.append(_step("FINDING", finding, d, d.state.decisive_file))
                steps.append(_step("REMEDIATION", remediation, d, ""))
            steps.append(_step("VERIFICATION", verification, d, ""))
        return PlanResult("INCOMPLETE", sort_steps(steps), decisions, flags)

    cited = []
    for d in decisions:
        file_id, source = citation(d.system_id, d.state, request.audit_at)
        if source == "coverage_any_system":
            flags.append(f"{d.resource_id}: cited {file_id} without same-system coverage")
        if file_id is None:
            flags.append(f"{d.resource_id}: no citable evidence; step omitted")
            continue
        d = Decision(**{**d.__dict__, "citation": file_id, "citation_source": source})
        cited.append(d)
        steps.append(_step("VERIFICATION", OPS[d.treatment.action][2], d, file_id))
    if not steps:
        return PlanResult("COMPLETE", [], cited, flags + ["no verifiable resource"])
    return PlanResult("COMPLETE", sort_steps(steps), cited, flags)
