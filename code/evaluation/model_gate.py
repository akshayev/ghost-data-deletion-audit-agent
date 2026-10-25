"""Per-model validation gate: a model's case-agent decisions are trusted only if, alone, it
reproduces the sample within the margin.

    python code/evaluation/model_gate.py --model groq:openai/gpt-oss-20b    # live, own caches
    python code/evaluation/model_gate.py --model groq:openai/gpt-oss-120b --replay
    python code/evaluation/model_gate.py --table                           # print the results

A live gate run uses only that model (GHOST_AGENT_CHAIN) with its own reply cache and its own
checkpoints (code/cache/llm_gate/<slug>/, code/cache/agent_runs_gate/<slug>/), so it can never
replay another model's answers. --replay scores existing checkpoints without any model call.
Accepted iff every routed sample case got a decision from the model, sample step F1 >=
deterministic - 0.3 pp, and status accuracy is equal. Results: code/evaluation/gate/<slug>.json.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation import main as evalmain  # noqa: E402
from ghost import llm, paths  # noqa: E402
from ghost.config import Config, load_config  # noqa: E402
from ghost.orchestrator import Orchestrator  # noqa: E402

GATE_DIR = paths.EVALUATION / "gate"
MARGIN = 0.003
UNANSWERED = ("FALLBACK_QUOTA", "FALLBACK_LLM_ERROR", "FALLBACK_STALE_CHECKPOINT",
              "FALLBACK_ERROR")


def slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def _config(tag: str, *, mode: str, no_llm: bool, checkpoint_dir: Path | None,
            accepted: list[str] | None) -> Config:
    cfg = load_config(cli={"runs_dir": paths.EVAL_RUNS / f"gate_{tag}", "mode": mode,
                           "no_llm": no_llm, "trace": False})
    agent = {**cfg.agent, "accepted_models": accepted}
    if checkpoint_dir is not None:
        agent["checkpoint_dir"] = str(checkpoint_dir)
    return Config(**{**cfg.__dict__, "agent": agent})


def score(result) -> dict:
    rows = {c.case_id: (c.status, c.steps) for c in result.cases.values() if c.status}
    m = evalmain.evaluate(rows, evalmain.load_rows(evalmain.GOLD))
    return {"step_f1": m["step_full"]["f1"], "precision": m["step_full"]["precision"],
            "recall": m["step_full"]["recall"], "status": m["status_accuracy"]}


def run_gate(spec: str, replay: bool) -> dict:
    provider, model = spec.split(":", 1)
    tag = slug(spec)
    det = score(Orchestrator(_config(f"{tag}_det", mode="deterministic", no_llm=True,
                                     checkpoint_dir=None, accepted=None)).run("sample",
                                                                               write=False))
    if replay:  # existing checkpoints only, decisions of this model only
        cfg = _config(tag, mode="agent", no_llm=True, checkpoint_dir=None, accepted=[model])
    else:
        os.environ[llm.CHAIN_ENV] = spec
        llm.CACHE_DIR = paths.CODE / "cache" / "llm_gate" / tag
        cfg = _config(tag, mode="agent", no_llm=False,
                      checkpoint_dir=paths.CODE / "cache" / "agent_runs_gate" / tag,
                      accepted=[model])
    result = Orchestrator(cfg).run("sample", write=False)
    agent = score(result)
    outcomes = result.summary["agent"]["outcomes"]
    routed = result.summary["agent"]["routed"]
    unanswered = sum(v for k, v in outcomes.items() if k in UNANSWERED or k ==
                     "FALLBACK_UNVALIDATED_MODEL")
    reasons = []
    if unanswered:
        reasons.append(f"{unanswered} of {routed} routed cases without a decision from {model}")
    if agent["step_f1"] < det["step_f1"] - MARGIN:
        reasons.append("step F1 below deterministic - 0.3 pp")
    if agent["status"] != det["status"]:
        reasons.append("status accuracy differs")
    out = {
        "model": model, "provider": provider, "how": "replay" if replay else "live",
        "routed": routed, "outcomes": outcomes, "deterministic": det, "agent": agent,
        "accepted": not reasons, "reasons": reasons,
        "tokens": result.summary.get("llm_tokens", {}),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    GATE_DIR.mkdir(parents=True, exist_ok=True)
    (GATE_DIR / f"{tag}.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def results() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(GATE_DIR.glob("*.json"))]


def accepted_models() -> list[str]:
    return sorted(r["model"] for r in results() if r["accepted"])


def table() -> str:
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    lines = ["| model | how | routed sample cases answered | step F1 | precision | recall | "
             "status acc | accepted |", "|---|---|---|---|---|---|---|---|"]
    for r in results():
        answered = r["routed"] - sum(v for k, v in r["outcomes"].items()
                                     if k in UNANSWERED or k == "FALLBACK_UNVALIDATED_MODEL")
        a = r["agent"]
        verdict = "yes" if r["accepted"] else "no: " + "; ".join(r["reasons"])
        if answered:
            scores = (f"{pct(a['step_f1'])} | {pct(a['precision'])} | {pct(a['recall'])} | "
                      f"{pct(a['status'])}")
        else:  # every case fell back: the scores would just be the deterministic ones
            scores = "n/a | n/a | n/a | n/a"
        lines.append(f"| {r['provider']}:{r['model']} | {r['how']} | {answered}/{r['routed']} | "
                     f"{scores} | {verdict} |")
    if results():
        d = results()[0]["deterministic"]
        lines.append(f"| deterministic (reference) | - | - | {pct(d['step_f1'])} | "
                     f"{pct(d['precision'])} | {pct(d['recall'])} | {pct(d['status'])} | - |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="provider:model, e.g. groq:openai/gpt-oss-20b")
    parser.add_argument("--replay", action="store_true", help="score existing checkpoints only")
    parser.add_argument("--table", action="store_true")
    args = parser.parse_args(argv)
    if args.model:
        r = run_gate(args.model, args.replay)
        print(json.dumps({k: r[k] for k in ("model", "how", "outcomes", "agent", "accepted",
                                             "reasons")}, indent=1))
    if args.table or not args.model:
        print(table())
        print("accepted:", accepted_models())
    return 0


if __name__ == "__main__":
    sys.exit(main())
