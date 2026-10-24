"""Common evidence records produced by every parser."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Observation:
    """One sighting of one resource in one evidence file (or one log trace group)."""

    file_id: str
    captured_at: datetime  # from the manifest
    observed_at: datetime  # row/line-level time if present, else captured_at
    system_id: str  # canonical, from the resource_id prefix
    source_system: str  # raw system/service name as written in the file
    resource_id: str
    present: bool | None  # None = mentioned, existence not stated
    copy: str  # "primary", "replica:<table>", "export", "bundle", "ledger", "log", "pdf", ...
    collection: str  # raw table / collection / event name
    fields: dict = field(default_factory=dict)  # identifier fields, raw values
    attrs: dict = field(default_factory=dict)  # everything else useful, raw values
    link_keys: frozenset[str] = frozenset()  # normalized identifiers (see ghost.extract)
    raw_ref: str = ""  # "path#table:rowid" / "path#L<line>" / "path#trace:<id>"


@dataclass(frozen=True)
class Coverage:
    """A file inspected a system. complete=True means it lists the whole collection,
    so a resource missing from it is absent at that time."""

    file_id: str
    captured_at: datetime  # from the manifest
    system_id: str
    scope: str  # table / collection / "lookup"
    complete: bool
    observed_at: datetime | None = None  # when the collection itself was captured

    def __post_init__(self) -> None:
        if self.observed_at is None:
            object.__setattr__(self, "observed_at", self.captured_at)


@dataclass(frozen=True)
class FileFacts:
    """Facts from documents that carry identities but no resource ids (e.g. registers)."""

    file_id: str
    captured_at: datetime
    user_ids: frozenset[str]
    order_refs: frozenset[str]
    retain_until: tuple[str, ...]
    emails: frozenset[str] = frozenset()
    ticket_refs: frozenset[str] = frozenset()
    file_names: frozenset[str] = frozenset()
