"""The agent-facing tools (get_case_brief ... escalate_case) and the deterministic gate."""

import json

import pytest

from ghost import config as cfgmod
from ghost.orchestrator import Orchestrator

AGENT_TOOLS = ("get_case_brief", "list_candidate_resources", "inspect_resource",
               "get_ambiguities", "propose_plan", "submit_case", "escalate_case")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cfg = cfgmod.load_config(cli={"runs_dir": tmp_path_factory.mktemp("runs"), "no_llm": True,
                                  "mode": "deterministic"})
    return Orchestrator(cfg).run("sample", write=False)


def call(run, name, **kw):
    return run.registry.call(name, _agent="test", **kw)


def tokens(result) -> int:
    return len(json.dumps(result)) // 4


def routed(run):
    return [c for c, case in run.cases.items() if case.route == "AGENT"]


def test_agent_tools_are_registered_with_model_facing_descriptions(run):
    names = {t.name for t in run.registry.list()}
    assert set(AGENT_TOOLS) <= names
    for name in AGENT_TOOLS:
        assert len(run.registry.tools[name].description) > 40


def test_brief_wraps_request_text_as_untrusted_and_gives_counts_only(run):
    brief = call(run, "get_case_brief", case_id="case_25")
    text = brief["request_text"]
    assert text.startswith("<UNTRUSTED_CUSTOMER_TEXT>") and text.endswith(
        "</UNTRUSTED_CUSTOMER_TEXT>")
    assert brief["intake"] == "DELETION"
    assert set(brief["identity"]) >= {"user_ids", "emails", "phones", "hashes", "matched_via"}
    assert all(isinstance(v, int) for k, v in brief["identity"].items() if k != "matched_via")


def test_ambiguities_and_candidates_for_the_analytics_tie(run):
    points = call(run, "get_ambiguities", case_id="case_25")
    assert points and all(p["options"] == ["INCLUDE", "EXCLUDE"] for p in points)
    cands = call(run, "list_candidate_resources", case_id="case_25")
    by_id = {c["resource_id"]: c for c in cands["resources"]}
    for p in points:
        assert by_id[p["resource_id"]]["mandatory"] is False
    assert any(c["mandatory"] for c in by_id.values())


def test_inspect_unknown_resource_is_an_error_message_for_the_model(run):
    out = call(run, "inspect_resource", case_id="case_25", resource_id="account_99999999")
    assert "error" in out and "list_candidate_resources" in out["error"]


def test_inspect_shows_only_evidence_up_to_audit_at(run):
    point = call(run, "get_ambiguities", case_id="case_25")[0]
    out = call(run, "inspect_resource", case_id="case_25", resource_id=point["resource_id"])
    audit_at = run.cases["case_25"].request.audit_at_raw
    assert all(o["captured_at"] <= audit_at for o in out["observations"])
    assert all("offset_hours_from_reported_at" in a for a in out["activities"])


def test_default_decisions_reproduce_the_deterministic_plan(run):
    for case_id in routed(run):
        plan = call(run, "propose_plan", case_id=case_id, decisions=[])
        assert plan["verifier"]["ok"], plan
        stored = run.env.plans[plan["plan_id"]]
        case = run.cases[case_id]
        assert (stored["status"], stored["steps"]) == (case.status, case.steps)
        assert plan["changes_vs_default"] == []


def test_excluding_a_mandatory_resource_is_rejected(run):
    mandatory = next(c for c in call(run, "list_candidate_resources", case_id="case_25")[
        "resources"] if c["mandatory"])
    plan = call(run, "propose_plan", case_id="case_25",
                decisions=[{"ambiguity_id": mandatory["resource_id"], "choice": "EXCLUDE"}])
    assert plan["plan_id"] is None and not plan["verifier"]["ok"]
    assert "mandatory" in " ".join(plan["verifier"]["errors"])


def test_unknown_ambiguity_id_and_bad_choice_are_rejected(run):
    plan = call(run, "propose_plan", case_id="case_25",
                decisions=[{"ambiguity_id": "A99", "choice": "INCLUDE"}])
    assert not plan["verifier"]["ok"] and "unknown" in plan["verifier"]["errors"][0]
    plan = call(run, "propose_plan", case_id="case_25",
                decisions=[{"ambiguity_id": "A1", "choice": "MAYBE"}])
    assert not plan["verifier"]["ok"]


def test_include_of_a_tie_candidate_adds_its_steps(run):
    point = call(run, "get_ambiguities", case_id="case_25")[0]
    plan = call(run, "propose_plan", case_id="case_25",
                decisions=[{"ambiguity_id": point["id"], "choice": "INCLUDE"}])
    assert plan["verifier"]["ok"]
    stored = run.env.plans[plan["plan_id"]]
    assert point["resource_id"] in {s["resource_id"] for s in stored["steps"]}
    assert plan["changes_vs_default"] == [f"{point['id']} {point['resource_id']}: INCLUDE"]


def test_submit_needs_a_verifier_passed_plan_from_this_case(run):
    out = call(run, "submit_case", case_id="case_25", plan_id="case_25:P999", rationale="x")
    assert out["accepted"] is False
    other = call(run, "propose_plan", case_id=routed(run)[-1], decisions=[])
    out = call(run, "submit_case", case_id="case_25", plan_id=other["plan_id"], rationale="x")
    assert out["accepted"] is False  # a plan of another case
    plan = call(run, "propose_plan", case_id="case_25", decisions=[])
    long = " ".join(["word"] * 61)
    assert call(run, "submit_case", case_id="case_25", plan_id=plan["plan_id"],
                rationale=long)["accepted"] is False
    ok = call(run, "submit_case", case_id="case_25", plan_id=plan["plan_id"],
              rationale="Defaults kept: no evidence ties the tie candidates to this case.")
    assert ok["accepted"] is True and run.env.submissions["case_25"]["plan_id"] == plan["plan_id"]


def test_escalate_records_the_reason(run):
    out = call(run, "escalate_case", case_id="case_26", reason="evidence is contradictory")
    assert out["accepted"] and run.env.escalations["case_26"] == "evidence is contradictory"


def test_every_agent_tool_result_stays_under_1000_tokens(run):
    for case_id in routed(run):
        assert tokens(call(run, "get_case_brief", case_id=case_id)) < 1000
        assert tokens(call(run, "list_candidate_resources", case_id=case_id)) < 1000
        points = call(run, "get_ambiguities", case_id=case_id)
        assert tokens(points) < 1000
        for p in points:
            out = call(run, "inspect_resource", case_id=case_id, resource_id=p["resource_id"])
            assert tokens(out) < 1000
        assert tokens(call(run, "propose_plan", case_id=case_id, decisions=[])) < 1000
