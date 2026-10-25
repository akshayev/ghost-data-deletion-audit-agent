"""Build code.zip: runnable solution, prompts, configuration, README and the reports.

    python code/package.py [--out code.zip]

Included: README.md, requirements.txt, everything under code/ except run outputs and caches of
the tooling. Never included: dataset/, virtual environments, .env, git data, log.txt, nested
archives, __pycache__, code/evaluation/runs/. The LLM caches and agent checkpoints under
code/cache/ ARE included: they hold model answers only (no keys) and make the run reproducible
offline.
"""

import argparse
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOP = ("README.md", "requirements.txt")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git", "runs"}
EXCLUDE_NAMES = {".env", "log.txt", ".DS_Store"}
EXCLUDE_SUFFIXES = {".zip", ".tar", ".gz", ".pyc"}


def select_files(root: Path = REPO) -> list[Path]:
    """Repo-relative paths to ship, sorted."""
    chosen = [Path(n) for n in TOP if (root / n).exists()]
    for path in sorted((root / "code").rglob("*")):
        rel = path.relative_to(root)
        if (not path.is_file() or set(rel.parts) & EXCLUDE_DIRS or path.name in EXCLUDE_NAMES
                or path.suffix in EXCLUDE_SUFFIXES or path.name.endswith(".md.tmpl")):
            continue
        chosen.append(rel)
    return sorted(chosen)


def build(root: Path = REPO, out: Path | None = None) -> Path:
    out = out or root / "code.zip"
    files = select_files(root)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(root / rel, rel.as_posix())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = build(REPO, args.out)
    size = out.stat().st_size / 1e6
    print(f"wrote {out} ({len(select_files(REPO))} files, {size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
