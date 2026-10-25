"""Orchestrator: runs the agents over one split in phases.

  1. global:   EvidenceAgent -> IntakeAgent -> InvestigatorAgent.global_assign  (then freeze)
  2. per case: IdentityAgent -> InvestigatorAgent -> AssessorAgent -> PlannerAgent
               -> VerifierAgent -> RouterAgent; a failing case is retried once, then recorded
               and skipped. This is the deterministic answer (and the FAST path).
  3. agent:    (mode=agent) CaseInvestigatorAgent on every case the router sent to the AGENT
               path; then the global consistency gate (a resource INCLUDEd by one case must
               not stay with another: both revert to the deterministic plan), then the
               VerifierAgent again on every changed row
  4. write:    validated, deterministic output in case_id order (only if no case failed),
               per-case traces, run_summary.json and agent_decisions.csv
"""

import csv
import json
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ghost import ambiguity, llm, loaders
from ghost import config as configmod
from ghost.agents import (
    AssessorAgent,
    CaseInvestigatorAgent,
    EvidenceAgent,
    IdentityAgent,
    IntakeAgent,
    InvestigatorAgent,
    PlannerAgent,
    RouterAgent,
    VerifierAgent,
)
from ghost.agents.verifier import InvalidRow
from ghost.context import CaseContext, RunContext
from ghost.serialize import OutputRow, write_output
from ghost.tools.registry import ToolEnv, ToolRegistry, build_registry
from ghost.trace import Tracer

MAX_ATTEMPTS = 2  # first try + one retry
DECISION_FIELDS = ("case_id", "route_reasons", "outcome", "applied", "changed_vs_deterministic",
                   "changes", "reverted", "rationale", "provider", "model", "tool_calls",
                   "rejections", "prompt_tokens", "completion_tokens", "replayed")


@dataclass
class RunResult:
    split: str
    rows: list[OutputRow]
    cases: dict[str, CaseContext]
    failures: list[dict]
    summary: dict
    out: Path | None
    exit_code: int
    run: RunContext | None = field(default=None, repr=False)
    registry: ToolRegistry | None = field(default=None, repr=False)
    env: ToolEnv | None = field(default=None, repr=False)


class Orchestrator:
    def __init__(self, config: configmod.Config, chat=None) -> None:
        self.config = config
        self.chat = chat  # the case agent's model call (default: llm.tool_chat)

    def default_out(self, split: str) -> Path:
        return self.config.output if split == "full" else (
            self.config.runs_dir / "sample_output_pred.csv")

    def run(self, split: str, out: Path | None = None, write: bool = True) -> RunResult:
        started = time.perf_counter()
        configmod.apply(self.config)
        calls_before = Counter(llm.CALLS)
        tokens_before = Counter(llm.TOKENS)
        tracer = Tracer(self.config.trace)
        all_requests = {r.case_id: r for s in ("sample", "full") for r in loaders.load_requests(s)}
        requests = {r.case_id: r for r in loaders.load_requests(split)}
        run = RunContext(self.config, split, requests, all_requests, tracer)
        env = ToolEnv(run)
        registry = build_registry(env, tracer)
        agent_seconds: Counter = Counter()

        def timed(agent, method: str, ctx):
            t0 = time.perf_counter()
            try:
                return getattr(agent, method)(ctx)
            finally:
                agent_seconds[agent.name] += time.perf_counter() - t0

        evidence, intake = EvidenceAgent(registry), IntakeAgent(registry)
        investigator, verifier = InvestigatorAgent(registry), VerifierAgent(registry)
        router = RouterAgent(registry)
        chain = (IdentityAgent(registry), investigator, AssessorAgent(registry),
                 PlannerAgent(registry), verifier, router)

        # 1. global phase
        timed(evidence, "run", run)
        timed(intake, "run", run)
        timed(investigator, "global_assign", run)
        run.freeze()

        # 2. per case, isolated (the deterministic answer)
        cases: dict[str, CaseContext] = {}
        failures: list[dict] = []
        for case_id, request in requests.items():
            errors: list[str] = []
            for attempt in range(1, MAX_ATTEMPTS + 1):
                case = CaseContext(request, run, authorized=case_id not in run.non_deletion)
                case.errors = errors
                env.cases[case_id] = case
                cases[case_id] = case
                try:
                    for agent in chain:
                        case = timed(agent, "run", case)
                    break
                except Exception as exc:  # isolate: record, retry once, then give up
                    errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                    tracer.emit(case_id, "Orchestrator", None, f"case failed (attempt {attempt})",
                                {"error": repr(exc), "where": traceback.format_exc(limit=3)})
                    case.status, case.steps = None, []
                    if attempt == MAX_ATTEMPTS:
                        failures.append({"case_id": case_id, "attempts": attempt,
                                         "error": errors[-1]})

        # 3. agent phase (routed cases only)
        agent_stats: dict = {}
        if self.config.mode == "agent":
            agent_stats = self._agent_phase(cases, env, registry, verifier, timed, tracer)

        # 4. write
        rows = [OutputRow(c.case_id, c.status, c.steps, c.request.audit_at_raw)
                for c in cases.values() if c.status]
        out_path = None
        if write and not failures:
            out_path = Path(out) if out else self.default_out(split)
            write_output(rows, out_path, env.citable)  # validates every row again
        llm_calls = Counter(llm.CALLS)
        llm_calls.subtract(calls_before)
        tokens = Counter(llm.TOKENS)
        tokens.subtract(tokens_before)
        per_model: dict[str, dict] = {}
        for (provider, model, kind), n in sorted(tokens.items()):
            if n:
                per_model.setdefault(f"{provider}:{model}", {})[kind] = n
        summary = {
            "run_id": run.run_id,
            "split": split,
            "mode": self.config.mode,
            "cases": len(requests),
            "statuses": dict(sorted(Counter(c.status or "FAILED" for c in cases.values()).items())),
            "failures": failures,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "agent_seconds": {k: round(v, 3) for k, v in sorted(agent_seconds.items())},
            "tool_calls": dict(sorted(registry.counts.items())),
            "llm_calls": {"fresh": llm_calls["fresh"], "cached": llm_calls["cached"]},
            "llm_tokens": per_model,
            "router": dict(sorted(router.stats.items())),
            "agent": agent_stats,
            "evidence": run.corpus.stats if run.corpus else {},
            "tiebreak": self.config.tiebreak,
            "output": str(out_path) if out_path else None,
        }
        runs_dir = self.config.runs_dir
        runs_dir.mkdir(parents=True, exist_ok=True)
        tracer.write(runs_dir / "traces")
        (runs_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                   encoding="utf-8")
        if self.config.mode == "agent":
            self._write_decisions(cases, runs_dir / "agent_decisions.csv")
        return RunResult(split, rows, cases, failures, summary, out_path,
                         1 if failures else 0, run, registry, env)

    # ----- agent phase -----

    def _agent_phase(self, cases, env, registry, verifier, timed, tracer) -> dict:
        cfg = self.config.agent
        agent = CaseInvestigatorAgent(
            registry, chat=self.chat, max_tool_calls=int(cfg["max_tool_calls"]),
            max_rejections=int(cfg["max_rejections"]),
            offline=self.config.no_llm or not self.config.agent_live or not llm.agent_chain(),
            checkpoint_dir=configmod._path(cfg["checkpoint_dir"]),
            salt=f"{self.config.tiebreak}|{self.config.dataset.name}")
        budget = cfg.get("max_agent_cases")
        only = set(cfg.get("only_cases") or ())
        routed = [c for c in cases.values() if c.route == "AGENT" and c.status]
        ran = 0
        for case in routed:
            case.agent = None
            if only and case.case_id not in only:
                case.agent = {"outcome": "NOT_SELECTED"}
                continue
            if budget is not None and ran >= int(budget):
                case.agent = {"outcome": "FALLBACK_BUDGET"}
                continue
            ran += 1
            try:
                timed(agent, "run", case)
            except Exception as exc:  # the deterministic plan stands
                case.agent = {"outcome": "FALLBACK_ERROR", "error": f"{type(exc).__name__}: {exc}"}
                tracer.emit(case.case_id, agent.name, None, "agent failed", {"error": repr(exc)})

        # per-model validation gate: only models that passed the sample gate are trusted
        accepted = cfg.get("accepted_models")
        if accepted is not None:
            for case in routed:
                a = case.agent or {}
                bad = sorted(set(a.get("models", [])) - set(accepted))
                if a.get("outcome") == "AGENT_SUBMITTED" and bad:
                    case.agent = {**a, "outcome": "FALLBACK_UNVALIDATED_MODEL", "unvalidated": bad}

        # the deterministic gate: global consistency over all submitted plans
        submitted = {c.case_id: env.plans[c.agent["plan_id"]] for c in routed
                     if c.agent and c.agent["outcome"] == "AGENT_SUBMITTED"}
        plans = {}
        for case_id, plan in submitted.items():
            points = {a.id: a for a in env.ambiguities(case_id)}
            included = [points[a].resource_id for a, pick in plan["choices"].items()
                        if pick == "INCLUDE" and points[a].default == "EXCLUDE"]
            plans[case_id] = {"resources": plan["resources"], "included": included}
        conflicts = ambiguity.global_conflicts(env.linker, plans)
        reverted = {c for _, holders in conflicts for c in holders if c in plans}
        for rid, holders in conflicts:
            tracer.emit(None, "Orchestrator", None, f"global conflict on {rid}",
                        {"holders": list(holders), "reverted": sorted(set(holders) & reverted)})

        # apply accepted plans; the verifier re-checks every changed row
        for case_id, plan in submitted.items():
            case = cases[case_id]
            if case_id in reverted:
                case.agent["reverted"] = "global_conflict"
                continue
            det = (case.status, case.steps)
            case.status, case.steps = plan["status"], plan["steps"]
            try:
                verifier.run(case)
            except InvalidRow as exc:
                case.status, case.steps = det
                case.agent["reverted"] = f"verifier: {exc}"
                continue
            case.agent["applied"] = True
            case.agent["changed_vs_deterministic"] = (case.status, case.steps) != det

        outcomes = Counter(c.agent["outcome"] for c in routed if c.agent)
        ran = [c.agent for c in routed if c.agent and "tool_calls" in c.agent]
        return {
            "routed": len(routed),
            "outcomes": dict(sorted(outcomes.items())),
            "avg_tool_calls": round(sum(a["tool_calls"] for a in ran) / len(ran), 2) if ran
            else 0,
            "applied": sum(1 for c in routed if c.agent and c.agent.get("applied")),
            "changed_cases": sorted((c.case_id for c in routed if c.agent
                                     and c.agent.get("changed_vs_deterministic")),
                                    key=lambda x: int(x.split("_")[1])),
            "conflicts": [{"resource_id": r, "cases": list(h)} for r, h in conflicts],
            "replayed": sum(1 for a in ran if a.get("replayed")),
            "checkpoint_config": agent.config_hash,
        }

    @staticmethod
    def _write_decisions(cases: dict[str, CaseContext], path: Path) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DECISION_FIELDS, lineterminator="\n")
            w.writeheader()
            for c in cases.values():
                if c.route != "AGENT":
                    continue
                a = c.agent or {"outcome": "NOT_RUN"}
                w.writerow({
                    "case_id": c.case_id,
                    "route_reasons": ";".join(c.route_reasons),
                    "outcome": a.get("outcome"),
                    "applied": bool(a.get("applied")),
                    "changed_vs_deterministic": bool(a.get("changed_vs_deterministic")),
                    "changes": ";".join(a.get("changes", [])),
                    "reverted": a.get("reverted", ""),
                    "rationale": a.get("rationale", ""),
                    "provider": a.get("provider", ""),
                    "model": a.get("model", ""),
                    "tool_calls": a.get("tool_calls", 0),
                    "rejections": a.get("rejections", 0),
                    "prompt_tokens": a.get("prompt_tokens", 0),
                    "completion_tokens": a.get("completion_tokens", 0),
                    "replayed": bool(a.get("replayed")),
                })
