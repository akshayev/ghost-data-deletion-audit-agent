"""Full-split blind-spot report (evaluation side; no logic changes, measurement only).

    python code/evaluation/fullsplit_report.py

Runs the real pipeline (cached LLM output, no network) on the full split and writes
code/evaluation/runs/fullsplit_report.md. No gold exists for the full split: the report looks
for patterns that never occur in the 32 samples and for decisions that rest on a rule (such as
"no capture <= audit_at means assumed compliant") that the samples cannot check.
"""

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ghost import linker, loaders, paths, planner, policy, state  # noqa: E402
from ghost.linker import LinkedResource  # noqa: E402
from ghost.loaders import Activity, DeletionRequest  # noqa: E402
from ghost.parsers.common import safe_ts  # noqa: E402

REPORT = paths.EVAL_RUNS / "fullsplit_report.md"
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
RARE_PREFIXES = ("marketing", "cache", "email_delivery", "legal_hold", "object")
RETAIN_PREFIXES = ("audit_record", "legal_hold")
CASE_WORKER_EVENTS = frozenset(
    {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "BLOCK_RESTORE", "DISABLE_DATA_FLOW"}
)
KEY_ATTRS = ("http_status", "status_code", "rows", "enabled", "restore_allowed", "eligible",
             "deletion_block", "result", "record_type", "retention_until", "retained_until",
             "matched_value", "account_status")


# ---------- data ----------


@dataclass
class Rec:
    case_id: str
    split: str
    request: DeletionRequest
    res: LinkedResource
    decision: planner.Decision
    label: str
    channels: str
    steps: list[dict]
    status: str

    @property
    def prefix(self) -> str:
        return self.res.resource_id.rpartition("_")[0]

    @property
    def action(self) -> str:
        return self.decision.treatment.action


@dataclass
class CaseInfo:
    request: DeletionRequest
    status: str
    steps: list[dict]
    records: list[Rec] = field(default_factory=list)


@dataclass
class Data:
    full_cases: dict[str, CaseInfo]
    sample_cases: dict[str, CaseInfo]
    ctx: object
    activities: tuple[Activity, ...]

    @property
    def full_records(self) -> list[Rec]:
        return [r for c in self.full_cases.values() for r in c.records]

    @property
    def sample_records(self) -> list[Rec]:
        return [r for c in self.sample_cases.values() for r in c.records]


def state_label(d: planner.Decision) -> str:
    s, action = d.state, d.treatment.action
    if s.unknown:
        return "UNKNOWN"
    if action == "DELETE":
        return "PRESENT" if s.present else "ABSENT"
    if action in ("REDACT", "ANONYMIZE"):
        return "ABSENT" if not s.present else ("EXPOSED" if s.exposed else "CLEAN")
    if action == "DISABLE":
        return "ACTIVE" if s.active else "DISABLED"
    if action == "BLOCK":
        return "RISK" if s.risk else "BLOCKED"
    return "RETAINED" if s.present else "ABSENT"


def collect() -> Data:
    from evaluation.main import _load_cli

    ctx = _load_cli().build_context(use_llm=False)
    lk = ctx.lk
    out: dict[str, dict[str, CaseInfo]] = {"sample": {}, "full": {}}
    for split in ("sample", "full"):
        for req in loaders.load_requests(split):
            if req.case_id in ctx.non_deletion:
                continue
            linked = lk.link(req.case_id)
            plan = planner.plan_case(req, linked, lk)
            info = CaseInfo(req, plan.status, plan.steps)
            for res in linked:
                d = planner.decide(res, req, lk)
                info.records.append(
                    Rec(
                        req.case_id, split, req, res, d, state_label(d),
                        "+".join(sorted({v[0] for v in res.via})),
                        [s for s in plan.steps if s["resource_id"] == res.resource_id],
                        plan.status,
                    )
                )
            out[split][req.case_id] = info
    return Data(out["full"], out["sample"], ctx, loaders.load_activities())


# ---------- helpers ----------


def case_num(case_id: str) -> int:
    return int(case_id.rsplit("_", 1)[1])


def later_state(rec: Rec) -> state.ResourceState | None:
    """State computed from ONLY the observations captured after audit_at (None if none)."""
    later = tuple(o for o in rec.res.observations if o.captured_at > rec.request.audit_at)
    if not later:
        return None
    s = state.resource_state(later, FAR_FUTURE, rec.action)
    return None if s.unknown else s


def later_violation(rec: Rec) -> state.ResourceState | None:
    s = later_state(rec)
    return s if s is not None and planner.violated(rec.decision.treatment, s) else None


def shows(s: state.ResourceState) -> str:
    parts = [n for n, on in (("present", s.present), ("exposed identifiers", s.exposed),
                             ("flow enabled", s.active), ("restore allowed", s.risk)) if on]
    return ", ".join(parts) or "not present"


def obs_line(o) -> str:
    fields = ",".join(k for k, v in o.fields.items() if v not in (None, ""))
    attrs = ",".join(f"{k}={o.attrs[k]}" for k in KEY_ATTRS if k in o.attrs)
    return (f"{o.file_id}@{o.captured_at:%Y-%m-%d %H:%M} {o.copy} present={o.present}"
            f" fields[{fields}] {attrs}").rstrip()


def step_line(s: dict) -> str:
    evidence = f"@{s['evidence_id']}" if s["evidence_id"] else ""
    return f"{s['phase'][:3]}:{s['operation']}{evidence}"


def parse_date(value) -> datetime | None:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return safe_ts(value if "T" in value else f"{value}T00:00:00Z", FAR_FUTURE)
    return None


# ---------- sections ----------


def type_table(data: Data) -> list[dict]:
    rows: dict[tuple, dict] = {}
    for split, recs in (("sample", data.sample_records), ("full", data.full_records)):
        for r in recs:
            key = (r.prefix, r.action, r.label, r.channels)
            row = rows.setdefault(key, {"prefix": key[0], "treatment": key[1], "state": key[2],
                                        "channel": key[3], "sample": 0, "full": 0})
            row[split] += 1
    out = sorted(
        rows.values(), key=lambda r: (r["prefix"], r["treatment"], r["state"], r["channel"])
    )
    for row in out:
        row["new_in_full"] = row["sample"] == 0 and row["full"] > 0
    return out


def unknown_kind(rec: Rec) -> str:
    obs = rec.res.observations
    if not obs:
        return "no_observation"
    if all(o.captured_at > rec.request.audit_at for o in obs):
        return "only_after_audit"
    return "only_mentions"


def assumed_compliant(data: Data) -> dict:
    unknown = [r for r in data.full_records if r.label == "UNKNOWN"]
    kinds = ("no_observation", "only_after_audit", "only_mentions")
    by_prefix: dict[str, Counter] = defaultdict(Counter)
    for r in unknown:
        by_prefix[r.prefix][unknown_kind(r)] += 1
    later_viol = []
    for r in unknown:
        if unknown_kind(r) != "no_observation" and (s := later_violation(r)):
            later_viol.append((r, s))
    examples = [
        {"case_id": r.case_id, "resource_id": r.res.resource_id, "file_id": s.decisive_file,
         "shows": shows(s), "kind": unknown_kind(r)}
        for r, s in sorted(later_viol, key=lambda t: (case_num(t[0].case_id), t[0].res.resource_id))
    ][:15]
    return {
        "total": len(unknown),
        **{k: sum(unknown_kind(r) == k for r in unknown) for k in kinds},
        "by_prefix": {p: dict(c) for p, c in sorted(by_prefix.items())},
        "by_treatment": dict(sorted(Counter(r.action for r in unknown).items())),
        "by_channel": dict(sorted(Counter(r.channels for r in unknown).items())),
        "later_violation": len(later_viol),
        "later_violation_by_kind": dict(Counter(unknown_kind(r) for r, _ in later_viol)),
        "examples": examples,
    }


def rare_records(data: Data) -> list[Rec]:
    from evaluation.diagnostics import gold_resource_ids

    gold_prefixes = {rid.rpartition("_")[0] for rid in gold_resource_ids()}
    rare = set(RARE_PREFIXES) | ({r.prefix for r in data.full_records} - gold_prefixes)
    return sorted((r for r in data.full_records if r.prefix in rare),
                  key=lambda r: (r.prefix, case_num(r.case_id), r.res.resource_id))


def retention_rows(data: Data) -> list[dict]:
    rows = []
    for r in data.full_records:
        if r.prefix not in RETAIN_PREFIXES:
            continue
        dates = [d for o in r.res.observations for k in policy.RETENTION_ATTRS
                 if (d := parse_date(o.attrs.get(k)))]
        newest = max(dates) if dates else None
        audit = r.request.audit_at
        if newest is None or newest <= audit:
            rows.append({"case_id": r.case_id, "resource_id": r.res.resource_id,
                         "max_retention": newest, "audit_at": audit,
                         "treatment": r.action, "source": r.decision.treatment.source,
                         "label": r.label, "steps": [step_line(s) for s in r.steps]})
    return sorted(rows, key=lambda x: (case_num(x["case_id"]), x["resource_id"]))


def retention_summary(data: Data) -> dict:
    counts: Counter = Counter()
    for r in data.full_records:
        if r.prefix not in RETAIN_PREFIXES:
            continue
        dates = [d for o in r.res.observations for k in policy.RETENTION_ATTRS
                 if (d := parse_date(o.attrs.get(k)))]
        if not dates:
            counts["missing"] += 1
        elif max(dates) <= r.request.audit_at:
            counts["expired"] += 1
        else:
            counts["active"] += 1
    return dict(counts)


def identity_rows(data: Data) -> list[Rec]:
    return [r for r in data.full_records
            if "catalog" in r.channels or r.res.flags.get("identity_conflict")]


def multi_rows(data: Data) -> dict[str, list[tuple[str, list[Rec]]]]:
    out: dict[str, list] = defaultdict(list)
    for case_id, info in data.full_cases.items():
        groups: dict[str, list[Rec]] = defaultdict(list)
        for r in info.records:
            groups[r.prefix].append(r)
        for prefix, recs in groups.items():
            if len(recs) > 1:
                out[prefix].append((case_id, recs))
    return {p: sorted(v, key=lambda t: case_num(t[0])) for p, v in sorted(out.items())}


def _case_delete_time(rec_case: CaseInfo, lk) -> datetime:
    """Earliest deletion_worker action on the case's resources that lies inside the case's own
    learned window (events of other cases on the same resource are not this case's deletion);
    reported_at - 1.5h when there is none."""
    case_id = rec_case.request.case_id
    times = [a.event_time for r in rec_case.records for aid in r.res.activity_ids
             if (a := lk.activity(aid)).source_system == linker.WORKER
             and a.event_type in CASE_WORKER_EVENTS and lk.in_window(a, case_id)]
    return min(times) if times else rec_case.request.reported_at - timedelta(hours=1.5)


def unused_activity(data: Data) -> dict:
    lk = data.ctx.lk
    used = lambda a: a.source_system == linker.WORKER or a.event_type == linker.LINK_EVENT  # noqa: E731
    linked_by_resource: dict[str, list[CaseInfo]] = defaultdict(list)
    for info in data.full_cases.values():
        for r in info.records:
            linked_by_resource[r.res.resource_id].append(info)
    types: dict[str, dict] = {}
    pairs: list[tuple[str, CaseInfo, Activity]] = []
    for a in data.activities:
        if used(a):
            continue
        t = types.setdefault(a.event_type, {"total": 0, "touch_linked": 0, "touch_pairs": 0,
                                            "after_delete": 0})
        t["total"] += 1
        infos = linked_by_resource.get(a.resource_id, [])
        t["touch_linked"] += bool(infos)
        for info in infos:
            t["touch_pairs"] += 1
            deleted_at = _case_delete_time(info, lk)
            if deleted_at < a.event_time <= info.request.audit_at:
                t["after_delete"] += 1
                pairs.append((a.event_type, info, a))
    by_type: dict[str, list] = defaultdict(list)
    for t, info, a in sorted(pairs, key=lambda p: (p[0], p[2].event_time, p[1].request.case_id)):
        by_type[t].append((info, a))
    examples = []
    while len(examples) < 10 and any(by_type.values()):  # round-robin over types
        for t in sorted(by_type):
            if by_type[t] and len(examples) < 10:
                info, a = by_type[t].pop(0)
                examples.append({"case_id": info.request.case_id, "event_type": t,
                                 "resource_id": a.resource_id, "time": a.event_time,
                                 "result": a.reported_result, "details": a.details})
    all_used = sum(used(a) for a in data.activities)
    return {"all_events": len(data.activities), "used_total": all_used,
            "types": dict(sorted(types.items())), "examples": examples}


def status_sanity(data: Data) -> dict:
    complete = sorted(
        (c for c, info in data.full_cases.items() if info.status == "COMPLETE"
         and any(later_violation(r) for r in info.records)), key=case_num)
    ledger_only = []
    for case_id, info in data.full_cases.items():
        if info.status != "INCOMPLETE":
            continue
        violating = [r for r in info.records if r.decision.violation]
        if violating and all(
            all(o.copy == "ledger" for o in r.decision.state.observations
                if state.coerce_present(o))
            for r in violating
        ):
            ledger_only.append(case_id)
    ledger_only.sort(key=case_num)
    return {"complete_with_later_violation": len(complete), "complete_examples": complete[:5],
            "ledger_only_incomplete": len(ledger_only), "ledger_examples": ledger_only[:5]}


# ---------- rendering ----------


def _fmt_counts(d: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items()) or "none"


def render(data: Data) -> str:
    full, sample = data.full_records, data.sample_records
    n_complete = sum(i.status == "COMPLETE" for i in data.full_cases.values())
    n_incomplete = len(data.full_cases) - n_complete
    L: list[str] = ["# Full-split blind-spot report", "",
                    f"Full split: {len(data.full_cases)} deletion cases "
                    f"({n_complete} COMPLETE, {n_incomplete} INCOMPLETE), "
                    f"{len(full)} linked resources. Sample split: {len(data.sample_cases)} cases, "
                    f"{len(sample)} linked resources.", ""]

    # 1
    table = type_table(data)
    new = [r for r in table if r["new_in_full"]]
    L += ["## 1. Resource type x (treatment, decided state, via channel)", "",
          f"{len(table)} rows; {len(new)} occur in the full split but never in the sample split "
          "(marked NEW).", "",
          "| prefix | treatment | state | via | sample | full | |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r['prefix']} | {r['treatment']} | {r['state']} | {r['channel']} | {r['sample']} "
          f"| {r['full']} | {'**NEW**' if r['new_in_full'] else ''} |" for r in table]

    # 2
    ac = assumed_compliant(data)
    L += ["", "## 2. Assumed-compliant resources (no capture <= audit_at decides)", "",
          f"Total: {ac['total']} of {len(full)} linked resources "
          f"({100 * ac['total'] / max(len(full), 1):.1f}%).", "",
          f"- (a) no observation at all: {ac['no_observation']}",
          f"- (b) observations only AFTER audit_at: {ac['only_after_audit']}",
          f"- (c) only mentions (and/or later files): {ac['only_mentions']}",
          f"- by treatment: {_fmt_counts(ac['by_treatment'])}",
          f"- by via channel: {_fmt_counts(ac['by_channel'])}", "",
          "| prefix | (a) none | (b) only after | (c) mentions |", "|---|---|---|---|"]
    L += [f"| {p} | {c.get('no_observation', 0)} | {c.get('only_after_audit', 0)} | "
          f"{c.get('only_mentions', 0)} |" for p, c in ac["by_prefix"].items()]
    L += ["", f"A later observation (after audit_at) shows a VIOLATION for "
          f"{ac['later_violation']} of the (b)/(c) resources "
          f"({_fmt_counts(ac['later_violation_by_kind'])}). For these the answer rests on the "
          "assume-compliant rule. First 15:", ""]
    L += [f"- {e['case_id']} {e['resource_id']} ({e['kind']}): later file {e['file_id']} shows "
          f"{e['shows']}" for e in ac["examples"]] or ["- none"]

    # 3
    rare = rare_records(data)
    L += ["", "## 3. Rare categories (marketing_, cache_, email_delivery_, legal_hold_, object_, "
          "and any prefix absent from the sample gold)", "", f"{len(rare)} linked resources.", ""]
    for r in rare:
        L += [f"### {r.case_id} {r.res.resource_id} ({r.action}, "
              f"source={r.decision.treatment.source}, "
              f"category={r.decision.treatment.category}, state={r.label}, "
              f"via={r.channels})"]
        L += [f"- obs: {obs_line(o)}" for o in r.res.observations[:6]] or ["- obs: none"]
        if len(r.res.observations) > 6:
            L.append(f"- ... {len(r.res.observations) - 6} more observations")
        L += [f"- steps: {', '.join(step_line(s) for s in r.steps) or 'none'}", ""]

    # 4
    ret = retention_rows(data)
    L += ["## 4. Retention: audit/legal-hold records with retention <= audit_at or missing", "",
          f"{len(ret)} records. All audit_record_/legal_hold_ resources: "
          f"{_fmt_counts(retention_summary(data))} (active = retention date known and later "
          "than audit_at; the rule only needs the date to be unknown or in the future).", ""]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for x in ret:
        when = "expired" if x["max_retention"] else "missing"
        sig = ", ".join(x["steps"])
        groups[(x["resource_id"].rpartition("_")[0], when, x["treatment"], x["source"],
                x["label"], sig)].append(x)
    L += ["| prefix | retention | treatment (source) | state | steps | records | example cases |",
          "|---|---|---|---|---|---|---|"]
    for (prefix, when, treat, source, label, sig), items in sorted(groups.items()):
        cases = ", ".join(f"{i['case_id']} {i['resource_id']}" for i in items[:3])
        L.append(f"| {prefix} | {when} | {treat} ({source}) | {label} | {sig} | {len(items)} "
                 f"| {cases} |")
    if not ret:
        L.append("- none")

    # 5
    ident = identity_rows(data)
    L += ["", "## 5. Identity conflicts and catalog links", "", f"{len(ident)} resources.", ""]
    for r in ident:
        L.append(f"- {r.case_id} {r.res.resource_id}: via={r.channels}, flags={r.res.flags}, "
                 f"treatment={r.action}, state={r.label}; steps: "
                 f"{', '.join(step_line(s) for s in r.steps) or 'none'}")
    if not ident:
        L.append("- none")

    # 6
    multi = multi_rows(data)
    L += ["", "## 6. Cases with more than one resource of a type", "",
          "Counts per prefix: " + _fmt_counts({p: len(v) for p, v in multi.items()}), ""]
    for prefix, items in multi.items():
        shown = items if prefix == "account" else items[:8]
        L.append(f"### {prefix}_ ({len(items)} cases{'' if shown is items else ', first 8'})")
        for case_id, recs in shown:
            L.append(f"- {case_id}: " + "; ".join(
                f"{r.res.resource_id} [{r.channels}, {r.action}, {r.label}]" for r in recs))
        L.append("")

    # 7
    ua = unused_activity(data)
    L += ["## 7. Activity event types not read by linker/policy/state", "",
          f"{ua['all_events']} events; {ua['used_total']} are deletion_worker or catalog events "
          "(read by the linker). The rest. Deletion event = the case's earliest deletion_worker "
          "action inside its learned window (else reported_at - 1.5h):", "",
          "| event_type | events | touch a linked full-split resource | (case, event) pairs | "
          "after the case's deletion event and <= audit_at |", "|---|---|---|---|---|"]

    L += [f"| {t} | {v['total']} | {v['touch_linked']} | {v['touch_pairs']} | {v['after_delete']} |"
          for t, v in ua["types"].items()]
    L += ["", "Examples (round-robin over types) of events after the deletion event:", ""]
    L += [f"- {e['case_id']} {e['event_type']} {e['resource_id']} {e['time']:%Y-%m-%d %H:%M} "
          f"result={e['result']}: {e['details']}" for e in ua["examples"]] or ["- none"]

    # 8
    ss = status_sanity(data)
    L += ["", "## 8. Status sanity", "",
          f"- COMPLETE cases where a linked resource has a later-than-audit violation: "
          f"{ss['complete_with_later_violation']}; examples: {', '.join(ss['complete_examples'])}",
          f"- INCOMPLETE cases whose only findings come from ledger rows: "
          f"{ss['ledger_only_incomplete']}; examples: {', '.join(ss['ledger_examples'])}", ""]
    return "\n".join(L)


def main() -> int:
    md = render(collect())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(md, encoding="utf-8")
    print(f"written: {REPORT} ({len(md.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
