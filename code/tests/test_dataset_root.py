"""The dataset root is configurable (GHOST_DATASET or --dataset); nothing else changes."""

import csv
import os
import subprocess
import sys

from ghost import loaders, paths

CASES = paths.CODE / "evaluation" / "custom_cases"
MAIN = paths.CODE / "main.py"


def run(args, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(MAIN), *args], capture_output=True, text=True,
                          env=env, cwd=paths.REPO_ROOT)


def test_default_dataset_is_repo_dataset():
    assert paths.DATASET == paths.REPO_ROOT / "dataset"
    assert paths.EVIDENCE == paths.DATASET / "evidence"


def test_use_dataset_switches_paths_and_clears_caches(monkeypatch):
    real_n = len(loaders.load_requests("full"))
    monkeypatch.setattr(paths, "DATASET", paths.DATASET)
    monkeypatch.setattr(paths, "EVIDENCE", paths.EVIDENCE)
    try:
        paths.use_dataset(CASES / "replica_drift")
        assert paths.DATASET == CASES / "replica_drift"
        assert paths.EVIDENCE == CASES / "replica_drift" / "evidence"
        assert [r.case_id for r in loaders.load_requests("full")] == ["case_1"]
    finally:
        paths.use_dataset(paths.REPO_ROOT / "dataset")
    assert len(loaders.load_requests("full")) == real_n


def test_cli_dataset_flag_runs_a_custom_case(tmp_path):
    out = tmp_path / "out.csv"
    done = run(["--dataset", str(CASES / "non_deletion_keep_account"), "--split", "full",
                "--no-llm", "--out", str(out)])
    assert done.returncode == 0, done.stderr
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert [(r["case_id"], r["deletion_status"], r["steps_json"]) for r in rows] == [
        ("case_1", "NOT_APPLICABLE", "[]")
    ]


def test_env_var_selects_the_dataset(tmp_path):
    out = tmp_path / "out.csv"
    done = run(["--split", "full", "--no-llm", "--out", str(out)],
               {"GHOST_DATASET": str(CASES / "non_deletion_keep_account")})
    assert done.returncode == 0, done.stderr
    assert len(list(csv.DictReader(open(out, encoding="utf-8")))) == 1


def test_missing_dataset_is_a_clear_error(tmp_path):
    done = run(["--dataset", str(tmp_path / "nope"), "--split", "full", "--no-llm"])
    assert done.returncode != 0 and "dataset" in done.stderr.lower()
