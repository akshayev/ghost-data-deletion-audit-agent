"""Handover TXT: "- <ts> | <system> | <resource_id> | <RECORD_TYPE> | <RESULT> | <subject>"."""

import re
from pathlib import Path

from ghost.extract import find_resource_ids
from ghost.parsers.common import FileCtx, FileParse, ledger_coverages, ledger_observation

ROW_RE = re.compile(
    r"^-\s*(?P<ts>\S+)\s*\|\s*(?P<system>[^|]+?)\s*\|\s*(?P<rid>[^|]+?)\s*\|"
    r"\s*(?P<rtype>[^|]+?)\s*\|\s*(?P<result>[^|]+?)\s*\|\s*(?P<subject>.*?)\s*$"
)
NO_IDENTIFIER = "no direct identifier"


def parse_text(ctx: FileCtx, path: Path) -> FileParse:
    result = FileParse(ctx.file_id, ctx.path, "text:unknown")
    lines = path.read_text(encoding="utf-8").splitlines()
    for n, line in enumerate(lines, 1):
        m = ROW_RE.match(line)
        if not m:
            if find_resource_ids(line):
                result.unparsed += 1
            continue
        result.family = "text:handover"
        subject = m["subject"]
        record = {
            "recorded_at": m["ts"],
            "system": m["system"],
            "resource_id": m["rid"],
            "record_type": m["rtype"],
            "result": m["result"],
            "subject_reference": None if subject.lower() == NO_IDENTIFIER else subject,
        }
        obs = ledger_observation(ctx, record, "handover", f"{ctx.path}#L{n}")
        if obs:
            result.observations.append(obs)
        else:
            result.unparsed += 1
    result.coverages += ledger_coverages(ctx, result.observations, "handover")
    return result
