"""CLI entry point for the deletion-audit agent.

    python code/main.py run --split {full,sample} [--out PATH] [--no-llm] [--dataset DIR]
                            [--mode {agent,deterministic}] [--max-agent-cases N]
    python code/main.py eval [--oracle-links] [--custom]   # delegates to code/evaluation
    python code/main.py tools                             # tool registry (also code/docs/TOOLS.md)
    python code/main.py explain <case_id>                 # per-case explanation (P10)

The old form `python code/main.py --split full ...` still works (it means `run`).
full   -> reads dataset/deletion_requests.csv, writes <repo>/output.csv
sample -> reads dataset/sample_deletion_requests.csv,
          writes code/evaluation/runs/sample_output_pred.csv
Runs go through ghost.orchestrator (agents + tool registry + traces).
--mode agent (the default when GROQ_API_KEY or GEMINI_API_KEY is set) sends cases with open
linking decisions to the LLM case investigator; --mode deterministic never does. With
--no-llm, agent mode only replays checkpoints and caches (no network).
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from ghost import classifier, linker, loaders, paths, planner
from ghost.serialize import OutputRow, load_citable_ids

SPLIT_FILES = {"full": "deletion_requests.csv", "sample": "sample_deletion_requests.csv"}
DEFAULT_OUT = {
    "full": paths.OUTPUT,
    "sample": paths.EVAL_RUNS / "sample_output_pred.csv",
}


@dataclass
class Context:
    """Shared, case-independent state for one run."""

    citable: dict[str, str]  # file_id / activity_id / audit_id -> timestamp
    use_llm: bool = True
    lk: linker.Linker | None = None
    requests: dict[str, loaders.DeletionRequest] | None = None
    decisions: dict[str, classifier.Decision] | None = None
    non_deletion: frozenset[str] = frozenset()


def build_context(use_llm: bool = True) -> Context:
    requests = {r.case_id: r for s in ("sample", "full") for r in loaders.load_requests(s)}
    # classify first: NOT_DELETION cases must not compete for worker-window resources
    decisions = classifier.classify_requests(use_llm=use_llm)
    non_deletion = classifier.non_deletion_cases(decisions)
    return Context(
        citable=load_citable_ids(),
        use_llm=use_llm,
        lk=linker.build_linker(non_deletion),
        requests=requests,
        decisions=decisions,
        non_deletion=non_deletion,
    )


def load_requests(split: str) -> list[dict[str, str]]:
    with open(paths.DATASET / SPLIT_FILES[split], encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def classify(request: loaders.DeletionRequest, context: Context) -> bool:
    """Does request_text authorize deletion (or ask to verify one)?"""
    return request.case_id not in context.non_deletion


def predict_case(request_row: dict[str, str], context: Context) -> tuple[str, list[dict]]:
    """Decide one case and return (deletion_status, steps)."""
    assert context.lk is not None and context.requests is not None
    request = context.requests[request_row["case_id"]]
    if not classify(request, context):
        return "NOT_APPLICABLE", []
    result = planner.plan_case(request, context.lk.link(request.case_id), context.lk)
    return result.status, result.steps


def run_split(split: str, use_llm: bool = True, mode: str | None = None
              ) -> tuple[list[OutputRow], Context]:
    """Run the agent pipeline on a split without writing (used by the evaluation)."""
    from ghost.config import load_config
    from ghost.orchestrator import Orchestrator

    config = load_config(cli={"no_llm": not use_llm, "mode": mode})
    result = Orchestrator(config).run(split, write=False)
    if result.failures:
        raise RuntimeError(f"{len(result.failures)} case(s) failed: {result.failures}")
    return result.rows, Context(citable=load_citable_ids(), use_llm=use_llm)


SUBCOMMANDS = ("run", "eval", "tools", "explain")


def _cmd_run(args, parser) -> int:
    from ghost.config import load_config
    from ghost.orchestrator import Orchestrator

    if args.dataset and not Path(args.dataset).is_dir():
        parser.error(f"dataset directory not found: {Path(args.dataset).resolve()}")
    config = load_config(cli={"dataset": args.dataset, "no_llm": args.no_llm,
                              "mode": args.mode, "max_agent_cases": args.max_agent_cases,
                              "agent_cases": args.agent_cases.split(",") if args.agent_cases
                              else None})
    result = Orchestrator(config).run(args.split, out=args.out)
    for f in result.failures:
        print(f"FAILED {f['case_id']} after {f['attempts']} attempts: {f['error']}",
              file=sys.stderr)
    if result.exit_code:
        print(f"{len(result.failures)} case(s) failed; no output written", file=sys.stderr)
        return result.exit_code
    s = result.summary
    statuses = ", ".join(f"{k}={v}" for k, v in s["statuses"].items())
    print(f"wrote {len(result.rows)} rows to {result.out} ({statuses})")
    print(f"run {s['run_id']}: {s['wall_seconds']}s, tool calls {sum(s['tool_calls'].values())}, "
          f"llm fresh={s['llm_calls']['fresh']} cached={s['llm_calls']['cached']}, "
          f"summary {config.runs_dir / 'run_summary.json'}")
    r = s["router"]
    print(f"mode {s['mode']}: router FAST={r.get('FAST', 0)} AGENT={r.get('AGENT', 0)} "
          + " ".join(f"{k}={v}" for k, v in r.items() if ":" in k))
    if s["agent"]:
        a = s["agent"]
        print(f"agent outcomes {a['outcomes']}, applied {a['applied']}, changed "
              f"{len(a['changed_cases'])}, conflicts {len(a['conflicts'])}, "
              f"tokens {s['llm_tokens']}")
    return 0


def _cmd_tools(args, parser) -> int:
    from ghost.tools.registry import build_registry

    md = build_registry().describe()
    doc = paths.CODE / "docs" / "TOOLS.md"
    doc.write_text("# Tool registry\n\nAgents reach the domain layer only through these tools "
                   "(`code/ghost/tools/registry.py`). Generated by `python code/main.py tools`."
                   "\n\n" + md, encoding="utf-8")
    print(md, end="")
    return 0


def _cmd_explain(args, parser) -> int:
    print(f"explain {args.case_id}: not implemented yet (planned for P10). "
          f"Per-case traces are in code/evaluation/runs/traces/{args.case_id}.jsonl")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in SUBCOMMANDS:
        argv = ["run", *argv]  # backwards compatible: flags alone mean `run`
    if argv[0] == "eval":
        from evaluation.main import main as eval_main

        return eval_main(argv[1:])

    parser = argparse.ArgumentParser(prog="main.py",
                                     description="Ghost Data Still Exists deletion-audit agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="audit a split and write the output CSV")
    run.add_argument("--split", choices=sorted(SPLIT_FILES), default="full")
    run.add_argument("--out", type=Path, help="output CSV (default depends on --split)")
    run.add_argument("--no-llm", action="store_true", help="rules and caches only")
    run.add_argument("--dataset", type=Path, help="dataset root (default: <repo>/dataset)")
    run.add_argument("--mode", choices=("agent", "deterministic"),
                     help="agent: LLM investigator on routed cases (default when a key is set)")
    run.add_argument("--max-agent-cases", type=int, help="budget: at most N agent cases")
    run.add_argument("--agent-cases", help="comma-separated case ids: only these go to the agent")
    sub.add_parser("eval", help="score the sample split (see code/evaluation/main.py --help)")
    sub.add_parser("tools", help="print the tool registry")
    explain = sub.add_parser("explain", help="explain one case (P10)")
    explain.add_argument("case_id")
    args = parser.parse_args(argv)
    handler = {"run": _cmd_run, "tools": _cmd_tools, "explain": _cmd_explain}[args.command]
    return handler(args, parser)


if __name__ == "__main__":
    sys.exit(main())
