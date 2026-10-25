"""`python code/main.py explain <case_id>`: why the audit says what it says for one case.

Runs the real pipeline on the case's split (offline: caches and agent checkpoints only, never
the network) and prints, from the run's decisions and trace: the request and its intake label,
the identity match (counts and match kind, no personal values), the route, every linked
resource with its link channel, treatment and decisive evidence, the reason for every step,
the trace summary and, for routed cases, the case agent's tool calls and rationale.
"""

import re
from dataclasses import replace

from ghost import config as configmod
from ghost import loaders
from ghost.orchestrator import Orchestrator

NOT_DELETION = "NOT_DELETION"


class UnknownCase(KeyError):
    pass


def _split_of(case_id: str) -> str:
    for split in ("sample", "full"):
        if any(r.case_id == case_id for r in loaders.load_requests(split)):
            return split
    raise UnknownCase(f"unknown case {case_id!r}")


def intake_labels(config: configmod.Config) -> dict[str, str]:
    """case_id -> intake label for every request (rules + cached model answers)."""
    from ghost import classifier

    decisions = classifier.classify_requests(use_llm=False)
    return {r.case_id: decisions[r.request_text].final
            for s in ("sample", "full") for r in loaders.load_requests(s)}


def _key_kind(key: str) -> str:
    """Describe an identifier without printing it (phones, hashes and emails are personal)."""
    if re.fullmatch(r"user_\d+", key):
        return f"user id {key}"
    if "@" in key:
        return "email"
    if re.fullmatch(r"[0-9a-f]{64}", key):
        return "email hash"
    if re.fullmatch(r"\d{8,}", key):
        return "phone number"
    return "identifier"


def _channel_text(via: tuple) -> str:
    parts = []
    for v in sorted(via, key=repr):
        kind = v[0]
        if kind == "key":
            parts.append(f"{_key_kind(v[1])} in {v[2]}")
        elif kind == "catalog":
            parts.append(f"data catalog event {v[1]}")
        elif kind == "worker_window":
            parts.append(f"worker window (event {v[1]}"
                         + (", tie-broken guess)" if v[2] else ", unique fit)"))
        elif kind == "propagation":
            parts.append(f"sync from {v[2]} (event {v[1]})")
        else:
            parts.append(str(kind))
    return "; ".join(dict.fromkeys(parts))


SOURCES = {
    "worker": "the deletion worker's {action} action on it",
    "ledger": "its ledger record type via deletion_rules.csv",
    "shape": "the shape of its evidence",
    "prefix": "the default for its resource type",
    "retention_expired": "its retention period having ended",
}


def _state_text(d) -> str:
    """The state at audit_at, worded for what the treatment cares about."""
    s, action = d.state, d.treatment.action
    if s.unknown:
        return "no capture at or before audit_at (assumed compliant)"
    if action == "DISABLE":
        return "data flow ACTIVE" if s.active else "data flow disabled or gone"
    if action == "BLOCK":
        return "restore still ALLOWED" if s.risk else "restore blocked"
    if not s.present:
        return "absent"
    if action in ("REDACT", "ANONYMIZE"):
        return "present with prohibited identifiers still set" if s.exposed else \
            "present, identifiers already removed"
    return "present"


def _policy_text(t) -> str:
    cat = f" ({t.category})" if t.category else ""
    return f"{t.action}{cat}, decided by " + SOURCES.get(t.source, t.source).format(
        action=t.action)


def _why(step: dict, decision, cited, finding_no: int | None) -> str:
    rid, phase, t = step["resource_id"], step["phase"], decision.treatment
    if phase == "FINDING":
        return (f"because {step['evidence_id']} shows {rid} {_state_text(decision)} at "
                f"audit_at, but the required treatment is {_policy_text(t)}")
    if phase == "REMEDIATION":
        return (f"because of finding {finding_no} on {rid}; this brings it to the required "
                f"state ({step['state']}); data flows and restores are handled before the "
                f"records that depend on them")
    if step["evidence_id"]:
        how = {"decisive": "its latest capture", "coverage": "a complete listing of its system",
               "coverage_any_system": "a complete listing of another system"}.get(
            cited.citation_source if cited else "", "the evidence")
        return (f"because {step['evidence_id']} ({how}) shows {rid} {_state_text(decision)}, "
                f"which already meets {_policy_text(t)}")
    if t.action == "RETAIN":
        return (f"because policy requires {rid} to be kept ({_policy_text(t)}); confirm it is "
                f"still present after remediation")
    return (f"because after remediation {rid} must be {step['state']}, the end state of "
            f"{_policy_text(t)}")


def _agent_lines(agent: dict | None, route: str, reasons: tuple) -> list[str]:
    if route != "AGENT":
        return ["fast path: no open linking decision, the deterministic plan stands."]
    if not agent:
        return ["routed to the case agent, but no agent ran in this mode."]
    out = [f"outcome {agent.get('outcome')}"
           + (f"; model {agent.get('model')}" if agent.get("model") else "")
           + (f"; {agent.get('tool_calls', 0)} tool calls, {agent.get('rejections', 0)} "
              f"rejections" if "tool_calls" in agent else "")
           + ("; replayed from a checkpoint" if agent.get("replayed") else "")]
    if agent.get("outcome") == "FALLBACK_OFFLINE":
        out.append("no agent answer is cached for this case: the deterministic plan is used.")
    results = {m.get("tool_call_id"): m["content"] for m in agent.get("transcript", ())
               if m.get("role") == "tool"}
    for m in agent.get("transcript", ()):
        for call in m.get("tool_calls", ()):
            fn = call["function"]
            res = results.get(call["id"], "")
            out.append(f"call {fn['name']}({fn['arguments'][:140]}) -> {res[:140]}")
    if agent.get("changes") is not None and agent.get("outcome") == "AGENT_SUBMITTED":
        out.append("changes vs deterministic: " + (", ".join(agent["changes"]) or "none"))
    if agent.get("rationale"):
        out.append(f"rationale: {agent['rationale']}")
    if agent.get("reverted"):
        out.append(f"reverted to the deterministic plan: {agent['reverted']}")
    return out


def explain(case_id: str, config: configmod.Config | None = None) -> str:
    config = config or configmod.load_config()
    split = _split_of(case_id)
    config = replace(config, no_llm=True, runs_dir=config.runs_dir / "explain", trace=True)
    result = Orchestrator(config).run(split, write=False)
    case = result.cases[case_id]
    run, req = result.run, case.request
    decision = run.intake[req.request_text]
    s = case.subject
    L = [f"# Explanation of {case_id}", "", "## Request",
         f"- requested_at {req.requested_at_raw}; reported {req.reported_status} at "
         f"{req.reported_at_raw} (a claim, never evidence); audit_at {req.audit_at_raw}",
         f"- intake: {decision.final} (rules {decision.rule_label}, model "
         f"{decision.llm_label or 'not consulted'}, decided by {decision.source})",
         f"- result: status {case.status} with {len(case.steps)} steps", ""]
    L += ["## Identity"]
    if s is None:
        L.append("- the request email matches no known identity")
    else:
        changes = [h for h in s.history if h[1] in ("email", "phone", "email_hash", "name")]
        L += [f"- matched via {s.matched_via} email: {len(s.user_ids)} user id(s), "
              f"{len(s.emails)} emails, {len(s.phones)} phones, {len(s.hashes)} hashes, "
              f"{len(s.names)} names ({len(s.strong_keys)} strong link keys)",
              f"- {len(changes)} recorded identity change(s)"
              + (": " + ", ".join(sorted({f"{h[1]} at {h[0]}" for h in changes}))
                 if changes else "")]
    L += ["", "## Route", f"- {case.route}"
          + (f" ({', '.join(case.route_reasons)})" if case.route_reasons else ""), ""]
    L += ["## Resources"]
    if not case.authorized:
        L.append("- request does not authorize deletion: no resources are investigated")
    plan = None
    if case.authorized:
        plan = result.registry.call("plan_steps", case_id=case_id)
    cited = {d.resource_id: d for d in plan.decisions} if plan else {}
    by_res = {d.resource_id: d for d in case.assessments}
    for r in case.linked:
        d = by_res[r.resource_id]
        t = d.treatment
        L += [f"- {r.resource_id} ({r.system_id}) linked via {_channel_text(r.via)}",
              f"    treatment {_policy_text(t)}",
              f"    state at audit_at: {_state_text(d)}; decisive file "
              f"{d.state.decisive_file or 'none'}; {'VIOLATION' if d.violation else 'compliant'}"]
    L += ["", "## Steps"]
    if not case.steps:
        L.append("- none (" + ("does not authorize deletion" if not case.authorized
                               else "no verifiable resource") + ")")
    finding_no = {st["resource_id"]: st["step_no"] for st in case.steps
                  if st["phase"] == "FINDING"}
    for step in case.steps:
        rid = step["resource_id"]
        d = by_res[rid]
        L.append(f"{step['step_no']}. {step['phase']} {step['operation']} {step['resource_id']}"
                 f"{' @' + step['evidence_id'] if step['evidence_id'] else ''}: "
                 f"{_why(step, d, cited.get(rid), finding_no.get(rid))}")
    if not case.authorized:
        L.append("The request does not authorize deletion, so the answer is NOT_APPLICABLE "
                 "with no steps.")
    events = run.tracer.for_case(case_id)
    tools = sorted({e.tool for e in events if e.tool})
    L += ["", "## Trace",
          f"- {len(events)} events by {', '.join(sorted({e.agent for e in events}))}",
          f"- tools called: {', '.join(tools) or 'none'}",
          f"- per-case JSONL: {config.runs_dir / 'traces' / (case_id + '.jsonl')}", "",
          "## Agent"]
    L += [f"- {line}" for line in _agent_lines(case.agent, case.route or "FAST",
                                               case.route_reasons)]
    return "\n".join(L) + "\n"
