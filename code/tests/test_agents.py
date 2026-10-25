"""Agent architecture: config, tool registry, agents, orchestrator, traces, CLI."""

import ast
import json
import subprocess
import sys

import pytest

from ghost import config as cfgmod
from ghost import paths
from ghost.agents import ALL_AGENTS
from ghost.orchestrator import Orchestrator
from ghost.tools.registry import Tool, ToolInputError, ToolRegistry, build_registry

AGENT_DIR = paths.CODE / "ghost" / "agents"
FORBIDDEN = ("ghost.linker", "ghost.state", "ghost.policy", "ghost.planner", "ghost.parsers")


# ---------- config ----------


def test_config_precedence_cli_over_env_over_yaml_over_defaults(tmp_path, monkeypatch):
    yml = tmp_path / "config.yaml"
    yml.write_text("linker:\n  tiebreak: ORDER\ntrace: false\n", encoding="utf-8")
    monkeypatch.delenv("GHOST_TIEBREAK", raising=False)
    assert cfgmod.load_config(yml).tiebreak == "ORDER"  # yaml over default
    assert cfgmod.load_config(tmp_path / "missing.yaml").tiebreak == "SKIP_MULTI"  # default
    monkeypatch.setenv("GHOST_TIEBREAK", "SKIP_ALL")
    assert cfgmod.load_config(yml).tiebreak == "SKIP_ALL"  # env over yaml
    assert cfgmod.load_config(yml, cli={"tiebreak": "REQUESTED"}).tiebreak == "REQUESTED"
    assert cfgmod.load_config(yml).trace is False


def test_shipped_config_reproduces_the_current_defaults():
    c = cfgmod.load_config()
    assert c.dataset == paths.REPO_ROOT / "dataset"
    assert c.tiebreak == "SKIP_MULTI" and c.trace is True
    assert c.linker["default_window"] == [-1.5, 2.5]
    assert "key" not in json.dumps(c.llm).lower()  # no secrets in config


# ---------- registry ----------


def _echo_registry():
    reg = ToolRegistry()
    reg.register(Tool(
        name="echo", description="echo a word",
        input_schema={"type": "object", "properties": {"word": {"type": "string"},
                      "times": {"type": "integer"}}, "required": ["word"],
                      "additionalProperties": False},
        output_schema={"type": "string"}, fn=lambda word, times=1: word * times,
    ))
    return reg


def test_registry_validates_input_and_traces_calls():
    reg = _echo_registry()
    assert reg.call("echo", word="ab", times=2) == "abab"
    for bad in ({"word": 3}, {"times": 2}, {"word": "a", "extra": 1}, {"word": "a", "times": "2"}):
        with pytest.raises(ToolInputError):
            reg.call("echo", **bad)
    with pytest.raises(KeyError):
        reg.call("nope")
    assert reg.counts["echo"] == 1  # only the valid call ran


def test_bound_caller_rejects_undeclared_tools():
    reg = _echo_registry()
    with pytest.raises(PermissionError):
        reg.bind("SomeAgent", allowed=()).call("echo", word="x")


def test_registry_lists_every_tool_with_schemas():
    reg = build_registry()
    names = {t.name for t in reg.list()}
    assert {"classify_request", "resolve_identity", "search_evidence", "get_observations",
            "get_coverage", "get_activities", "link_resources", "assess_resource", "plan_steps",
            "validate_row", "extract_screenshot"} <= names
    for t in reg.list():
        assert t.description and t.input_schema["type"] == "object" and t.output_schema
    md = reg.describe()
    assert md.startswith("| tool |") and all(f"`{n}`" in md for n in names)


# ---------- agents ----------


def test_agents_never_import_domain_modules():
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" for a in node.names]
            for n in names:
                assert not n.startswith(FORBIDDEN), f"{path.name} imports {n}"


def test_every_agent_declares_a_role_and_known_tools():
    tools = {t.name for t in build_registry().list()}
    assert len(ALL_AGENTS) == 9
    for agent in ALL_AGENTS:
        assert agent.name and agent.role.endswith(".")
        assert agent.tools_used and set(agent.tools_used) <= tools, agent.name


# ---------- orchestrator ----------


@pytest.fixture
def config(tmp_path):
    return cfgmod.load_config(cli={"runs_dir": tmp_path / "runs", "no_llm": True,
                                   "mode": "deterministic"})


def test_orchestrator_sample_run_matches_golden_and_traces_only_declared_tools(config, tmp_path):
    out = tmp_path / "out.csv"
    result = Orchestrator(config).run("sample", out=out)
    assert result.exit_code == 0 and result.failures == []
    golden = paths.CODE / "evaluation" / "golden" / "output_sample.csv"
    assert out.read_bytes() == golden.read_bytes()
    declared = {a.name: set(a.tools_used) for a in ALL_AGENTS}
    traces = list((config.runs_dir / "traces").glob("*.jsonl"))
    assert len(traces) >= 32
    for path in traces:
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["tool"]:
                assert event["tool"] in declared[event["agent"]], event
    summary = json.loads((config.runs_dir / "run_summary.json").read_text())
    assert summary["cases"] == 32 and summary["failures"] == []
    assert {"statuses", "agent_seconds", "tool_calls", "llm_calls", "router"} <= set(summary)
    assert summary["mode"] == "deterministic" and summary["agent"] == {}


def test_orchestrator_isolates_a_failing_case(config, tmp_path, monkeypatch):
    from ghost.agents.planner import PlannerAgent

    original = PlannerAgent.run

    def boom(self, case):
        if case.case_id == "case_07":
            raise RuntimeError("injected failure")
        return original(self, case)

    monkeypatch.setattr(PlannerAgent, "run", boom)
    out = tmp_path / "out.csv"
    result = Orchestrator(config).run("sample", out=out)
    assert result.exit_code != 0
    assert [f["case_id"] for f in result.failures] == ["case_07"]
    assert result.failures[0]["attempts"] == 2  # retried once
    done = [c for c in result.cases.values() if c.status]
    assert len(done) == 31 and "case_07" not in {c.case_id for c in done}
    assert not out.exists()  # never a partial output
    assert any("injected failure" in e for e in result.cases["case_07"].errors)


def test_orchestrator_retry_recovers_a_transient_failure(config, tmp_path, monkeypatch):
    from ghost.agents.planner import PlannerAgent

    original, seen = PlannerAgent.run, set()

    def flaky(self, case):
        if case.case_id == "case_07" and case.case_id not in seen:
            seen.add(case.case_id)
            raise RuntimeError("transient")
        return original(self, case)

    monkeypatch.setattr(PlannerAgent, "run", flaky)
    result = Orchestrator(config).run("sample", out=tmp_path / "out.csv")
    assert result.exit_code == 0 and result.failures == []
    assert result.cases["case_07"].status and result.cases["case_07"].errors


# ---------- CLI ----------


def _cli(*args):
    return subprocess.run([sys.executable, str(paths.CODE / "main.py"), *args],
                          capture_output=True, text=True, cwd=paths.REPO_ROOT)


def test_cli_tools_prints_and_writes_the_table():
    done = _cli("tools")
    assert done.returncode == 0 and "| tool |" in done.stdout
    doc = (paths.CODE / "docs" / "TOOLS.md").read_text(encoding="utf-8")
    assert "`link_resources`" in doc


def test_cli_run_subcommand_matches_golden(tmp_path):
    out = tmp_path / "s.csv"
    done = _cli("run", "--split", "sample", "--no-llm", "--mode", "deterministic",
                "--out", str(out))
    assert done.returncode == 0, done.stderr
    golden = paths.CODE / "evaluation" / "golden" / "output_sample.csv"
    assert out.read_bytes() == golden.read_bytes()


def test_cli_explain_is_a_placeholder():
    done = _cli("explain", "case_05")
    assert done.returncode == 0 and "P10" in done.stdout
