"""Repository-relative paths. Everything is resolved from this file's location,
so the code works from any working directory on Windows, macOS and Linux."""

from pathlib import Path

# code/ghost/paths.py -> parents[0]=ghost, [1]=code, [2]=repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
CODE = REPO_ROOT / "code"
DATASET = REPO_ROOT / "dataset"
EVIDENCE = DATASET / "evidence"
OUTPUT = REPO_ROOT / "output.csv"
EVALUATION = CODE / "evaluation"
EVAL_RUNS = EVALUATION / "runs"
