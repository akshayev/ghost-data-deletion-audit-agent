"""Lightweight per-case trace: which agent called which tool, when, with what result.

Events are kept in memory and written at the end of a run as one JSON-lines file per case
(<runs_dir>/traces/<case_id>.jsonl; global-phase events go to _global.jsonl).
"""

import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

GLOBAL = "_global"


@dataclass(frozen=True)
class TraceEvent:
    ts: float
    case_id: str
    agent: str
    tool: str | None
    summary: str
    data: dict = field(default_factory=dict)


class Tracer:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.events: dict[str, list[TraceEvent]] = defaultdict(list)

    def emit(self, case_id: str | None, agent: str, tool: str | None, summary: str,
             data: dict | None = None) -> None:
        if self.enabled:
            case = case_id or GLOBAL
            self.events[case].append(TraceEvent(time.time(), case, agent, tool, summary,
                                                data or {}))

    def for_case(self, case_id: str) -> list[TraceEvent]:
        return list(self.events.get(case_id, ()))

    def write(self, directory: Path) -> int:
        """Replace the trace directory's files with this run's events; returns files written."""
        if not self.enabled:
            return 0
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob("*.jsonl"):
            old.unlink()
        for case, events in self.events.items():
            with open(directory / f"{case}.jsonl", "w", encoding="utf-8", newline="\n") as f:
                for e in events:
                    f.write(json.dumps(asdict(e), ensure_ascii=False, default=str) + "\n")
        return len(self.events)
