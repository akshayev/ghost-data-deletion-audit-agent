"""Package code.zip and prove it reproduces the submission in a clean room.

    python code/evaluation/cleanroom.py [--reuse-venv DIR] [--skip-pytest]

1. Builds code.zip with the submission command:
   zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*"
       "*/.git/*" "*/.DS_Store" "code/evaluation/runs/*"
2. Asserts the contents: required files present; no .env, log.txt, dataset/ or nested archive;
   under 50 MB; no secret-looking strings.
3. Unzips into a new temp dir, copies dataset/ in, creates a fresh venv, installs
   code/requirements.txt (from the zip), removes every *_API_KEY variable, runs
   `python code/main.py run --split full`
   and compares output.csv's sha256 with the repository root output.csv; runs pytest there.
Writes code/evaluation/runs/cleanroom.json and prints a summary.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_JSON = REPO / "code" / "evaluation" / "runs" / "cleanroom.json"
ZIP_CMD = ["zip", "-qr", "code.zip", "README.md", "code", "-x", "*/__pycache__/*", "*/.venv/*",
           "*/venv/*", "*/node_modules/*", "*/.git/*", "*/.DS_Store", "code/evaluation/runs/*"]
REQUIRED = ["README.md", "code/main.py", "code/evaluation/evaluation_report.md",
            "code/evaluation/usage_report.md"]
REQUIRED_DIRS = ["code/prompts/", "code/cache/"]
ARCHIVES = (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")
SECRETS = [re.compile(rb"gsk_[A-Za-z0-9]{20,}"), re.compile(rb"AIza[0-9A-Za-z_\-]{30,}"),
           re.compile(rb"\bsk-[A-Za-z0-9\-]{20,}"), re.compile(rb"Bearer\s+[A-Za-z0-9._\-]{20,}"),
           re.compile(rb"api_key\s*=\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I)]
MAX_MB = 50


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key_values() -> list[bytes]:
    values = [v for k, v in os.environ.items() if k.endswith("_API_KEY") and len(v) > 8]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]*API_KEY)\s*=\s*['\"]?([^'\"\s]+)", line)
            if m and len(m.group(2)) > 8:
                values.append(m.group(2))
    return [v.encode() for v in values]


def build_zip() -> Path:
    target = REPO / "code.zip"
    target.unlink(missing_ok=True)
    subprocess.run(ZIP_CMD, cwd=REPO, check=True)
    return target


def inspect_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        secret_hits, value_hits = [], []
        values = key_values()
        for n in names:
            data = z.read(n)
            if any(p.search(data) for p in SECRETS):
                secret_hits.append(n)
            if any(v in data for v in values):
                value_hits.append(n)
    files = [n for n in names if not n.endswith("/")]
    checks = {
        "required files present": all(r in names for r in REQUIRED),
        "code/prompts/ and code/cache/ present": all(any(n.startswith(d) for n in files)
                                                     for d in REQUIRED_DIRS),
        "no .env": not any(Path(n).name == ".env" for n in names),
        "no log.txt": not any(Path(n).name == "log.txt" for n in names),
        "no dataset/": not any(n.startswith("dataset/") or "/dataset/" in n for n in names),
        "no nested archives": not any(n.lower().endswith(ARCHIVES) for n in names),
        f"size < {MAX_MB} MB": path.stat().st_size < MAX_MB * 1024 * 1024,
        "no secret-looking strings": not secret_hits,
        "no API key value": not value_hits,
    }
    top = {}
    for n in files:
        parts = n.split("/")
        key = "/".join(parts[:2]) if parts[0] == "code" and len(parts) > 2 else parts[0]
        top[key] = top.get(key, 0) + 1
    return {"checks": checks, "files": len(files), "bytes": path.stat().st_size,
            "sha256": sha(path), "by_dir": dict(sorted(top.items())),
            "secret_hits": secret_hits, "value_hit_count": len(value_hits)}


def clean_room(zip_path: Path, reuse_venv: Path | None, run_pytest: bool) -> dict:
    root = Path(tempfile.mkdtemp(prefix="ghost_cleanroom_"))
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(root)
    shutil.copytree(REPO / "dataset", root / "dataset")
    env = {k: v for k, v in os.environ.items()
           if not k.endswith("_API_KEY") and not k.startswith(("GROQ_", "GEMINI_", "GHOST_"))}
    env["HOME"] = str(root)  # no user-level config either
    started = time.time()
    reqs = str(root / "code" / "requirements.txt")
    install = "reused"
    if reuse_venv:
        venv = reuse_venv
    else:
        venv = root / ".venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        pip = subprocess.run([str(venv / "bin" / "pip"), "install", "-q", "--timeout", "10",
                              "--retries", "1", "-r", reqs], env=env, capture_output=True)
        install = "pip (PyPI)"
        if pip.returncode != 0 and shutil.which("uv"):
            # no route to PyPI here: same requirement list, from uv's local wheel cache
            shutil.rmtree(venv)
            subprocess.run(["uv", "venv", "-q", "--python", "3.11", str(venv)], check=True)
            subprocess.run(["uv", "pip", "install", "--offline", "-q", "-r", reqs,
                            "--python", str(venv / "bin" / "python")], check=True)
            install = "uv offline cache (PyPI unreachable)"
        elif pip.returncode != 0:
            raise RuntimeError("pip install failed and uv is not available")
    py = str(venv / "bin" / "python")
    run = subprocess.run([py, "code/main.py", "run", "--split", "full"], cwd=root, env=env,
                         capture_output=True, text=True)
    out = root / "output.csv"
    result = {
        "dir": str(root), "fresh_venv": reuse_venv is None, "install": install,
        "keys_in_env": sorted(
            k for k in env if k.endswith("_API_KEY")), "dotenv_present": (root / ".env").exists(),
        "run_exit": run.returncode, "run_stdout_tail": run.stdout.strip().splitlines()[-3:],
        "output_sha256": sha(out) if out.exists() else None,
        "root_output_sha256": sha(REPO / "output.csv"),
    }
    result["byte_identical"] = result["output_sha256"] == result["root_output_sha256"]
    if run_pytest:
        tests = subprocess.run([py, "-m", "pytest", "-q", "code/tests"], cwd=root, env=env,
                               capture_output=True, text=True)
        result["pytest_exit"] = tests.returncode
        result["pytest_tail"] = tests.stdout.strip().splitlines()[-1:]
    result["seconds"] = round(time.time() - started, 1)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reuse-venv", type=Path, help="skip venv creation (re-check only)")
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args(argv)
    zip_path = build_zip()
    info = inspect_zip(zip_path)
    for name, ok in info["checks"].items():
        print(f"{'PASS' if ok else 'FAIL'}  zip: {name}")
    print(f"zip: {info['files']} files, {info['bytes'] / 1e6:.2f} MB, sha256 {info['sha256']}")
    room = clean_room(zip_path, args.reuse_venv, not args.skip_pytest)
    print(f"clean room: {room['dir']} (fresh venv: {room['fresh_venv']} via {room['install']}, "
          f"API keys in env: "
          f"{room['keys_in_env'] or 'none'}, .env present: {room['dotenv_present']})")
    print(f"{'PASS' if room['run_exit'] == 0 else 'FAIL'}  run exit {room['run_exit']}: "
          f"{' | '.join(room['run_stdout_tail'])[:300]}")
    print(f"{'PASS' if room['byte_identical'] else 'FAIL'}  output.csv sha256 "
          f"{room['output_sha256']} vs root {room['root_output_sha256']}")
    if "pytest_exit" in room:
        print(f"{'PASS' if room['pytest_exit'] == 0 else 'FAIL'}  pytest: {room['pytest_tail']}")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"zip": info, "room": room}, indent=2) + "\n",
                        encoding="utf-8")
    ok = (all(info["checks"].values()) and room["run_exit"] == 0 and room["byte_identical"]
          and room.get("pytest_exit", 0) == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
