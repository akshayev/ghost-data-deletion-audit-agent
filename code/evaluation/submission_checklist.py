"""Write code/docs/SUBMISSION_CHECKLIST.md: every AGENTS.md §6 requirement and every item of the
starter README's "Before submitting" list, PASS/FAIL, with the command and its output.

    python code/evaluation/submission_checklist.py

Run after code/evaluation/cleanroom.py (it reads code/evaluation/runs/cleanroom.json) and after
the reports are rendered. Exit code 1 if any item fails.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "code"
OUT = CODE / "docs" / "SUBMISSION_CHECKLIST.md"
CLEANROOM = CODE / "evaluation" / "runs" / "cleanroom.json"
NUMBERS = CODE / "evaluation" / "runs" / "numbers.json"


def sh(cmd: list[str]) -> tuple[int, str]:
    done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    return done.returncode, (done.stdout + done.stderr).strip()


def tail(text: str, n: int = 3) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " / ".join(lines[-n:])[:400] or "(no output)"


def main() -> int:
    items: list[tuple[str, str, bool, str, str]] = []  # section, requirement, ok, command, output

    def add(section, requirement, ok, command, output):
        items.append((section, requirement, bool(ok), command, output))

    py = sys.executable
    rc, val = sh([py, "code/evaluation/validate_submission.py", "output.csv"])
    checks = {line[6:].split("  [")[0]: line.startswith("PASS")
              for line in val.splitlines() if line.startswith(("PASS", "FAIL"))}
    vcmd = "python code/evaluation/validate_submission.py output.csv"

    def v(fragment: str) -> bool:
        hits = [ok for name, ok in checks.items() if fragment in name]
        return bool(hits) and all(hits)

    room = json.loads(CLEANROOM.read_text()) if CLEANROOM.exists() else None
    numbers = json.loads(NUMBERS.read_text()) if NUMBERS.exists() else {}
    zip_checks = room["zip"]["checks"] if room else {}
    rr = room["room"] if room else {}

    # ---- AGENTS.md §6 ----
    rc_g, grep_sample = sh(["grep", "-rnE", "[\"']sample_output\\.csv[\"']", "code/ghost",
                            "code/main.py"])
    add("6.1 Dataset", "Predictions use only participant-facing files under dataset/; "
        "sample_output.csv is read only by code/evaluation/", rc_g == 1,
        "grep -rnE \"[\\\"']sample_output\\.csv[\\\"']\" code/ghost code/main.py",
        grep_sample or "(no string literal naming sample_output.csv)")
    add("6.1 / 6.3 Output", "Exactly 518 rows, one per case_id of deletion_requests.csv, same "
        "order, no duplicates", v("exactly 518 rows"), vcmd, tail(val, 1))
    add("6.2 Time", "Only evidence at or before audit_at is cited", v("audit_at"), vcmd,
        next((ln for ln in val.splitlines() if "audit_at" in ln), ""))
    for frag, req in [
        ("header", "Header exactly case_id,deletion_status,steps_json"),
        ("statuses", "deletion_status in COMPLETE / INCOMPLETE / NOT_APPLICABLE"),
        ("parses", "steps_json is a valid JSON array"),
        ("7 keys", "Each step has exactly the 7 fields"),
        ("step_no", "Steps numbered consecutively from 1"),
        ("phases", "Phases ordered FINDING, REMEDIATION, VERIFICATION"),
        ("if and only if", "NOT_APPLICABLE with [] (and only then)"),
    ]:
        add("6.3 Output", req, v(frag), vcmd,
            next((ln for ln in val.splitlines() if frag in ln), ""))
    add("6.4 Operations", "Only the allowed phase/operation/state combinations",
        v("triples"), vcmd, next((ln for ln in val.splitlines() if "triples" in ln), ""))
    add("6.6 Evidence", "COMPLETE: only verification steps, each citing evidence", v("COMPLETE:"),
        vcmd, next((ln for ln in val.splitlines() if "COMPLETE:" in ln), ""))
    add("6.6 Evidence", "INCOMPLETE: findings cite evidence; remediation and future verification "
        "use \"\"", v("INCOMPLETE:"), vcmd,
        next((ln for ln in val.splitlines() if "INCOMPLETE:" in ln), ""))
    add("6.6 Evidence", "Every cited id is a real file_id / activity_id / audit_id", v("exists"),
        vcmd, next((ln for ln in val.splitlines() if "exists" in ln), ""))
    add("6.3 / Rules", "Within a phase: operation order, then system_id, then resource_id",
        v("within a phase"), vcmd,
        next((ln for ln in val.splitlines() if "within a phase" in ln), ""))
    add("6.5 Decisions", "Every remediated record has a verification of its final state",
        v("REMEDIATION has"), vcmd, next((ln for ln in val.splitlines() if "REMEDIATION has" in ln),
                                         ""))
    s = numbers.get("sample", {})
    add("6.5 Decisions", "Decision quality measured on the sample (status accuracy, step F1)",
        bool(s), "python code/evaluation/report_numbers.py",
        f"agent: status {s.get('agent', {}).get('status_acc')}, step F1 "
        f"{s.get('agent', {}).get('step_f1')}; deterministic step F1 "
        f"{s.get('det', {}).get('step_f1')}" if s else "numbers.json missing")
    add("6.7 Evaluable", "Runnable from the terminal with the README command; a clean run writes "
        "518 valid rows at the repository root", rr.get("run_exit") == 0,
        "python code/evaluation/cleanroom.py (clean room: python code/main.py run --split full)",
        " | ".join(rr.get("run_stdout_tail", []))[:300] if rr else "cleanroom.json missing")
    add("6.7 Evaluable", "Deterministic and reproducible: clean-room output is byte-identical to "
        "the submitted output.csv (no keys, fresh venv)", rr.get("byte_identical"),
        "python code/evaluation/cleanroom.py",
        f"clean room {str(rr.get('output_sha256'))[:16]} = root "
        f"{str(rr.get('root_output_sha256'))[:16]}; API keys in env: "
        f"{rr.get('keys_in_env') or 'none'}; .env present: {rr.get('dotenv_present')}")
    rc_c, case_lits = sh(["grep", "-rnE", "[\"']case_[0-9]+[\"']", "code/ghost", "code/main.py"])
    add("6.7 Evaluable", "No hardcoded golden labels or case_id literals in the logic",
        rc_c == 1, "grep -rnE \"[\\\"']case_[0-9]+[\\\"']\" code/ghost code/main.py",
        case_lits[:300] or "(no matches)")
    add("6.7 Evaluable", "Secrets only from environment variables; none in the package",
        zip_checks.get("no secret-looking strings") and zip_checks.get("no API key value")
        and zip_checks.get("no .env"), "python code/evaluation/cleanroom.py (zip scan)",
        f"secret-looking strings: {room['zip']['secret_hits'] if room else '?'}; key values: "
        f"{room['zip']['value_hit_count'] if room else '?'}; .env in zip: "
        f"{not zip_checks.get('no .env', False)}")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    add("6.7 Evaluable", "README has setup and run instructions",
        "pip install -r code/requirements.txt" in readme
        and "python code/main.py run --split full" in readme,
        "grep -n 'pip install\\|run --split full' README.md",
        "; ".join(ln.strip() for ln in readme.splitlines()
                  if "pip install" in ln or "run --split full" in ln)[:300])
    report = (CODE / "evaluation" / "evaluation_report.md").read_text(encoding="utf-8")
    needed = {"status accuracy": "Case-level deletion-status accuracy",
              "step and field accuracy": "Step-level, field-level",
              "sequence accuracy": "Sequence accuracy",
              "custom cases": "Custom evaluation cases",
              "failures and improvements": "Failures found and improvements made"}
    missing = [k for k, h in needed.items() if h not in report]
    custom = numbers.get("custom", {})
    add("6.8 Reports", "evaluation_report.md: status, step, field and sequence accuracy, >=3 "
        "custom cases, failures and improvements",
        not missing and custom.get("total", 0) >= 3 and "{{" not in report,
        "grep -c headings code/evaluation/evaluation_report.md",
        f"missing sections: {missing or 'none'}; custom cases {custom.get('passed')}/"
        f"{custom.get('total')} pass; placeholders left: {report.count('{{')}")
    usage = (CODE / "evaluation" / "usage_report.md").read_text(encoding="utf-8")
    want = ["provider", "model", "calls", "prompt tokens", "completion tokens",
            "Average per request", "Estimated cost", "overall"]
    gone = [w for w in want if w.lower() not in usage.lower()]
    add("6.8 Reports", "usage_report.md: providers and models, call counts, input/output "
        "tokens, total and per-request averages, estimated cost, per-model and overall totals",
        not gone and "(a) The full-dataset run that produced `output.csv`" in usage,
        "grep code/evaluation/usage_report.md", f"missing: {gone or 'none'}")
    for name, ok in zip_checks.items():
        add("6.9 Package", f"code.zip: {name}", ok, "python code/evaluation/cleanroom.py",
            f"{room['zip']['files']} files, {room['zip']['bytes'] / 1e6:.2f} MB" if room else "")
    rc_l, log = sh([py, "code/evaluation/audit_log.py"])
    add("6.9 Package", "log.txt follows AGENTS.md §5 (SESSION START, one tool= and model= per "
        "entry, no secrets)", rc_l == 0, "python code/evaluation/audit_log.py", tail(log, 4))
    rc_t, tests = sh([py, "-m", "pytest", "-q", "code/tests"])
    add("Quality", "Test suite (includes golden locks and the validator's mutation tests)",
        rc_t == 0, "python -m pytest -q code/tests", tail(tests, 1))
    if rr.get("pytest_tail"):
        add("Quality", "Test suite in the clean room", rr.get("pytest_exit") == 0,
            "python code/evaluation/cleanroom.py", " ".join(rr["pytest_tail"]))

    # ---- starter README "Before submitting" ----
    before = [
        ("output.csv has the exact header and all 518 rows sorted by case_id",
         v("header") and v("exactly 518 rows")),
        ("every steps_json is a valid JSON array with consecutive step numbers from 1",
         v("parses") and v("step_no")),
        ("findings cite valid evidence IDs; COMPLETE verification cites proving evidence",
         v("exists") and v("COMPLETE:") and v("INCOMPLETE:")),
        ("remediation and future verification steps use an empty evidence_id",
         v("INCOMPLETE:")),
        ("code.zip includes runnable code, README.md, evaluation_report.md and usage_report.md",
         zip_checks.get("required files present")),
        ("log.txt follows the AGENTS.md turn format", rc_l == 0),
        ("no API keys, credentials, real personal data or sensitive configuration included",
         zip_checks.get("no secret-looking strings") and zip_checks.get("no API key value")
         and zip_checks.get("no .env")),
    ]
    for req, ok in before:
        add("Before submitting", req, ok, "see the items above", "")

    passed = sum(ok for _, _, ok, _, _ in items)
    lines = ["# Submission checklist", "",
             f"Generated by `python code/evaluation/submission_checklist.py` on "
             f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}. "
             f"**{passed}/{len(items)} PASS.**", "",
             "Sources: AGENTS.md §6 (project contract) and the starter README's \"Before "
             "submitting\" list. Real personal data: the dataset is fictional (AGENTS.md §7) and "
             "nothing from outside it is used.", "",
             "| # | Section | Requirement | Result | Command | Output |",
             "|---|---|---|---|---|---|"]
    for i, (section, req, ok, cmd, out) in enumerate(items, 1):
        cell = out.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {section} | {req} | {'PASS' if ok else 'FAIL'} | `{cmd}` | "
                     f"{cell} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}: {passed}/{len(items)} PASS")
    for section, req, ok, _, _ in items:
        if not ok:
            print(f"FAIL  [{section}] {req}")
    return 0 if passed == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
