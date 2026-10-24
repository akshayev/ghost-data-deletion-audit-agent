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
         "--out", str(out)],
        capture_output=True, text=True, cwd=paths.REPO_ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert out.read_bytes() == (GOLDEN / f"output_{split}.csv").read_bytes()
