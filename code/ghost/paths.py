"""Repository-relative paths. Everything is resolved from this file's location,
so the code works from any working directory on Windows, macOS and Linux."""

import os
import sys
from pathlib import Path

# code/ghost/paths.py -> parents[0]=ghost, [1]=code, [2]=repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
CODE = REPO_ROOT / "code"
# The dataset root is configurable (GHOST_DATASET, or main.py --dataset) so the evaluation
# can run the same pipeline on small custom datasets; the default is <repo>/dataset.
DATASET = Path(os.environ.get("GHOST_DATASET") or REPO_ROOT / "dataset").resolve()
EVIDENCE = DATASET / "evidence"
OUTPUT = REPO_ROOT / "output.csv"
EVALUATION = CODE / "evaluation"
EVAL_RUNS = EVALUATION / "runs"


def use_dataset(path: Path | str) -> None:
    """Point every loader/parser at another dataset root and drop all cached results."""
    global DATASET, EVIDENCE
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"dataset directory not found: {root}")
    DATASET, EVIDENCE = root, root / "evidence"
    for name, module in list(sys.modules.items()):
        if name == "ghost" or name.startswith("ghost."):
            for attr in vars(module).values():
                if callable(getattr(attr, "cache_clear", None)):
                    attr.cache_clear()
