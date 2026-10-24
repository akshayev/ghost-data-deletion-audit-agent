"""Parse-coverage report: python code/ghost/parsers/report.py

Writes code/evaluation/runs/parse_coverage.md with one row per evidence file.
"""

import sys
from pathlib import Path

if __name__ == "__main__":  # running as a script: import from code/, not this folder
    sys.path[0] = str(Path(__file__).resolve().parents[2])

from ghost import paths  # noqa: E402
from ghost.parsers.registry import parse_all, parse_results  # noqa: E402

REPORT = paths.EVAL_RUNS / "parse_coverage.md"


def coverage_rows() -> list[dict]:
    rows = []
    for fid, r in parse_results().items():
        rows.append(
            {
                "file_id": fid,
                "path": r.path,
                "family": r.family,
                "observations": len(r.observations),
                "coverage": len(r.coverages),
                "resources": len({o.resource_id for o in r.observations}),
                "facts": len(r.facts),
                "unparsed": r.unparsed,
            }
        )
    return rows


def render_report(rows: list[dict]) -> str:
    observations, coverages, facts = parse_all()
    lines = [
        "# Evidence parse coverage",
        "",
        "| file_id | path | family | obs | coverage | resources | facts | unparsed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['file_id']} | {r['path']} | {r['family']} | {r['observations']} | "
            f"{r['coverage']} | {r['resources']} | {r['facts']} | {r['unparsed']} |"
        )
    empty = [r for r in rows if r["observations"] == 0]
    lines += [
        "",
        f"- Total observations: {len(observations)}",
        f"- Total coverage entries: {len(coverages)}",
        f"- File facts: {len(facts)}",
        f"- Distinct resources: {len({o.resource_id for o in observations})}",
        f"- Unparsed units: {sum(r['unparsed'] for r in rows)}",
        f"- Files with 0 observations: {len(empty)}",
    ]
    for r in empty:
        lines.append(f"  - {r['file_id']} ({r['family']}, coverage={r['coverage']})")
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = coverage_rows()
    md = render_report(rows)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
