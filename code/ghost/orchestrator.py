"""Orchestrator: runs the agents over one split in three phases.

  1. global:   EvidenceAgent -> IntakeAgent -> InvestigatorAgent.global_assign  (then freeze)
  2. per case: IdentityAgent -> InvestigatorAgent -> AssessorAgent -> PlannerAgent
               -> VerifierAgent; a failing case is retried once, then recorded and skipped
  3. write:    validated, deterministic output in case_id order (only if no case failed),
               per-case traces and run_summary.json
"""

import json
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ghost import config as configmod
from ghost import llm, loaders
from ghost.agents import (
    AssessorAgent,
    EvidenceAgent,
    IdentityAgent,
    IntakeAgent,
    InvestigatorAgent,
    PlannerAgent,
    VerifierAgent,
)
from ghost.context import CaseContext, RunContext
from ghost.serialize import OutputRow, write_output
from ghost.tools.registry import ToolEnv, build_registry
from ghost.trace import Tracer

MAX_ATTEMPTS = 2  # first try + one retry


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


class Orchestrator:
    def __init__(self, config: configmod.Config) -> None:
        self.config = config

    def default_out(self, split: str) -> Path:
        return self.config.output if split == "full" else (
            self.config.runs_dir / "sample_output_pred.csv")

    def run(self, split: str, out: Path | None = None, write: bool = True) -> RunResult:
        started = time.perf_counter()
        configmod.apply(self.config)
        calls_before = Counter(llm.CALLS)
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
        investigator = InvestigatorAgent(registry)
        chain = (IdentityAgent(registry), investigator, AssessorAgent(registry),
                 PlannerAgent(registry), VerifierAgent(registry))

        # 1. global phase
        timed(evidence, "run", run)
        timed(intake, "run", run)
        timed(investigator, "global_assign", run)
        run.freeze()

        # 2. per case, isolated
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

        # 3. write
        rows = [OutputRow(c.case_id, c.status, c.steps, c.request.audit_at_raw)
                for c in cases.values() if c.status]
        out_path = None
        if write and not failures:
            out_path = Path(out) if out else self.default_out(split)
            write_output(rows, out_path, env.citable)  # validates every row again
        llm_calls = Counter(llm.CALLS)
        llm_calls.subtract(calls_before)
        summary = {
            "run_id": run.run_id,
            "split": split,
            "cases": len(requests),
            "statuses": dict(sorted(Counter(c.status or "FAILED" for c in cases.values()).items())),
            "failures": failures,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "agent_seconds": {k: round(v, 3) for k, v in sorted(agent_seconds.items())},
            "tool_calls": dict(sorted(registry.counts.items())),
            "llm_calls": {"fresh": llm_calls["fresh"], "cached": llm_calls["cached"]},
            "evidence": run.corpus.stats if run.corpus else {},
            "tiebreak": self.config.tiebreak,
            "output": str(out_path) if out_path else None,
        }
        runs_dir = self.config.runs_dir
        runs_dir.mkdir(parents=True, exist_ok=True)
        tracer.write(runs_dir / "traces")
        (runs_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                   encoding="utf-8")
        return RunResult(split, rows, cases, failures, summary, out_path,
                         1 if failures else 0, run)
