"""Audit log.txt against AGENTS.md §5 (read-only: it never edits the log).

    python code/evaluation/audit_log.py [log.txt]

Checks: SESSION START entries exist; every entry has exactly one non-empty tool= and model=
line with no placeholder, "unknown" or generic label; no secret-looking strings (and no value
of a *_API_KEY variable present in the environment or .env). Problems are reported with line
numbers; secret values are never printed.
"""

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENTRY = re.compile(r"^## \[(.+?)\] (.*)$")
GENERIC = {"", "ai", "llm", "assistant", "agent", "model", "unknown", "n/a", "none", "tbd",
           "claude", "gpt"}
SECRET_PATTERNS = [
    (re.compile(r"gsk_[A-Za-z0-9]{20,}"), "groq-style key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "google-style key"),
    (re.compile(r"\bsk-[A-Za-z0-9\-]{20,}"), "openai/anthropic-style key"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
    (re.compile(r"api_key\s*=\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I), "api_key assignment"),
]


def key_values() -> list[str]:
    values = [v for k, v in os.environ.items() if k.endswith("_API_KEY") and len(v) > 8]
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_]*API_KEY)\s*=\s*['\"]?([^'\"\s]+)", line)
            if m and len(m.group(2)) > 8:
                values.append(m.group(2))
    return values


def audit(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if ENTRY.match(line):
            entries.append((i, line))
    problems: list[str] = []
    bounds = [e[0] for e in entries] + [len(lines) + 1]
    for (start, header), end in zip(entries, bounds[1:], strict=False):
        body = lines[start:end - 1]
        for field in ("tool", "model"):
            found = [(start + 1 + j, ln) for j, ln in enumerate(body)
                     if ln.strip().startswith(f"{field}=")]
            if len(found) != 1:
                problems.append(f"line {start}: {len(found)} {field}= lines in entry "
                                f"{header[:60]!r}")
                continue
            n, ln = found[0]
            value = ln.split("=", 1)[1].strip()
            if value.lower() in GENERIC or "<" in value or ">" in value:
                problems.append(f"line {n}: {field}={value!r} is empty, generic or a placeholder")
    secrets = []
    values = key_values()
    for i, line in enumerate(lines, 1):
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(line):
                secrets.append(f"line {i}: looks like a {label}")
        if any(v in line for v in values):
            secrets.append(f"line {i}: contains the value of an API key variable")
    return {
        "lines": len(lines), "entries": len(entries),
        "session_starts": [n for n, h in entries if "SESSION START" in h],
        "problems": problems, "secrets": secrets,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else REPO / "log.txt"
    r = audit(path)
    print(f"{path}: {r['lines']} lines, {r['entries']} entries, "
          f"{len(r['session_starts'])} SESSION START (lines {r['session_starts']})")
    print(f"{'PASS' if r['session_starts'] else 'FAIL'}  SESSION START entries exist")
    print(f"{'PASS' if not r['problems'] else 'FAIL'}  every entry has exactly one valid tool= "
          f"and model= line" + "".join(f"\n      {p}" for p in r["problems"]))
    print(f"{'PASS' if not r['secrets'] else 'FAIL'}  no secrets"
          + "".join(f"\n      {p}" for p in r["secrets"]))
    return 0 if r["session_starts"] and not r["problems"] and not r["secrets"] else 1


if __name__ == "__main__":
    sys.exit(main())
