"""`main.py explain <case_id>`: identity, links, treatments, step reasons, agent calls."""

import csv
import json
import subprocess
import sys

import pytest

from ghost import config as cfgmod
from ghost import explain, paths

GOLDEN = paths.CODE / "evaluation" / "golden" / "output_sample.csv"


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    return cfgmod.load_config(cli={"runs_dir": tmp_path_factory.mktemp("runs"),
                                   "mode": "deterministic"})


def golden_steps(case_id):
    for row in csv.DictReader(open(GOLDEN, encoding="utf-8", newline="")):
        if row["case_id"] == case_id:
            return row["deletion_status"], json.loads(row["steps_json"])


@pytest.mark.parametrize("case_id", ["case_05", "case_11"])
def test_explain_covers_every_section_and_every_step(cfg, case_id):
    text = explain.explain(case_id, cfg)
    status, steps = golden_steps(case_id)
    for heading in ("## Request", "## Identity", "## Route", "## Resources", "## Steps",
                    "## Trace"):
        assert heading in text, heading
    assert f"status {status}" in text
    for s in steps:
        line = next(ln for ln in text.splitlines()
                    if ln.lstrip().startswith(f"{s['step_no']}. ")
                    and s["operation"] in ln and s["resource_id"] in ln)
        assert "because" in line or "why:" in line


def test_explain_shows_how_the_resource_was_linked_and_the_policy_source(cfg):
    text = explain.explain("case_11", cfg)
    resources = text.split("## Resources", 1)[1].split("## Steps", 1)[0]
    assert "linked via" in resources and "treatment" in resources
    assert "decisive file" in resources


def test_explain_identity_reports_counts_and_match_kind_not_values(cfg):
    text = explain.explain("case_05", cfg)
    identity = text.split("## Identity", 1)[1].split("##", 1)[0]
    assert "matched via" in identity and "emails" in identity and "@" not in identity


def test_explain_for_a_non_deletion_request_says_so(cfg):
    case_id = next(c for c, d in explain.intake_labels(cfg).items() if d == "NOT_DELETION")
    text = explain.explain(case_id, cfg)
    assert "NOT_APPLICABLE" in text and "does not authorize deletion" in text


def test_explain_unknown_case_is_an_error(cfg):
    with pytest.raises(explain.UnknownCase):
        explain.explain("case_999999", cfg)


def test_cli_explain_prints_the_report():
    done = subprocess.run([sys.executable, str(paths.CODE / "main.py"), "explain", "case_05",
                           "--mode", "deterministic"], capture_output=True, text=True,
                          cwd=paths.REPO_ROOT)
    assert done.returncode == 0, done.stderr
    assert "## Steps" in done.stdout and "case_05" in done.stdout
    bad = subprocess.run([sys.executable, str(paths.CODE / "main.py"), "explain", "nope"],
                         capture_output=True, text=True, cwd=paths.REPO_ROOT)
    assert bad.returncode != 0 and "unknown case" in bad.stderr
