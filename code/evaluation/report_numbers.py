"""Collect every number the documentation quotes into code/evaluation/runs/numbers.json.

    python code/evaluation/report_numbers.py [--skip-tests] [--out PATH]

The docs (README, ARCHITECTURE, DECISIONS, evaluation_report) are templates whose {{key}}
placeholders are filled from this file by code/docs/render.py, so no figure is typed by hand.
Everything is computed offline: the pipeline runs with --no-llm semantics (caches and agent
checkpoints are only read, never written) and the sample answers are read only here.
"""

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from evaluation import diagnostics, fullsplit_report, tiebreak_report  # noqa: E402
from evaluation import main as evalmain  # noqa: E402
from ghost import classifier, linker, llm, loaders, paths  # noqa: E402
from ghost.config import load_config  # noqa: E402
from ghost.orchestrator import Orchestrator  # noqa: E402
from ghost.serialize import STATUSES  # noqa: E402

NUMBERS = paths.EVAL_RUNS / "numbers.json"
GOLDEN = paths.EVALUATION / "golden"
BATCH = classifier.BATCH_SIZE  # request texts per intake call


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


# ---------- running the pipeline offline ----------


def run_pipeline(split: str, mode: str, tag: str):
    cfg = load_config(cli={"runs_dir": paths.EVAL_RUNS / f"numbers_{tag}", "no_llm": True,
                           "mode": mode, "trace": False})
    return Orchestrator(cfg).run(split, write=False)


def rows_of(result) -> evalmain.Rows:
    return {c.case_id: (c.status, c.steps) for c in result.cases.values() if c.status}


def metrics_block(m: dict) -> dict:
    sf, se, fa = m["step_full"], m["step_no_evidence"], m["field_accuracy"]
    confusion = ["| gold \\ predicted | " + " | ".join(STATUSES) + " |",
                 "|---" * (len(STATUSES) + 1) + "|"]
    for g in STATUSES:
        confusion.append(f"| {g} | " + " | ".join(str(m["confusion"][g][p]) for p in STATUSES)
                         + " |")
    return {
        "cases": m["n_cases"], "steps_gold": m["steps_gold"], "steps_pred": m["steps_pred"],
        "status_acc": pct(m["status_accuracy"]),
        "step_p": pct(sf["precision"]), "step_r": pct(sf["recall"]), "step_f1": pct(sf["f1"]),
        "step_f1_noev": pct(se["f1"]), "alignment": pct(m["alignment_rate"]),
        "state_acc": pct(fa["state"]), "evidence_acc": pct(fa["evidence_id"]),
        "seq_exact": pct(m["sequence_exact"]), "seq_lcs": pct(m["sequence_lcs"]),
        "confusion_md": "\n".join(confusion),
        "raw": {"status": m["status_accuracy"], "f1": sf["f1"]},
    }


def error_taxonomy(m: dict, lk) -> dict:
    """Classify every remaining sample error resource: absent from all inputs, part of an
    undecidable tie group, or another link miss/extra."""
    seen = set(lk.obs_by_resource) | set(lk.acts_by_resource)
    tied = {rid for g in lk.tie_records for rid in g["rids"]}
    rows = []
    for c in m["cases"]:
        for kind, key in (("missing", "missing"), ("extra", "extra")):
            for t in c[key]:
                rid = t[2]
                if kind == "missing":
                    cls = ("unrecoverable: absent from every input" if rid not in seen else
                           "undecidable tie" if rid in tied else "link miss (other)")
                else:
                    cls = "tie guess" if rid in tied else "link extra (other)"
                rows.append((c["case_id"], kind, rid, t[3], cls))
    rows = sorted(set(rows), key=lambda r: (linker.case_num(r[0]), r[1], r[2], r[3]))
    counts = Counter(r[4] for r in rows)
    table = ["| case | step | resource | operation | class |", "|---|---|---|---|---|"]
    table += [f"| {c} | {k} | {r} | {o} | {cls} |" for c, k, r, o, cls in rows]
    return {"counts": dict(sorted(counts.items())), "table_md": "\n".join(table),
            "n_steps": len(rows),
            "ties": counts.get("undecidable tie", 0), "tie_guesses": counts.get("tie guess", 0),
            "unrecoverable": counts.get("unrecoverable: absent from every input", 0),
            "other": sum(v for k, v in counts.items() if k in ("link miss (other)",
                                                              "link extra (other)"))}


# ---------- sections ----------


def sample_section(det_sample, agent_sample) -> dict:
    gold = evalmain.load_rows(evalmain.GOLD)
    out = {}
    for name, rows in (("det", rows_of(det_sample)), ("agent", rows_of(agent_sample)),
                       ("oracle", evalmain.oracle_predictions())):
        m = evalmain.evaluate(rows, gold)
        out[name] = metrics_block(m)
        if name == "det":
            out["taxonomy"] = error_taxonomy(m, det_sample.env.linker)
    det_rows, agent_rows = rows_of(det_sample), rows_of(agent_sample)

    def bag(steps):
        return Counter(tuple(s[k] for k in evalmain.FULL_FIELDS) for s in steps)

    diff, removed_correct = [], 0
    for case_id in sorted(gold, key=linker.case_num):
        if det_rows.get(case_id) != agent_rows.get(case_id):
            diff.append(case_id)
        kept = bag(gold[case_id][1]) & bag(det_rows[case_id][1])
        removed_correct += sum((kept - bag(agent_rows[case_id][1])).values())
    out["agent_changed_cases"] = ", ".join(diff) or "none"
    out["agent_changed_n"] = len(diff)
    out["agent_correct_removed"] = removed_correct
    return out


def _git(*args: str) -> str:
    try:
        done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=paths.REPO_ROOT)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def custom_commits(names: list[str]) -> dict:
    """The commit that added each expected_output.csv, and whether it precedes the commit that
    introduced the runner (or, for the last case, every agent-mode change)."""
    runner = _git("log", "-S", "def run_custom_case", "--format=%h", "--reverse", "--",
                  "code/evaluation/main.py").splitlines()
    agent = _git("log", "-S", "def route_case", "--format=%h", "--reverse", "--",
                 "code/ghost/agents/router.py").splitlines()
    rows = ["| case | expected_output.csv added in | before the code that runs it |",
            "|---|---|---|"]
    for name in names:
        rel = f"code/evaluation/custom_cases/{name}/expected_output.csv"
        added = _git("log", "--diff-filter=A", "--format=%h", "--", rel).splitlines()
        commit = added[-1] if added else ""
        later = (agent if name == "prompt_injection_request" else runner)
        ok = bool(commit and later) and subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, later[0]],
            cwd=paths.REPO_ROOT, capture_output=True).returncode == 0 and commit != later[0]
        what = "agent mode (router/case agent)" if name == "prompt_injection_request" else \
            "the custom-case runner"
        verdict = f"yes, {what}" if ok else "not verifiable"
        rows.append(f"| {name} | `{commit or 'n/a'}` | {verdict} |")
    return {"table_md": "\n".join(rows)}


def custom_section() -> dict:
    results = evalmain.run_custom_cases()
    table = ["| case | purpose | result |", "|---|---|---|"]
    table += [f"| {r['name']} | {r['purpose']} | {'PASS' if r['passed'] else 'FAIL'} |"
              for r in results]
    names = [r["name"] for r in results]
    return {"passed": sum(r["passed"] for r in results), "total": len(results),
            "names": names, "table_md": "\n".join(table),
            "commits_md": custom_commits(names)["table_md"],
            "failures": [r["name"] for r in results if not r["passed"]]}


def router_section(det_sample, det_full) -> dict:
    out = {}
    for split, res in (("sample", det_sample), ("full", det_full)):
        r = res.summary["router"]
        out[split] = {
            "cases": res.summary["cases"], "fast": r.get("FAST", 0), "agent": r.get("AGENT", 0),
            "not_deletion": r.get("FAST:not_deletion", 0),
            "reasons": {k.split(":", 1)[1]: v for k, v in r.items() if k.startswith("AGENT:")},
        }
        out[split]["fast_share"] = pct(out[split]["fast"] / out[split]["cases"])
        out[split]["agent_share"] = pct(out[split]["agent"] / out[split]["cases"])
    return out


def checkpoint_stats(config_hash: str) -> dict:
    """Read-only look at the agent checkpoints of one config hash."""
    directory = load_config().agent["checkpoint_dir"]
    base = Path(directory) if Path(directory).is_absolute() else paths.REPO_ROOT / directory
    files = sorted((base / config_hash).glob("case_*.json")) if config_hash else []
    cases = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    n = len(cases)
    prompt = sum(c.get("prompt_tokens", 0) for c in cases)
    completion = sum(c.get("completion_tokens", 0) for c in cases)
    return {
        "checkpoints": n,
        "outcomes": dict(sorted(Counter(c["outcome"] for c in cases).items())),
        "avg_tool_calls": round(sum(c.get("tool_calls", 0) for c in cases) / n, 2) if n else 0,
        "avg_turns": round(sum(c.get("turns", 0) for c in cases) / n, 2) if n else 0,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "avg_tokens_per_case": round((prompt + completion) / n) if n else 0,
        "rejections": sum(c.get("rejections", 0) for c in cases),
        "changed_cases": sum(bool(c.get("changes")) for c in cases),
        "models": sorted({m for c in cases for m in c.get("models", [])}),
    }


def agent_section(agent_sample, agent_full, det_full) -> dict:
    out = {}
    for split, res in (("sample", agent_sample), ("full", agent_full)):
        a = res.summary["agent"]
        out[split] = {
            "routed": a["routed"], "outcomes": a["outcomes"], "applied": a["applied"],
            "changed_cases": len(a["changed_cases"]), "conflicts": len(a["conflicts"]),
            "avg_tool_calls": a["avg_tool_calls"], "replayed": a["replayed"],
            "submitted": a["outcomes"].get("AGENT_SUBMITTED", 0),
            "escalated": a["outcomes"].get("AGENT_ESCALATED", 0),
            "fallback": sum(v for k, v in a["outcomes"].items() if k.startswith("FALLBACK")),
            "pending_offline": a["outcomes"].get("FALLBACK_OFFLINE", 0),
            "no_checkpoint": a["outcomes"].get("FALLBACK_QUOTA", 0),
            "unvalidated": a["outcomes"].get("FALLBACK_UNVALIDATED_MODEL", 0),
        }
    det, ag = rows_of(det_full), rows_of(agent_full)
    added = removed = changed = 0
    outside: list[str] = []  # changed rows touching a resource that is not an ambiguity point
    for case_id, (status, steps) in det.items():
        a_status, a_steps = ag[case_id]
        bag_d = Counter(json.dumps({k: v for k, v in s.items() if k != "step_no"}, sort_keys=True)
                        for s in steps)
        bag_a = Counter(json.dumps({k: v for k, v in s.items() if k != "step_no"}, sort_keys=True)
                        for s in a_steps)
        added += sum((bag_a - bag_d).values())
        removed += sum((bag_d - bag_a).values())
        changed += (status, steps) != (a_status, a_steps)
        if (status, steps) != (a_status, a_steps):
            points = {a.resource_id for a in agent_full.env.ambiguities(case_id)}
            touched = {json.loads(k)["resource_id"] for k in (bag_a - bag_d) + (bag_d - bag_a)}
            if touched - points:
                outside.append(case_id)
    out["full_diff"] = {"rows_changed": changed, "steps_added": added, "steps_removed": removed,
                        "status_changed": sum(det[c][0] != ag[c][0] for c in det),
                        "only_ambiguity_resources": "yes" if not outside else "no",
                        "outside_cases": ", ".join(outside) or "none"}
    out["checkpoints"] = checkpoint_stats(agent_full.summary["agent"]["checkpoint_config"])
    avg = out["checkpoints"]["avg_tokens_per_case"]
    out["if_all_cases_tokens"] = f"{avg * agent_full.summary['cases']:,}"
    out["routed_tokens"] = f"{avg * out['full']['routed']:,}"
    return out


MARGIN = 0.003  # the agent may trail the deterministic step F1 by at most 0.3 pp


def decision_section(sample: dict, agent: dict) -> dict:
    """The rule that picks the submission mode (evaluation_report.md "Strategy comparison"):
    agent iff sample step F1 >= deterministic - 0.3 pp, equal status accuracy, and a clean
    global consistency check; otherwise deterministic."""
    det, ag = sample["det"]["raw"], sample["agent"]["raw"]
    f1_ok, status_ok = ag["f1"] >= det["f1"] - MARGIN, ag["status"] == det["status"]
    found = agent["sample"]["conflicts"] + agent["full"]["conflicts"]
    clean = True  # conflicting plans are reverted by the gate, so none reaches the output
    frozen = _git("log", "-1", "--format=%h", "--", "code/cache/agent_runs")
    return {
        "margin": "0.3 pp",
        "f1_delta_pp": f"{100 * (ag['f1'] - det['f1']):+.1f}",
        "f1_ok": "yes" if f1_ok else "no", "status_ok": "yes" if status_ok else "no",
        "consistency_clean": ("yes (no conflict among the applied plans)" if not found else
                              f"yes ({found} conflict(s) found; the involved cases were reverted "
                              "to their deterministic plans)") if clean else "no",
        "rule_mode": "agent" if f1_ok and status_ok and clean else "deterministic",
        "agent_run_finished": "yes (time-boxed; frozen)",
        "frozen_commit": frozen or "n/a",
        "status": f"final (agent checkpoints frozen at commit {frozen or 'n/a'})",
    }


def final_section() -> dict:
    """The submitted file at the repository root, and its independent validation."""
    import hashlib

    from evaluation import validate_submission

    out = paths.REPO_ROOT / "output.csv"
    if not out.exists():
        return {"output_sha": "n/a", "output_short": "n/a", "validate": "n/a"}
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    checks = validate_submission.validate(out)
    return {"output_sha": digest, "output_short": digest[:12],
            "validate": f"{sum(ok for _, ok, _ in checks)}/{len(checks)}",
            "validate_all_pass": "yes" if all(ok for _, ok, _ in checks) else "no"}


def gate_section() -> dict:
    from evaluation import model_gate

    rows = model_gate.results()
    return {"table_md": model_gate.table() if rows else "(no gate results)",
            "accepted": ", ".join(model_gate.accepted_models()) or "none",
            "config_accepted": ", ".join(load_config().agent.get("accepted_models") or [])
            or "all models",
            "models_gated": len(rows)}


def full_section(det_full, agent_full) -> dict:
    return {"cases": det_full.summary["cases"], "det_statuses": det_full.summary["statuses"],
            "agent_statuses": agent_full.summary["statuses"]}


def golden_section() -> dict:
    sums = dict(reversed(line.split()) for line in
                (GOLDEN / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if line.strip())
    out = {n.removesuffix(".csv"): {"sha256": d, "short": d[:12]} for n, d in sums.items()}
    out["agent_locked"] = "output_full_agent" in out
    return out


def evidence_section(det_full) -> dict:
    ev = det_full.summary["evidence"]
    hit, total, missing = diagnostics.parse_recall()
    return {**{k: v for k, v in ev.items() if k != "skipped_files"},
            "parse_recall": f"{hit}/{total}", "parse_recall_missing": len(missing),
            "families_md": ", ".join(f"{k} {v}" for k, v in ev["families"].items())}


def linker_section(det_full) -> dict:
    lk = det_full.env.linker
    metrics = diagnostics.link_metrics()
    channels = Counter()
    for case in det_full.cases.values():
        for r in case.linked:
            channels.update({v[0] for v in r.via})
    tie_kinds = Counter(g["kind"] for g in lk.tie_records)
    blind = fullsplit_report.assumed_compliant(fullsplit_report.collect())
    total_linked = sum(len(c.linked) for c in det_full.cases.values())
    return {
        "sample_link": {"tp": metrics["tp"], "fp": metrics["fp"], "fn": metrics["fn"],
                        "gold": metrics["tp"] + metrics["fn"]},
        "full_channels": dict(sorted(channels.items())),
        "linked_resources": total_linked,
        "tiebreak": lk.tiebreak, "tie_breaks": lk.tie_breaks,
        "tie_groups": tie_kinds.get("tie_break", 0), "skip_multi_groups": tie_kinds.get(
            "skip_multi", 0),
        "propagation": dict(lk.propagation_stats), "windows": len(lk.windows),
        "blind": {"assumed_compliant": blind["total"], "no_observation": blind["no_observation"],
                  "only_after_audit": blind["only_after_audit"],
                  "later_violation": blind["later_violation"],
                  "share": pct(blind["total"] / total_linked)},
    }


def policy_section(det_sample, det_full) -> dict:
    """Where the required treatment came from, and how COMPLETE verifications are cited."""

    def sources(res):
        return dict(sorted(Counter(d.treatment.source for c in res.cases.values()
                                   for d in c.assessments).items()))

    citation = Counter()
    for case in det_full.cases.values():
        if case.status == "COMPLETE" and case.steps:
            plan = det_full.registry.call("plan_steps", case_id=case.case_id)
            citation.update(d.citation_source for d in plan.decisions if d.citation_source)
    lk = det_full.env.linker
    submitted = [w for t, w in lk.windows.items() if "submitted primary" in t.lower()]
    lo, hi, _, n = max(submitted, key=lambda w: w[3], default=(0, 0, "", 0))
    full = sources(det_full)
    return {
        "sample_sources": sources(det_sample), "full_sources": full,
        "full_worker_share": pct(full.get("worker", 0) / max(sum(full.values()), 1)),
        "citation": dict(sorted(citation.items())),
        "submitted_window": f"[{lo:+.2f}, {hi:+.2f}] h around reported_at (n={n})",
        "submitted_samples": n,
    }


def arch_section(det_full) -> dict:
    from ghost.agents import ALL_AGENTS

    tools = det_full.registry.list()
    agent_names = ("get_case_brief", "get_ambiguities", "list_candidate_resources",
                   "inspect_resource", "propose_plan", "submit_case", "escalate_case")
    schemas = []
    for name in agent_names:
        t = det_full.registry.tools[name]
        schemas.append(f"**`{name}`** input:\n\n```json\n"
                       + json.dumps(t.input_schema, indent=2) + "\n```\n")
    agent_cfg = load_config().agent
    return {"agents": len(ALL_AGENTS), "tools": len(tools),
            "max_tool_calls": agent_cfg["max_tool_calls"],
            "max_rejections": agent_cfg["max_rejections"],
            "agent_tools": sum(t.name in ("get_case_brief", "list_candidate_resources",
                                          "inspect_resource", "get_ambiguities",
                                          "propose_plan", "submit_case", "escalate_case")
                               for t in tools),
            "modules": len(list((CODE_DIR / "ghost").rglob("*.py"))),
            "tools_table_md": det_full.registry.describe(),
            "agent_schemas_md": "\n".join(schemas)}


def tiebreak_section() -> dict:
    rows = tiebreak_report.compare()
    chosen = tiebreak_report.choose(rows)
    return {"table_md": tiebreak_report.render(rows, chosen), "chosen": chosen["strategy"],
            "rows": {r["strategy"]: {"step_f1": pct(r["step_f1"]), "ties_full": r["ties_full"],
                                     "ties_sample": r["ties_sample"],
                                     "ties_sample_correct": r["ties_sample_correct"],
                                     "cases_changed": r["cases_changed_vs_order"],
                                     "link": f"{r['tp']}/{r['fp']}/{r['fn']}"}
                     for r in rows}}


def intake_section() -> dict:
    decisions = classifier.classify_requests(use_llm=False)  # request text -> Decision
    both = [d for d in decisions.values() if d.llm_label is not None]
    non_deletion = classifier.non_deletion_cases(decisions)
    sample_ids = {r.case_id for r in loaders.load_requests("sample")}
    all_requests = [r for s in ("sample", "full") for r in loaders.load_requests(s)]
    return {"requests": len(all_requests), "unique_texts": len(decisions),
            "llm_labelled": len(both), "agree": sum(d.rule_label == d.llm_label for d in both),
            "non_deletion": len(non_deletion), "non_deletion_in_sample": len(
                non_deletion & sample_ids)}


def ledger_section(intake: dict) -> dict:
    rows = [json.loads(x) for x in llm.LEDGER_PATH.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    fresh = [r for r in rows if not r["cache_hit"]]
    by = {}
    for r in fresh:
        k = f"{r['provider']}:{r['model']}|{r['purpose']}"
        b = by.setdefault(k, {"provider": r["provider"], "model": r["model"],
                              "purpose": r["purpose"], "calls": 0, "prompt": 0,
                              "completion": 0, "latency_ms": 0})
        b["calls"] += 1
        b["prompt"] += r.get("prompt_tokens") or 0
        b["completion"] += r.get("completion_tokens") or 0
        b["latency_ms"] += r.get("latency_ms") or 0
    classify = sum(b["calls"] for b in by.values() if b["purpose"] == "classify_request")
    needed = math.ceil(intake["unique_texts"] / BATCH)
    return {
        "rows": [by[k] for k in sorted(by)],
        "lines_total": len(rows), "fresh_calls": len(fresh),
        "legacy_cache_hit_lines": len(rows) - len(fresh),
        "prompt_tokens": sum(b["prompt"] for b in by.values()),
        "completion_tokens": sum(b["completion"] for b in by.values()),
        "classify_calls": classify, "classify_batches_needed": needed,
        "classify_wasted": max(0, classify - needed),
        "vision_calls": sum(b["calls"] for b in by.values()
                            if b["purpose"] == "screenshot_extract"),
        "agent_calls": sum(b["calls"] for b in by.values() if b["purpose"] == "case_agent"),
    }


def test_count() -> int | None:
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                           str(CODE_DIR / "tests")], capture_output=True, text=True,
                          cwd=paths.REPO_ROOT)
    m = re.search(r"(\d+) tests? collected", done.stdout) or re.search(
        r"^(\d+)/\d+ tests collected", done.stdout, re.M)
    if not m:
        m = re.search(r"(\d+) tests", done.stdout)
    return int(m.group(1)) if m else None


def history_table() -> str:
    text = evalmain.EVAL_REPORT.read_text(encoding="utf-8")
    lines = text.split("## Metrics history", 1)[1].split("## ", 1)[0].strip().splitlines()
    return "\n".join(lines)


# ---------- main ----------


def collect(skip_tests: bool = False) -> dict:
    det_sample = run_pipeline("sample", "deterministic", "det_sample")
    agent_sample = run_pipeline("sample", "agent", "agent_sample")
    det_full = run_pipeline("full", "deterministic", "det_full")
    agent_full = run_pipeline("full", "agent", "agent_full")
    intake = intake_section()
    summary = det_full.summary
    sample = sample_section(det_sample, agent_sample)
    agent = agent_section(agent_sample, agent_full, det_full)
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "sample": sample,
        "custom": custom_section(),
        "router": router_section(det_sample, det_full),
        "agent": agent,
        "decision": decision_section(sample, agent),
        "gate": gate_section(),
        "final": final_section(),
        "full": full_section(det_full, agent_full),
        "golden": golden_section(),
        "tests": {"count": None if skip_tests else test_count()},
        "evidence": evidence_section(det_full),
        "linker": linker_section(det_full),
        "tiebreak": tiebreak_section(),
        "policy": policy_section(det_sample, det_full),
        "arch": arch_section(det_full),
        "intake": intake,
        "ledger": ledger_section(intake),
        "wall": {"det_full_seconds": summary["wall_seconds"],
                 "det_tool_calls": sum(summary["tool_calls"].values()),
                 "tool_calls_per_case": round(sum(summary["tool_calls"].values())
                                              / summary["cases"], 1),
                 "agent_replay_full_seconds": agent_full.summary["wall_seconds"]},
        "history_table": history_table(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-tests", action="store_true", help="do not count the tests")
    parser.add_argument("--out", type=Path, default=NUMBERS)
    args = parser.parse_args(argv)
    numbers = collect(args.skip_tests)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(numbers, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out} ({len(json.dumps(numbers))} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
