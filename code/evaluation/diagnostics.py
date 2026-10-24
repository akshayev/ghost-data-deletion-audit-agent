"""Evaluation-only diagnostics (may read sample_output.csv; never imported by ghost).

    python code/evaluation/diagnostics.py

Parse recall = gold resource_ids in sample_output.csv that appear in at least
one parsed Observation (non-PNG evidence only until P4).
"""

import csv
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from ghost import paths  # noqa: E402
from ghost.parsers.registry import parse_all  # noqa: E402

GOLD = paths.DATASET / "sample_output.csv"


def gold_resource_ids() -> set[str]:
    with open(GOLD, encoding="utf-8", newline="") as f:
        return {
            s["resource_id"] for row in csv.DictReader(f) for s in json.loads(row["steps_json"])
        }


def parse_recall() -> tuple[int, int, list[str]]:
    gold = gold_resource_ids()
    seen = {o.resource_id for o in parse_all()[0]}
    missing = sorted(gold - seen)
    return len(gold) - len(missing), len(gold), missing


def main() -> int:
    found, total, missing = parse_recall()
    print(f"parse recall: {found}/{total} gold resources appear in >=1 observation")
    print(f"missing ({len(missing)}): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
