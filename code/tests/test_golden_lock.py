"""Golden lock: the pipeline's outputs must stay byte-identical to code/evaluation/golden/.

If a decision change is ever intended, regenerate golden in a separate commit that says why.
"""

import hashlib
import subprocess
import sys

import pytest

from ghost import paths

GOLDEN = paths.CODE / "evaluation" / "golden"


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_golden_files_match_their_recorded_sha256():
    recorded = dict(
        reversed(line.split()) for line in (GOLDEN / "SHA256SUMS").read_text().splitlines()
    )
    for name, digest in recorded.items():
        assert _sha(GOLDEN / name) == digest, name


@pytest.mark.parametrize("split", ["full", "sample"])
def test_regenerated_output_is_byte_identical_to_golden(split, tmp_path):
    out = tmp_path / f"output_{split}.csv"
    done = subprocess.run(
        [sys.executable, str(paths.CODE / "main.py"), "--split", split, "--no-llm",
         "--mode", "deterministic", "--out", str(out)],
        capture_output=True, text=True, cwd=paths.REPO_ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == (GOLDEN / f"output_{split}.csv").read_bytes()


AGENT_GOLDEN = GOLDEN / "output_full_agent.csv"


@pytest.mark.skipif(not AGENT_GOLDEN.exists(), reason="agent mode not chosen for the submission")
def test_agent_mode_output_replays_byte_identical_from_checkpoints(tmp_path):
    """The submitted output.csv came from agent mode: replaying it from the committed
    checkpoints and caches (--no-llm: no network) must reproduce it exactly."""
    out = tmp_path / "output_full_agent.csv"
    done = subprocess.run(
        [sys.executable, str(paths.CODE / "main.py"), "--split", "full", "--no-llm",
         "--mode", "agent", "--out", str(out)],
        capture_output=True, text=True, cwd=paths.REPO_ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == AGENT_GOLDEN.read_bytes()
    root = paths.REPO_ROOT / "output.csv"  # the submission file (not tracked in git)
    if root.exists():
        assert root.read_bytes() == AGENT_GOLDEN.read_bytes()
