"""Ambiguity points: the only linking decisions the case agent is allowed to change.

  tie_break          a tie-broken worker-window link this case received
                     (linked, default INCLUDE)
  tie_candidate      a resource of a tie group the case did NOT receive: lost to another case
                     (source tie_break) or left unassigned by SKIP_MULTI
                     (unlinked, default EXCLUDE)
  identity_conflict  a catalog link whose evidence also names another identity
                     (linked, default INCLUDE)

Every other linked resource is mandatory. The defaults reproduce the deterministic answer.
"""

from dataclasses import asdict, dataclass

from ghost.linker import LinkedResource, Linker, _hours, _natural, case_num, rtype

OPTIONS = ("INCLUDE", "EXCLUDE")


@dataclass(frozen=True)
class Ambiguity:
    id: str
    kind: str
    resource_id: str
    competing_case_ids: tuple[str, ...]
    evidence_summary: str
    default: str
    source: str  # tie_break | skip_multi | catalog
    options: tuple[str, ...] = OPTIONS

    def as_dict(self) -> dict:
        d = asdict(self)
        d["competing_case_ids"] = list(self.competing_case_ids)
        d["options"] = list(self.options)
        return d


def _offset(lk: Linker, activity_id: str | None, case_id: str) -> str:
    if not activity_id:
        return "n/a"
    return f"{_hours(lk.activity(activity_id), lk.requests[case_id].reported_at):+.2f}h"


def _tie_summary(lk: Linker, g: dict, rid: str, case_id: str, holder: str | None) -> str:
    act_id = g["acts"][rid].get(case_id)
    act = lk.activity(act_id) if act_id else None
    what = f"{act.event_type} {act.reported_result}" if act else "worker event"
    others = ", ".join(f"{c} {_offset(lk, g['acts'][rid].get(c), c)}"
                       for c in g["cases"] if c != case_id)
    head = (f"no identifier links {rid} to anyone; worker event {act_id} ({what}) is "
            f"{_offset(lk, act_id, case_id)} from this case's reported_at; same fit for: "
            f"{others}. Group: {len(g['rids'])} {rtype(rid)} resource(s) for {len(g['cases'])} "
            f"cases")
    if g["kind"] == "skip_multi":
        return head + "; too many candidates, left unassigned (SKIP_MULTI)."
    return head + (f"; currently assigned to {holder} by event order." if holder
                   else "; unassigned.")


def case_ambiguities(lk: Linker, case_id: str, linked: list[LinkedResource]) -> list[Ambiguity]:
    linked_ids = {r.resource_id for r in linked}
    raw: dict[str, tuple] = {}
    for g in lk.tie_records:
        if case_id not in g["cases"]:
            continue
        for rid in g["rids"]:
            holders = [c for c in g["cases"] if rid in lk.worker_links.get(c, {})]
            holder = holders[0] if holders else None
            competing = tuple(c for c in g["cases"] if c != case_id)
            if holder == case_id:
                if rid not in linked_ids:  # dropped by the stale filter: not a choice
                    continue
                kind, default = "tie_break", "INCLUDE"
            else:
                kind, default = "tie_candidate", "EXCLUDE"
            raw[rid] = (kind, competing, _tie_summary(lk, g, rid, case_id, holder), default,
                        g["kind"])
    subject = lk.subjects.get(case_id)
    mine = set(subject.user_ids) if subject else set()
    for r in linked:
        if not r.flags.get("identity_conflict") or r.resource_id in raw:
            continue
        others = sorted(lk.owners.get(r.resource_id, set()) - mine)
        competing = tuple(c for c in lk.case_ids if c != case_id and lk.subjects.get(c)
                          and set(lk.subjects[c].user_ids) & set(others))
        catalog = [v[1] for v in r.via if v[0] == "catalog"]
        summary = (f"linked by data catalog event {', '.join(catalog)}, but its evidence also "
                   f"carries identifiers of {', '.join(others) or 'an unknown identity'}.")
        raw[r.resource_id] = ("identity_conflict", competing, summary, "INCLUDE", "catalog")
    out = []
    for i, rid in enumerate(sorted(raw, key=_natural), start=1):
        kind, competing, summary, default, source = raw[rid]
        out.append(Ambiguity(f"A{i}", kind, rid, tuple(sorted(competing, key=case_num)),
                             summary, default, source))
    return out


def global_conflicts(lk: Linker, plans: dict[str, dict]) -> list[tuple[str, tuple[str, ...]]]:
    """A resource the agent INCLUDEd for one case must not stay with any other case.

    plans: case_id -> {"resources": every resource in the agent's plan, "included": resources
    changed from EXCLUDE to INCLUDE}. Cases without an agent plan keep their deterministic
    links (both splits: the worker-window assignment is global). Returns (resource, holders)
    for every included resource held by more than one case."""
    held: dict[str, set[str]] = {}

    def holders(rid: str) -> set[str]:
        if rid not in held:
            held[rid] = {c for c in lk.case_ids if c not in plans
                         and any(r.resource_id == rid for r in lk.link(c))}
            held[rid] |= {c for c, p in plans.items() if rid in p["resources"]}
        return held[rid]

    out = []
    for rid in sorted({r for p in plans.values() for r in p["included"]}, key=_natural):
        cases = holders(rid)
        if len(cases) > 1:
            out.append((rid, tuple(sorted(cases, key=case_num))))
    return out
