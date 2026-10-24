"""Log evidence: six line formats, grouped by trace id.

Formats (all verified in dataset/evidence/logs):
  kv       TS [LEVEL ]k=v k="v" ...                       (trace_id=, service=)
  bracket  TS [LEVEL] [service] [trace] event: k="v" ...
  pipe     TS | LEVEL | service | trace | event | k="v" ...
  json     TS {"severity":..,"service":..,"trace_id":..,"event":..}
  syslog   TS <pri>1 node service trace - - k="v" ...
  cloud    TS requestId=trace level=X function=service message="k=\\"v\\" ..."

Identity keys (lookup_key, match_key, correlation_key, ...) often sit on a
different line than resource_id but share the trace id, so every resource in a
trace group is linked to the identifiers of all lines in that group.
"""

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ghost.extract import find_resource_ids, link_keys_from
from ghost.loaders import parse_ts
from ghost.parsers.common import (
    IDENT_KEYS,
    LEDGER_ABSENT,
    LEDGER_PRESENT,
    FileCtx,
    FileParse,
    coverage,
    make_observation,
)
from ghost.systems import RESOURCE_ID_RE

KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
_TS = r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
FORMATS = (
    ("json", re.compile(_TS + r" (?P<body>\{.*\})$")),
    (
        "bracket",
        re.compile(
            _TS + r" \[(?P<level>\w+)\] \[(?P<service>[\w-]+)\] \[(?P<trace>[\w-]+)\]"
            r" (?P<event>\w+): (?P<body>.*)$"
        ),
    ),
    (
        "pipe",
        re.compile(
            _TS + r" \| (?P<level>\w+) \| (?P<service>[\w-]+) \| (?P<trace>[\w-]+)"
            r" \| (?P<event>\w+) \| (?P<body>.*)$"
        ),
    ),
    (
        "syslog",
        re.compile(
            _TS + r" <\d+>1 (?P<node>\S+) (?P<service>\S+) (?P<trace>\S+) - - (?P<body>.*)$"
        ),
    ),
    (
        "cloud",
        re.compile(
            _TS + r" requestId=(?P<trace>\S+) level=(?P<level>\w+) function=(?P<service>\S+)"
            r' message="(?P<body>.*)"$'
        ),
    ),
    ("kv", re.compile(_TS + r" (?:(?P<level>INFO|DEBUG|WARN|ERROR) )?(?P<body>\w+=.*)$")),
)

# kv attributes that describe the log transport, not the record
TRANSPORT_KEYS = frozenset(
    {"instance", "pod", "region", "node", "logger", "severity", "level", "trace_id", "seq"}
)


@dataclass(frozen=True)
class LogLine:
    lineno: int
    ts: datetime
    fmt: str
    trace_id: str | None
    service: str
    kv: dict

    @property
    def event(self) -> str:
        return self.kv.get("event") or ""


def _unquote(v: str):
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"')
    return None if v == "null" else v


def parse_kv(body: str) -> dict:
    return {k: _unquote(v) for k, v in KV_RE.findall(body)}


def parse_line(line: str, lineno: int = 0) -> LogLine | None:
    line = line.rstrip("\r\n")
    for fmt, rx in FORMATS:
        m = rx.match(line)
        if not m:
            continue
        g = m.groupdict()
        if fmt == "json":
            raw = json.loads(g["body"])
            kv = {k: (None if v == "null" else v) for k, v in raw.items()}
            trace, service = kv.get("trace_id"), kv.get("service") or kv.get("logger") or ""
        elif fmt == "cloud":
            kv = parse_kv(g["body"].replace('\\"', '"'))
            trace, service = g["trace"], g["service"]
        else:
            kv = parse_kv(g["body"])
            trace = g.get("trace") or kv.get("trace_id")
            service = g.get("service") or kv.get("service") or ""
        if g.get("event"):
            kv.setdefault("event", g["event"])
        if g.get("level"):
            kv.setdefault("level", g["level"])
        return LogLine(lineno, parse_ts(g["ts"]), fmt, trace, service, kv)
    return None


def _as_int(v) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def line_present(kv: dict) -> bool | None:
    """Existence stated by one log line about its resource_id."""
    rows, status, result = _as_int(kv.get("rows")), str(kv.get("http_status")), kv.get("result")
    if rows == 0 or status == "404" or result == "miss" or result in LEDGER_ABSENT:
        return False
    event = kv.get("event") or ""
    if (
        (rows is not None and rows >= 1)
        or status == "200"
        or result == "hit"
        or result in LEDGER_PRESENT
        or (event.endswith("_pii_scan") and kv.get("matched_value"))
        or kv.get("marker_found") == "false"
        or kv.get("eligible") == "true"
    ):
        return True
    return None


def _group_key(line: LogLine) -> str:
    return f"trace:{line.trace_id}" if line.trace_id else f"L{line.lineno}"


def parse_log(ctx: FileCtx, path: Path) -> FileParse:
    result = FileParse(ctx.file_id, ctx.path, "log")
    groups: dict[str, list[LogLine]] = {}
    raw_lines: dict[int, str] = {}
    formats: set[str] = set()
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip():
            continue
        line = parse_line(text, n)
        if line is None:
            result.unparsed += 1  # unrecognised line format
            continue
        formats.add(line.fmt)
        raw_lines[n] = text
        groups.setdefault(_group_key(line), []).append(line)

    emitted: dict[str, set[str]] = {}
    for gkey, lines in groups.items():
        emitted[gkey] = set()
        resources = [
            line.kv["resource_id"]
            for line in lines
            if isinstance(line.kv.get("resource_id"), str)
            and RESOURCE_ID_RE.fullmatch(line.kv["resource_id"])
        ]
        group_keys = link_keys_from(*[line.kv for line in lines])
        events = [line.event for line in lines]
        for rid in dict.fromkeys(resources):
            own = [line for line in lines if line.kv.get("resource_id") == rid]
            fields: dict = {}
            attrs: dict = {}
            for line in [*lines, *own]:  # resource lines last so their values win
                for k, v in line.kv.items():
                    if k == "resource_id" or k in TRANSPORT_KEYS:
                        continue
                    (fields if k in IDENT_KEYS else attrs)[k] = v
            attrs["events"] = events
            if lines[0].trace_id:
                attrs["trace_id"] = lines[0].trace_id
            present = None
            for line in own:
                p = line_present(line.kv)
                if p is not None:
                    present = p
            last = own[-1]
            obs = make_observation(
                ctx,
                resource_id=rid,
                present=present,
                copy="log",
                collection=last.event,
                source_system=last.kv.get("system") or last.service,
                fields=fields,
                attrs=attrs,
                raw_ref=f"{ctx.path}#{gkey}" if lines[0].trace_id else f"{ctx.path}#L{last.lineno}",
                observed_at=last.ts,
            )
            if obs is not None:
                obs = replace(obs, link_keys=obs.link_keys | group_keys)
                result.observations.append(obs)
                emitted[gkey].add(rid)

    # every resource id mentioned on any line must be covered by its group's observations
    for gkey, lines in groups.items():
        for line in lines:
            if set(find_resource_ids(raw_lines[line.lineno])) - emitted[gkey]:
                result.unparsed += 1

    for s in sorted({o.system_id for o in result.observations}):
        result.coverages.append(coverage(ctx, s, "lookup", complete=False))
    result.family = "log:" + "+".join(sorted(formats)) if formats else "log:empty"
    return result
