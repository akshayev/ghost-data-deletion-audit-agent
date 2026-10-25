"""Report generators: placeholder rendering, cost maths, packaging rules, committed docs."""

import json
import re
import zipfile

import pytest

from docs import render
from evaluation import usage_report
from ghost import paths

REPO = paths.REPO_ROOT


# ---------- render ----------


def test_render_replaces_dotted_keys_and_leaves_other_text():
    numbers = {"a": {"b": "97.4%", "n": 32}, "t": "x"}
    assert render.render_text("F1 {{a.b}} over {{ a.n }} cases; {{t}}", numbers) == (
        "F1 97.4% over 32 cases; x")


def test_render_fails_loudly_on_a_missing_key():
    with pytest.raises(render.MissingKey) as err:
        render.render_text("{{a.b}} and {{nope.x}}", {"a": {"b": 1}})
    assert "nope.x" in str(err.value)


def test_render_refuses_non_scalar_values():
    with pytest.raises(render.MissingKey):
        render.render_text("{{a}}", {"a": {"b": 1}})


def test_render_all_writes_the_md_beside_each_template(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "X.md.tmpl").write_text("# X {{v}}\n", encoding="utf-8")
    (tmp_path / "numbers.json").write_text(json.dumps({"v": 7}), encoding="utf-8")
    written = render.render_all(tmp_path, tmp_path / "numbers.json")
    assert [p.name for p in written] == ["X.md"]
    assert "# X 7" in (tmp_path / "sub" / "X.md").read_text(encoding="utf-8")
    assert "{{" not in (tmp_path / "sub" / "X.md").read_text(encoding="utf-8")


def test_every_template_placeholder_resolves_against_the_current_numbers():
    numbers_path = paths.EVAL_RUNS / "numbers.json"
    if not numbers_path.exists():
        pytest.skip("run python code/evaluation/report_numbers.py first")
    numbers = json.loads(numbers_path.read_text(encoding="utf-8"))
    for tmpl in render.templates(REPO):
        render.render_text(tmpl.read_text(encoding="utf-8"), numbers)  # raises if unresolved


def test_rendered_docs_contain_no_placeholder():
    rendered = [t.with_suffix("") for t in render.templates(REPO)]
    assert rendered, "no *.md.tmpl found"
    for md in rendered + [REPO / "code" / "evaluation" / "usage_report.md"]:
        assert md.exists(), f"{md} has not been rendered"
        assert "{{" not in md.read_text(encoding="utf-8"), md


# ---------- usage report ----------

PRICES = {"groq:m": {"input": 0.15, "output": 0.60}}


def test_cost_is_per_million_tokens():
    assert usage_report.cost(PRICES, "groq", "m", 2_000_000, 1_000_000) == pytest.approx(0.9)
    assert usage_report.cost(PRICES, "groq", "unpriced", 10, 10) is None


def test_usage_aggregation_counts_only_fresh_calls():
    rows = [
        {"provider": "groq", "model": "m", "purpose": "case_agent", "cache_hit": False,
         "prompt_tokens": 100, "completion_tokens": 10, "latency_ms": 500, "case_id": "c1"},
        {"provider": "groq", "model": "m", "purpose": "case_agent", "cache_hit": True,
         "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0, "case_id": "c1"},
        {"provider": "groq", "model": "m", "purpose": "case_agent", "cache_hit": False,
         "prompt_tokens": None, "completion_tokens": None, "latency_ms": None, "case_id": "c2"},
    ]
    (agg,) = usage_report.aggregate(rows)
    assert (agg["calls"], agg["prompt"], agg["completion"], agg["latency_ms"]) == (2, 100, 10, 500)


# ---------- packaging ----------


def test_package_selects_no_secret_dataset_or_cache_of_runs():
    import package

    files = [p.as_posix() for p in package.select_files(REPO)]
    assert "README.md" in files and "code/main.py" in files
    assert "code/evaluation/evaluation_report.md" in files
    assert "code/evaluation/usage_report.md" in files
    assert not any(f.startswith(("dataset/", ".venv/", ".git/")) for f in files)
    assert not any(re.search(r"(^|/)(\.env|log\.txt)$|\.zip$|__pycache__|/runs/", f)
                   for f in files)


def test_package_writes_a_zip_with_the_same_files(tmp_path):
    import package

    out = tmp_path / "code.zip"
    package.build(REPO, out)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert names == {p.as_posix() for p in package.select_files(REPO)}
