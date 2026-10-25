"""CaseInvestigatorAgent loop, checkpoints, and the global consistency gate (mocked model)."""

import json
import re

import pytest

from ghost import ambiguity, llm
from ghost import config as cfgmod
from ghost.agents.case_investigator import CaseInvestigatorAgent
from ghost.orchestrator import Orchestrator

CASE = "case_25"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cfg = cfgmod.load_config(cli={"runs_dir": tmp_path_factory.mktemp("runs"), "no_llm": True,
                                  "mode": "deterministic"})
    return Orchestrator(cfg).run("sample", write=False)


_ids = iter(range(10**6))


def reply(*calls, content=""):
    """calls: (name, args_dict_or_raw_string) tuples."""
    tool_calls = []
    for name, args in calls:
        raw = args if isinstance(args, str) else json.dumps(args)
        tool_calls.append(llm.parse_tool_call(f"call_{next(_ids)}", name, raw))
    return llm.ToolReply(content, tuple(tool_calls), "groq", "fake-model", False, 100, 10)


def last_tool_result(messages, name=None) -> dict:
    for m in reversed(messages):
        if m["role"] == "tool":
            return json.loads(m["content"])
    return {}


def plan_id_from(messages) -> str:
    for m in reversed(messages):
        if m["role"] == "tool" and '"plan_id"' in m["content"]:
            found = json.loads(m["content"]).get("plan_id")
            if found:
                return found
    return "none"


class Script:
    """A fake model: each step is a ToolReply or a callable(messages) -> ToolReply."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.seen: list[list[dict]] = []

    def __call__(self, messages, tools, **kw):
        self.seen.append([dict(m) for m in messages])
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step


def agent(run, chat, tmp_path, **kw):
    return CaseInvestigatorAgent(run.registry, chat=chat, checkpoint_dir=tmp_path, **kw)


def brief_and_points(case=CASE):
    return reply(("get_case_brief", {"case_id": case}), ("get_ambiguities", {"case_id": case}))


def propose(decisions=(), case=CASE):
    return reply(("propose_plan", {"case_id": case, "decisions": list(decisions)}))


def submit(case=CASE, rationale="No evidence ties the candidates to this case; defaults kept."):
    return lambda msgs: reply(("submit_case", {"case_id": case, "plan_id": plan_id_from(msgs),
                                               "rationale": rationale}))


def fresh_case(run, case_id=CASE):
    case = run.cases[case_id]
    case.agent = None
    run.env.submissions.pop(case_id, None)
    return case


def test_happy_path_submits_and_checkpoints(run, tmp_path):
    point = run.env.ambiguities(CASE)[0]
    chat = Script(brief_and_points(),
                  reply(("inspect_resource", {"case_id": CASE, "resource_id": point.resource_id})),
                  propose(), submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    out = case.agent
    assert out["outcome"] == "AGENT_SUBMITTED" and out["tool_calls"] == 5
    assert out["provider"] == "groq" and out["model"] == "fake-model"
    assert out["changes"] == [] and out["rationale"].startswith("No evidence")
    assert chat.seen[0][0]["role"] == "system" and "UNTRUSTED" in chat.seen[0][0]["content"]
    assert len(list(tmp_path.rglob(f"{CASE}.json"))) == 1


def test_checkpoint_resume_needs_no_model(run, tmp_path):
    chat = Script(brief_and_points(), propose(), submit())
    agent(run, chat, tmp_path).run(fresh_case(run))
    never = Script(RuntimeError("the model must not be called on resume"))
    case = agent(run, never, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "AGENT_SUBMITTED" and case.agent["replayed"] is True
    assert never.seen == [] and run.env.submissions[CASE]


def test_excluding_a_mandatory_resource_is_rejected_and_the_model_recovers(run, tmp_path):
    mandatory = next(r.resource_id for r in run.cases[CASE].linked
                     if r.resource_id not in {a.resource_id for a in run.env.ambiguities(CASE)})
    chat = Script(brief_and_points(),
                  propose([{"ambiguity_id": mandatory, "choice": "EXCLUDE"}]),
                  propose(), submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "AGENT_SUBMITTED" and case.agent["rejections"] == 1
    rejected = last_tool_result(chat.seen[2])
    assert rejected["verifier"]["ok"] is False and "mandatory" in rejected["verifier"]["errors"][0]


def test_hallucinated_id_gets_an_error_message_not_a_crash(run, tmp_path):
    chat = Script(brief_and_points(),
                  reply(("inspect_resource", {"case_id": CASE, "resource_id": "account_31337"})),
                  propose(), submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "AGENT_SUBMITTED"
    assert "unknown resource_id" in last_tool_result(chat.seen[2])["error"]


def test_other_case_id_is_refused(run, tmp_path):
    chat = Script(reply(("get_case_brief", {"case_id": "case_01"})), propose(), submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert "only work on case_25" in last_tool_result(chat.seen[1])["error"]
    assert case.agent["outcome"] == "AGENT_SUBMITTED"


def test_submit_without_propose_is_not_accepted(run, tmp_path):
    chat = Script(brief_and_points(),
                  reply(("submit_case", {"case_id": CASE, "plan_id": "case_25:P404",
                                         "rationale": "done"})),
                  propose(), submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    first = last_tool_result(chat.seen[2])
    assert first["accepted"] is False and "propose_plan first" in first["error"]
    assert case.agent["outcome"] == "AGENT_SUBMITTED"


def test_iteration_cap_falls_back_to_deterministic(run, tmp_path):
    chat = Script(*[reply(("get_case_brief", {"case_id": CASE}))] * 9)
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "FALLBACK_TOOL_CAP" and CASE not in run.env.submissions


def test_too_many_verifier_rejections_fall_back(run, tmp_path):
    bad = propose([{"ambiguity_id": "A99", "choice": "INCLUDE"}])
    chat = Script(bad, bad, bad)
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "FALLBACK_REJECTED" and case.agent["rejections"] == 3


def test_malformed_arguments_are_reported_back_to_the_model(run, tmp_path):
    chat = Script(reply(("get_case_brief", '{"case_id": ')), brief_and_points(), propose(),
                  submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert "invalid JSON" in last_tool_result(chat.seen[1])["error"]
    assert case.agent["outcome"] == "AGENT_SUBMITTED"


def test_prose_instead_of_tool_calls_is_nudged(run, tmp_path):
    chat = Script(reply(content="I think it is complete."), brief_and_points(), propose(),
                  submit())
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "AGENT_SUBMITTED"
    assert any("tool calls" in m["content"] for m in chat.seen[1] if m["role"] == "user")


def test_escalation_keeps_the_deterministic_plan(run, tmp_path):
    chat = Script(reply(("escalate_case", {"case_id": CASE, "reason": "contradictory"})))
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "AGENT_ESCALATED"


def test_quota_exhaustion_falls_back_and_writes_no_checkpoint(run, tmp_path):
    chat = Script(llm.QuotaExhausted("all models hit the daily quota"))
    case = agent(run, chat, tmp_path).run(fresh_case(run))
    assert case.agent["outcome"] == "FALLBACK_QUOTA"
    assert not list(tmp_path.rglob("*.json"))  # unfinished: a rerun resumes it


def test_global_consistency_reverts_both_cases_of_a_conflict(run):
    lk = run.env.linker
    rid = ambiguity.case_ambiguities(lk, CASE, run.cases[CASE].linked)[0].resource_id
    plans = {}
    for c in ("case_25", "case_26"):
        resources = [r.resource_id for r in run.cases[c].linked] + [rid]
        plans[c] = {"resources": resources, "included": [rid]}
    conflicts = ambiguity.global_conflicts(lk, plans)
    assert conflicts == [(rid, ("case_25", "case_26"))]
    assert ambiguity.global_conflicts(lk, {"case_25": plans["case_25"]}) == []


def test_orchestrator_agent_mode_reverts_conflicts_and_logs_decisions(tmp_path):
    """Every routed case INCLUDEs its first tie candidate: case_25 and case_26 claim the same
    analytics resource, so both are reverted to the deterministic plan."""

    def model(messages, tools, **kw):
        case_id = re.search(r"case_\d+", messages[1]["content"]).group(0)
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            return brief_and_points(case_id)
        if "plan_id" in results[-1] and results[-1].get("verifier", {}).get("ok"):
            return submit(case_id, "Tie candidate included for the test.")(messages)
        points = next(r for r in results if isinstance(r, list))
        pick = [{"ambiguity_id": p["id"], "choice": "INCLUDE"} for p in points
                if p["kind"] == "tie_candidate"][:1]
        return propose(pick, case_id)

    cfg = cfgmod.load_config(cli={"runs_dir": tmp_path / "runs", "no_llm": False,
                                  "mode": "agent"})
    cfg_ckpt = cfgmod.Config(**{**cfg.__dict__,
                                "agent": {**cfg.agent, "checkpoint_dir": str(tmp_path / "ck"),
                                          "accepted_models": None}})  # gate tested separately
    result = Orchestrator(cfg_ckpt, chat=model).run("sample", write=False)
    det = Orchestrator(cfgmod.load_config(cli={"runs_dir": tmp_path / "r2", "no_llm": True,
                                              "mode": "deterministic"})).run("sample",
                                                                             write=False)
    for c in ("case_25", "case_26"):
        assert result.cases[c].agent["outcome"] == "AGENT_SUBMITTED"
        assert result.cases[c].agent["reverted"] == "global_conflict"
        assert result.cases[c].steps == det.cases[c].steps
    summary = result.summary
    assert summary["mode"] == "agent" and summary["agent"]["conflicts"]
    assert summary["router"]["AGENT"] == sum(1 for c in result.cases.values()
                                             if c.route == "AGENT")
    rows = (tmp_path / "runs" / "agent_decisions.csv").read_text().splitlines()
    assert rows[0].startswith("case_id,") and len(rows) == 1 + summary["router"]["AGENT"]


def test_offline_without_checkpoint_is_fallback_quota(run, tmp_path):
    chat = Script(llm.CacheMiss("no cached reply (offline)"))
    case = agent(run, chat, tmp_path, offline=True).run(fresh_case(run))
    assert case.agent["outcome"] == "FALLBACK_QUOTA"
    assert not list(tmp_path.rglob("*.json"))


def _replay_config(tmp_path, accepted, ckpt):
    cfg = cfgmod.load_config(cli={"runs_dir": tmp_path / "runs", "no_llm": True})
    return cfgmod.Config(**{**cfg.__dict__, "agent": {**cfg.agent, "checkpoint_dir": str(ckpt),
                                                      "accepted_models": accepted}})


def test_decisions_from_unvalidated_models_fall_back(run, tmp_path):
    """Checkpoints made by a model outside agent.accepted_models are not trusted."""
    def model(messages, tools, **kw):
        case_id = re.search(r"case_\d+", messages[1]["content"]).group(0)
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            return brief_and_points(case_id)
        if "plan_id" in results[-1]:
            return submit(case_id)(messages)
        return propose((), case_id)

    ckpt = tmp_path / "ck"
    live = cfgmod.load_config(cli={"runs_dir": tmp_path / "r1", "no_llm": False, "mode": "agent"})
    live = cfgmod.Config(**{**live.__dict__, "agent": {**live.agent, "checkpoint_dir": str(ckpt),
                                                       "only_cases": ["case_25"]}})
    Orchestrator(live, chat=model).run("sample", write=False)  # checkpoint by "fake-model"
    trusted = Orchestrator(_replay_config(tmp_path, ["fake-model"], ckpt)).run("sample",
                                                                                write=False)
    assert trusted.cases["case_25"].agent["outcome"] == "AGENT_SUBMITTED"
    distrusted = Orchestrator(_replay_config(tmp_path, ["other-model"], ckpt)).run(
        "sample", write=False)
    out = distrusted.cases["case_25"].agent
    assert out["outcome"] == "FALLBACK_UNVALIDATED_MODEL" and out["unvalidated"] == ["fake-model"]
    # case_26 has no checkpoint here, but its whole conversation is in the shared reply cache
    # (cache-first replay): it replays, and is distrusted because its model is not accepted
    other = distrusted.cases["case_26"].agent
    assert other["outcome"] == "FALLBACK_UNVALIDATED_MODEL"
    assert other["unvalidated"] == ["openai/gpt-oss-120b"]


def test_default_mode_is_offline_agent_replay():
    cfg = cfgmod.load_config()
    assert cfg.mode == "agent" and cfg.agent_live is False
    assert cfgmod.load_config(cli={"mode": "agent"}).agent_live is True
    assert cfgmod.load_config(cli={"mode": "deterministic"}).mode == "deterministic"
