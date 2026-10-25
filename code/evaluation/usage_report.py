"""Generate code/evaluation/usage_report.md from the usage ledger and the run summary.

    python code/evaluation/usage_report.py [--summary PATH] [--out PATH]

Inputs (nothing is typed by hand):
  - code/cache/usage_ledger.jsonl   real API calls only (cache replays are never ledgered)
  - run summary JSON of the full-dataset run (cache-replay counters, router and agent stats)
  - code/evaluation/runs/numbers.json (python code/evaluation/report_numbers.py)
  - code/config.yaml `pricing:` (USD per 1M tokens)
"""

import argparse
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ghost import llm, loaders, paths  # noqa: E402
from ghost.config import load_config  # noqa: E402

OUT = paths.EVALUATION / "usage_report.md"
NUMBERS = paths.EVAL_RUNS / "numbers.json"
PURPOSES = ("screenshot_extract", "classify_request", "case_agent", "check")


def cost(prices: dict, provider: str, model: str, prompt: int, completion: int) -> float | None:
    p = prices.get(f"{provider}:{model}")
    if not p:
        return None
    return (prompt * p["input"] + completion * p["output"]) / 1_000_000


def aggregate(rows: list[dict]) -> list[dict]:
    """Fresh calls only, grouped by (provider, model, purpose)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if r.get("cache_hit"):
            continue
        key = (r["provider"], r["model"], r["purpose"])
        g = groups.setdefault(key, {"provider": key[0], "model": key[1], "purpose": key[2],
                                    "calls": 0, "prompt": 0, "completion": 0, "latency_ms": 0})
        g["calls"] += 1
        g["prompt"] += r.get("prompt_tokens") or 0
        g["completion"] += r.get("completion_tokens") or 0
        g["latency_ms"] += r.get("latency_ms") or 0
    return [groups[k] for k in sorted(groups, key=lambda k: (k[0], k[1], PURPOSES.index(k[2])
                                                              if k[2] in PURPOSES else 9, k[2]))]


def _money(x: float | None) -> str:
    return "n/a (no price)" if x is None else f"${x:,.4f}"


def _n(x: int) -> str:
    return f"{x:,}"


def _table(groups: list[dict], prices: dict) -> list[str]:
    lines = ["| provider | model | purpose | calls | prompt tokens | completion tokens | "
             "avg latency (s) | est. cost at list price |", "|---|---|---|---|---|---|---|---|"]
    for g in groups:
        c = cost(prices, g["provider"], g["model"], g["prompt"], g["completion"])
        lines.append(f"| {g['provider']} | {g['model']} | {g['purpose']} | {_n(g['calls'])} | "
                     f"{_n(g['prompt'])} | {_n(g['completion'])} | "
                     f"{g['latency_ms'] / 1000 / max(g['calls'], 1):.1f} | {_money(c)} |")
    return lines


def _totals(groups: list[dict], prices: dict) -> tuple[dict, list[str]]:
    per_model: dict[tuple, dict] = {}
    for g in groups:
        m = per_model.setdefault((g["provider"], g["model"]),
                                 {"calls": 0, "prompt": 0, "completion": 0})
        for k in ("calls", "prompt", "completion"):
            m[k] += g[k]
    lines = ["| provider | model | calls | prompt tokens | completion tokens | total tokens | "
             "est. cost at list price |", "|---|---|---|---|---|---|---|"]
    total = {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0}
    for (prov, model), m in sorted(per_model.items()):
        c = cost(prices, prov, model, m["prompt"], m["completion"])
        lines.append(f"| {prov} | {model} | {_n(m['calls'])} | {_n(m['prompt'])} | "
                     f"{_n(m['completion'])} | {_n(m['prompt'] + m['completion'])} | "
                     f"{_money(c)} |")
        for k in ("calls", "prompt", "completion"):
            total[k] += m[k]
        total["cost"] += c or 0.0
    lines.append(f"| **overall** | | **{_n(total['calls'])}** | **{_n(total['prompt'])}** | "
                 f"**{_n(total['completion'])}** | **{_n(total['prompt'] + total['completion'])}** "
                 f"| **{_money(total['cost'])}** |")
    return total, lines


def render_report(ledger: list[dict], summary: dict, numbers: dict, prices: dict,
                  price_meta: dict, full_ids: set[str], n_requests: int) -> str:
    all_groups = aggregate(ledger)
    full_rows = [r for r in ledger if r["purpose"] != "case_agent" or r.get("case_id") in full_ids]
    final_rows = [r for r in full_rows if r["purpose"] in ("screenshot_extract", "classify_request",
                                                           "case_agent")]
    final_groups = aggregate(final_rows)
    final_total, final_totals_md = _totals(final_groups, prices)
    all_total, all_totals_md = _totals(all_groups, prices)
    ledger_numbers = numbers["ledger"]
    agent = numbers["agent"]
    router = numbers["router"]["full"]
    ck = agent["checkpoints"]
    tokens = final_total["prompt"] + final_total["completion"]
    llm_calls = summary.get("llm_calls", {})
    agent_summary = summary.get("agent", {})
    unpriced = sorted({f"{g['provider']}:{g['model']}" for g in all_groups
                       if cost(prices, g["provider"], g["model"], 0, 0) is None})
    by_model: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    for r in ledger:
        if r["purpose"] == "case_agent" and not r.get("cache_hit"):
            key = f"{r['provider']}:{r['model']}"
            by_model[key] = by_model.get(key, 0) + (r.get("prompt_tokens") or 0) + (
                r.get("completion_tokens") or 0)
            first_seen.setdefault(key, r["ts"])
    order = sorted(by_model, key=lambda k: first_seen[k])
    if len(order) > 1:
        quota_sentence = (
            f"The case agent used {_n(by_model[order[0]])} tokens on {order[0]} before the model "
            f"chain moved on to {', '.join(order[1:])} ({_n(sum(by_model[k] for k in order[1:]))} "
            "tokens so far): consistent with the first model's daily quota ending there, which "
            "is the retirement behavior of `llm.tool_chat`.")
    else:
        quota_sentence = (
            f"So far the case agent has used {_n(sum(by_model.values()))} tokens on "
            f"{', '.join(order) or 'no model'}; no model has been retired by a quota yet.")
    status = "in progress" if agent["full"]["pending_offline"] else "complete"

    L = [
        "# Usage report",
        "",
        f"Generated by `python code/evaluation/usage_report.py` on {numbers['generated_at']} "
        f"from `code/cache/usage_ledger.jsonl`, the full-split run summary and `code/config.yaml` "
        f"(`pricing:`, retrieved {price_meta.get('date', '?')}). Agent run status when generated: "
        f"**{status}** ({agent['full']['submitted']} of {router['agent']} routed cases have an "
        f"agent answer in the replay; the rest use the deterministic plan).",
        "",
        "All runs used free tiers, so the **actual cost is $0.00**. The \"estimated cost\" "
        "columns price the same tokens at the list prices in `config.yaml`.",
        "",
        "## (a) The full-dataset run that produced `output.csv`",
        "",
        f"- Mode: `{summary.get('mode')}`; {summary.get('cases')} requests; "
        f"{router['agent']} routed to the LLM case agent, {router['fast']} on the deterministic "
        f"fast path ({router['not_deletion']} of those are non-deletion requests that never "
        f"reach any model).",
        f"- Model calls inside the final run process: **{llm_calls.get('fresh', 0)} fresh**, "
        f"**{llm_calls.get('cached', 0)} cache replays**. The run is a replay: every answer it "
        f"uses was produced earlier by the API calls counted below and is read back from "
        f"`code/cache/llm/` and `code/cache/agent_runs/`.",
        f"- Agent outcomes in the run: {json.dumps(agent_summary.get('outcomes', {}))}; "
        f"{agent_summary.get('applied', 0)} agent plans applied.",
        "",
        "API calls that produced those answers (screenshots and intake for both splits, the "
        "case agent for the full split only):",
        "",
        *_table(final_groups, prices),
        "",
        *final_totals_md,
        "",
        f"- **Total tokens:** {_n(tokens)} "
        f"({_n(final_total['prompt'])} in / {_n(final_total['completion'])} out).",
        f"- **Average per request** (/{n_requests}): {_n(round(tokens / n_requests))} tokens "
        f"({_n(round(final_total['prompt'] / n_requests))} in / "
        f"{_n(round(final_total['completion'] / n_requests))} out); "
        f"{final_total['calls'] / n_requests:.2f} API calls.",
        f"- **Average per routed case:** {_n(ck['avg_tokens_per_case'])} tokens, "
        f"{ck['avg_tool_calls']} tool calls, {ck['avg_turns']} model turns "
        f"(over {ck['checkpoints']} checkpointed cases, sample and full).",
        f"- **Estimated cost at list price:** {_money(final_total['cost'])} total, "
        f"{_money(final_total['cost'] / n_requests)} per request. **Actual:** $0.00.",
        "",
        "## (b) Cumulative API usage behind the cached answers",
        "",
        "Every real call ever made by this repository (development included), from the ledger. "
        "Cache replays are not in the ledger.",
        "",
        *_table(all_groups, prices),
        "",
        *all_totals_md,
        "",
        f"The ledger holds {ledger_numbers['lines_total']} lines: {ledger_numbers['fresh_calls']} "
        f"real calls plus {ledger_numbers['legacy_cache_hit_lines']} zero-token cache-hit lines "
        "written by an older version of `llm.py` (cache replays are no longer ledgered; they are "
        "counted in `run_summary.json` as `llm_calls.cached`).",
        "",
        "## (c) Token-efficiency design",
        "",
        "- **Caching keyed on the request.** `llm.chat` keys on provider, purpose, messages and "
        "image hash; `llm.tool_chat` keys on messages plus the SHA-256 of the tool schemas "
        "(`_tool_cache_key`). A hit never touches the network or the ledger, and works with no "
        "API key. An entry made under another model name is reused (`_find_cached`).",
        f"- **Batching.** The intake classifier needs {ledger_numbers['classify_batches_needed']} "
        f"batches of up to 40 request texts for {numbers['intake']['unique_texts']} unique "
        f"texts ({numbers['intake']['requests']} requests), not one call per request.",
        f"- **Confidence routing.** Only cases with an open linking decision call the case agent: "
        f"{router['agent']} of {router['cases']} full-split cases ({router['agent_share']}); the "
        f"other {router['fast']} ({router['fast_share']}) are fully deterministic. Non-deletion "
        "requests never enter the agent.",
        "- **Compact tool outputs.** Agent tool results are capped near 900 tokens "
        "(`registry._fit`, `MAX_RESULT_CHARS`), the system prompt is about 500 tokens "
        "(`prompts/case_agent_system.md`), and the model is told to batch calls in one turn.",
        "- **Only the ambiguity is delegated.** The model decides INCLUDE/EXCLUDE for open "
        "points; the planner writes the steps, so output tokens stay small "
        f"({_n(ck['completion_tokens'])} completion tokens over {ck['checkpoints']} cases).",
        "- **Bounded loops.** At most 8 tool calls and 2 rejected proposals per case "
        "(`config.yaml` `agent:`), then the deterministic plan is kept.",
        "- **Checkpointing and offline replay.** Finished cases are stored under "
        "`code/cache/agent_runs/<config_hash>/`; a rerun, `--no-llm`, or a machine with no keys "
        "replays them for free and reproduces the same output.",
        "- **Rate limiting and quota awareness.** `RateLimiter` keeps each model under its "
        "requests/min and tokens/min budget, honours `Retry-After`, and retires a model for the "
        "run when the provider reports a daily quota.",
        "",
        "## (d) Honest notes",
        "",
        f"- **Wasted intake calls:** {ledger_numbers['classify_calls']} real intake calls were "
        f"made for {ledger_numbers['classify_batches_needed']} batches, so "
        f"**{ledger_numbers['classify_wasted']} were repeats**. Switching Gemini model "
        "mid-run (after the daily quota) changed the cache key, so batches already answered "
        "were requested again. The cross-model lookup in `llm._find_cached` fixed this.",
        "- **Gemini free tier:** 20 requests per day per model. The 11 screenshot calls and the "
        "intake batches exhausted `gemini-3.8-flash`; the remaining batches ran on "
        "`gemini-3.5-flash`.",
        "- **Groq free tier:** documented limits are 1,000 requests/day, 8K tokens/minute and "
        "200K tokens/day per model (as given for this event; not re-verified with Groq). "
        + quota_sentence
        + f" At about {_n(ck['avg_tokens_per_case'])} tokens per routed case, the "
        f"{router['agent']} routed full-split cases need roughly {agent['routed_tokens']} "
        "tokens in total. The 7K tokens/minute client limit also makes the run slow (about one "
        "case per 100 seconds), which is why the run is time-boxed.",
        "- **Time-boxed agent run:** the full-split agent run stops at 15:00 IST or when the "
        "daily quota is exhausted. Routed cases without an agent answer use the deterministic "
        "default (`FALLBACK_QUOTA`). The totals above are provisional until that run ends.",
        "- Costs are estimates from list prices; nothing was billed."
        + (f" No price is configured for {', '.join(unpriced)}: those rows show \"n/a\" and are "
           "left out of the cost totals (they are connectivity checks)." if unpriced else ""),
        "",
    ]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--summary", type=Path,
                        help="full-split run_summary.json (default: the offline replay summary)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if not NUMBERS.exists():
        print(f"{NUMBERS} not found: run python code/evaluation/report_numbers.py first",
              file=sys.stderr)
        return 1
    numbers = json.loads(NUMBERS.read_text(encoding="utf-8"))
    summary_path = args.summary or paths.EVAL_RUNS / "numbers_agent_full" / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    ledger = [json.loads(x) for x in llm.LEDGER_PATH.read_text(encoding="utf-8").splitlines()
              if x.strip()]
    pricing = load_config().raw["pricing"]
    full_ids = {r.case_id for r in loaders.load_requests("full")}
    args.out.write_text(render_report(ledger, summary, numbers, pricing["models"], pricing,
                                      full_ids, len(full_ids)) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
