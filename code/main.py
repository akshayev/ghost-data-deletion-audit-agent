"""CLI entry point for the deletion-audit agent.

    python code/main.py --split {full,sample} [--out PATH] [--no-llm]

full   -> reads dataset/deletion_requests.csv, writes <repo>/output.csv
sample -> reads dataset/sample_deletion_requests.csv,
          writes code/evaluation/runs/sample_output_pred.csv
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from ghost import linker, loaders, paths, planner
from ghost.serialize import OutputRow, load_citable_ids, write_output

SPLITS = {
    "full": paths.DATASET / "deletion_requests.csv",
    "sample": paths.DATASET / "sample_deletion_requests.csv",
}
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


def build_context(use_llm: bool = True) -> Context:
    requests = {r.case_id: r for s in ("sample", "full") for r in loaders.load_requests(s)}
    return Context(
        citable=load_citable_ids(),
        use_llm=use_llm,
        lk=linker.build_linker(),
        requests=requests,
    )


def load_requests(split: str) -> list[dict[str, str]]:
    with open(SPLITS[split], encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def classify(request: loaders.DeletionRequest) -> bool:
    """Does request_text authorize deletion? P7 replaces this hook (rules + LLM)."""
    return True


def predict_case(request_row: dict[str, str], context: Context) -> tuple[str, list[dict]]:
    """Decide one case and return (deletion_status, steps)."""
    assert context.lk is not None and context.requests is not None
    request = context.requests[request_row["case_id"]]
    if not classify(request):
        return "NOT_APPLICABLE", []
    result = planner.plan_case(request, context.lk.link(request.case_id), context.lk)
    return result.status, result.steps


def run_split(split: str, use_llm: bool = True) -> tuple[list[OutputRow], Context]:
    context = build_context(use_llm=use_llm)
    rows = []
    for req in load_requests(split):
        status, steps = predict_case(req, context)
        rows.append(OutputRow(req["case_id"], status, steps, req["audit_at"]))
    return rows, context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ghost Data Still Exists deletion-audit agent")
    parser.add_argument("--split", choices=sorted(SPLITS), default="full")
    parser.add_argument("--out", type=Path, help="output CSV (default depends on --split)")
    parser.add_argument("--no-llm", action="store_true", help="rules and caches only")
    args = parser.parse_args(argv)

    rows, context = run_split(args.split, use_llm=not args.no_llm)
    out = args.out or DEFAULT_OUT[args.split]
    write_output(rows, out, context.citable)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"wrote {len(rows)} rows to {out} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
