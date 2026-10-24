"""Resource state at audit_at, from evidence captured at or before audit_at only.

The decisive capture is the latest file (by captured_at <= audit_at) with a non-mention
observation of the resource. Inside that file the record is PRESENT if any copy (primary or
replica) is present, each copy judged by its latest observed_at.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from ghost import policy
from ghost.observation import Observation
from ghost.parsers.common import LEDGER_ABSENT, LEDGER_PRESENT

QUERY_FIELDS = frozenset(
    {"lookup_key", "subject_key", "match_key", "correlation_key", "query", "scan_key",
     "subject_selector", "lookup_value", "owner_key"}
)
MENTION_ATTRS = frozenset({"row_source", "referenced_by", "reference_field", "trace_id"})
RETURNED_ATTRS = ("enabled", "restore_allowed", "retention_until", "retained_until")
LEDGER_RESULTS = LEDGER_PRESENT | LEDGER_ABSENT
LEDGER_EXPOSED = frozenset({"IDENTIFIABLE", "FOUND"})
TRUE = frozenset({"true", "1", "yes"})


def truthy(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE


def _rows(o: Observation) -> int:
    try:
        return int(o.attrs.get("rows"))
    except (TypeError, ValueError):
        return 0


def _record_fields(o: Observation) -> dict:
    """The record's own identifier fields (the query keys are the search, not the record)."""
    return {k: v for k, v in o.fields.items() if k not in QUERY_FIELDS and v not in (None, "")}


def coerce_present(o: Observation) -> bool | None:
    """present, with present=None upgraded to True when the observation is a returned record.
    Pure mentions (no record fields, nothing but provenance attrs) stay None."""
    if o.present is not None:
        return o.present
    if "referenced_by" in o.attrs:  # another record names this one; its fields are not ours
        return None
    if _record_fields(o):
        return True
    a = o.attrs
    if _rows(o) >= 1 or str(a.get("http_status")) == "200" or str(a.get("status_code")) == "200":
        return True
    if any(a.get(k) not in (None, "") for k in RETURNED_ATTRS):
        return True
    events = a.get("events") or ([a["event"]] if a.get("event") else [])
    returned = {k for k in a if k not in MENTION_ATTRS and k not in ("event", "events")}
    if returned and any(str(e).endswith(("_completed", "_read")) for e in events):
        return True
    return None


@dataclass(frozen=True)
class ResourceState:
    decisive_file: str | None  # None = no capture <= audit_at decides: assumed compliant
    present: bool
    exposed: bool  # REDACT/ANONYMIZE: prohibited identifiers still set
    active: bool  # data flow enabled
    risk: bool  # backup can still be restored
    observations: tuple[Observation, ...] = ()  # the decisive file's observations

    @property
    def unknown(self) -> bool:
        return self.decisive_file is None


UNKNOWN = ResourceState(None, False, False, False, False)


def _latest_per_copy(observations: list[Observation]) -> list[Observation]:
    best: dict[str, Observation] = {}
    for o in sorted(observations, key=lambda o: (o.observed_at, o.raw_ref)):
        best[o.copy] = o  # later observed_at overwrites
    return list(best.values())


def _exposed(o: Observation, action: str) -> bool:
    """Prohibited identifiers still on a present record (ledger rows: by their result)."""
    result = o.attrs.get("result")
    if o.copy == "ledger" or result in LEDGER_RESULTS:
        return result in LEDGER_EXPOSED
    matched = o.attrs.get("matched_value") or o.fields.get("matched_value")
    if matched not in (None, ""):
        return True
    return bool(policy.prohibited(_record_fields(o), action))


def _flow_active(o: Observation) -> bool:
    return truthy(o.attrs.get("enabled")) is True or o.attrs.get("result") == "ACTIVE"


def _backup_risk(o: Observation) -> bool:
    a = o.attrs
    if truthy(a.get("restore_allowed")) is True or a.get("result") == "ACTIVE":
        return True
    return truthy(a.get("eligible")) is True and truthy(a.get("deletion_block")) is False


def resource_state(
    observations: tuple[Observation, ...], audit_at: datetime, action: str
) -> ResourceState:
    by_file: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        if o.captured_at <= audit_at and coerce_present(o) is not None:
            by_file[o.file_id].append(o)
    if not by_file:
        return UNKNOWN
    decisive = max(
        by_file,
        key=lambda f: (by_file[f][0].captured_at, max(o.observed_at for o in by_file[f]), f),
    )
    latest = _latest_per_copy(by_file[decisive])
    present_obs = [o for o in latest if coerce_present(o)]
    return ResourceState(
        decisive_file=decisive,
        present=bool(present_obs),
        exposed=any(_exposed(o, action) for o in present_obs),
        active=any(_flow_active(o) for o in present_obs),
        risk=any(_backup_risk(o) for o in present_obs),
        observations=tuple(by_file[decisive]),
    )
