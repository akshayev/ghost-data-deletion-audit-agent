"""Helpers shared by the evidence parsers."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from ghost.extract import link_keys_from
from ghost.loaders import parse_ts
from ghost.observation import Coverage, FileFacts, Observation
from ghost.systems import RESOURCE_ID_RE, canonical

# Keys whose values identify a person (kept in Observation.fields); everything
# else goes to Observation.attrs.
IDENT_KEYS = frozenset(
    {
        "email",
        "phone",
        "name",
        "full_name",
        "display_name",
        "requester_name",
        "requester_email",
        "requester_phone",
        "email_hash",
        "customer_reference",
        "user_id",
        "subject_reference",
        "owner_email",
        "owner_phone",
        "customer_email",
        "subject_selector",
        "lookup_value",
        "uploaded_by",
        "lookup_key",
        "subject_key",
        "match_key",
        "correlation_key",
        "matched_value",
        "subject_ref",
        "scan_key",
        "owner_key",
        "query",
    }
)

# Ledger results (handover TXT, ledger JSON, record_lookup log lines).
LEDGER_PRESENT = frozenset(
    {"FOUND", "IDENTIFIABLE", "ACTIVE", "ANONYMIZED", "BLOCKED", "DISABLED", "REDACTED"}
)
LEDGER_ABSENT = frozenset({"NOT_FOUND"})
LEDGER_KEYS = frozenset({"system", "resource_id", "record_type", "result"})


def ledger_present(result: str | None) -> bool | None:
    if result in LEDGER_PRESENT:
        return True
    if result in LEDGER_ABSENT:
        return False
    return None


def is_ledger_record(record: Mapping) -> bool:
    return LEDGER_KEYS <= set(record) and (
        "subject_reference" in record or "lookup_value" in record
    )


@dataclass(frozen=True)
class FileCtx:
    file_id: str
    path: str  # dataset-relative, e.g. evidence/logs/x.log
    captured_at: datetime


@dataclass
class FileParse:
    file_id: str
    path: str
    family: str
    observations: list[Observation] = field(default_factory=list)
    coverages: list[Coverage] = field(default_factory=list)
    facts: list[FileFacts] = field(default_factory=list)
    unparsed: int = 0  # resource-bearing units that produced no observation


def safe_ts(value, default: datetime) -> datetime:
    if isinstance(value, str) and value:
        try:
            return parse_ts(value)
        except ValueError:
            return default
    return default


def split_record(record: Mapping, skip: frozenset[str] = frozenset({"resource_id"})):
    """Split a (possibly nested) record into identifier fields and other attrs.

    Nested dicts are flattened: identifier leaves go to fields under their leaf
    name, other leaves to attrs under their leaf name.
    """
    fields: dict = {}
    attrs: dict = {}

    def walk(d: Mapping) -> None:
        for k, v in d.items():
            if k in skip:
                continue
            if isinstance(v, Mapping):
                walk(v)
            elif k in IDENT_KEYS:
                fields[k] = v
            else:
                attrs[k] = v

    walk(record)
    return fields, attrs


def make_observation(
    ctx: FileCtx,
    *,
    resource_id: str,
    present: bool | None,
    copy: str,
    collection: str,
    source_system: str,
    fields: dict,
    attrs: dict,
    raw_ref: str,
    observed_at: datetime | None = None,
    extra_link_sources: tuple = (),
) -> Observation | None:
    """Build an Observation; returns None if resource_id is not a valid id."""
    if not isinstance(resource_id, str) or not RESOURCE_ID_RE.fullmatch(resource_id):
        return None
    system = canonical(resource_id)
    assert system is not None
    return Observation(
        file_id=ctx.file_id,
        captured_at=ctx.captured_at,
        observed_at=observed_at or ctx.captured_at,
        system_id=system,
        source_system=source_system or system,
        resource_id=resource_id,
        present=present,
        copy=copy,
        collection=collection,
        fields=fields,
        attrs=attrs,
        link_keys=link_keys_from(fields, attrs, *extra_link_sources),
        raw_ref=raw_ref,
    )


def coverage(
    ctx: FileCtx,
    system_id: str,
    scope: str,
    complete: bool,
    observed_at: datetime | None = None,
) -> Coverage:
    return Coverage(
        file_id=ctx.file_id,
        captured_at=ctx.captured_at,
        system_id=system_id,
        scope=scope,
        complete=complete,
        observed_at=observed_at or ctx.captured_at,
    )


def ledger_observation(ctx: FileCtx, record: Mapping, collection: str, raw_ref: str):
    """Ledger row: {system, resource_id, record_type, result, subject_reference|lookup_value}."""
    fields, attrs = split_record(record)
    return make_observation(
        ctx,
        resource_id=record["resource_id"],
        present=ledger_present(record.get("result")),
        copy="ledger",
        collection=collection,
        source_system=record.get("system", ""),
        fields=fields,
        attrs=attrs,
        raw_ref=raw_ref,
        observed_at=safe_ts(record.get("recorded_at"), ctx.captured_at),
    )


def reference_observations(observations) -> list[Observation]:
    """A record that names another resource (e.g. search doc source_resource_id=account_006)
    is evidence that the other resource is tied to the same identifiers. Emit a mention
    (present=None) for it so it can be linked; existence is not stated."""
    out = []
    for o in observations:
        for k, v in o.attrs.items():
            if (
                k.endswith("_resource_id")
                and isinstance(v, str)
                and v != o.resource_id
                and RESOURCE_ID_RE.fullmatch(v)
            ):
                out.append(
                    replace(
                        o,
                        resource_id=v,
                        system_id=canonical(v),
                        present=None,
                        attrs={"referenced_by": o.resource_id, "reference_field": k},
                    )
                )
    return out


def ledger_coverages(ctx: FileCtx, observations, scope: str) -> list[Coverage]:
    systems = sorted({o.system_id for o in observations})
    return [coverage(ctx, s, scope, complete=False) for s in systems]
