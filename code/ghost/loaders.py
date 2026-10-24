"""Typed loaders for every dataset CSV. Timestamps are parsed to UTC-aware
datetimes; the original string is kept in <field>_raw."""

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from ghost import paths

REQUEST_FILES = {
    "full": "deletion_requests.csv",
    "sample": "sample_deletion_requests.csv",
}


def parse_ts(value: str) -> datetime:
    """Parse ISO-8601 (accepting a trailing 'Z') to a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _read(name: str) -> list[dict[str, str]]:
    with open(Path(paths.DATASET) / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class DeletionRequest:
    case_id: str
    email: str
    requested_at: datetime
    requested_at_raw: str
    reported_status: str
    reported_at: datetime
    reported_at_raw: str
    audit_at: datetime
    audit_at_raw: str
    request_text: str


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime
    updated_at_raw: str


@dataclass(frozen=True)
class AuditChange:
    audit_id: str
    user_id: str
    event_time: datetime
    event_time_raw: str
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class Activity:
    activity_id: str
    event_time: datetime
    event_time_raw: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass(frozen=True)
class DeletionRule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    file_path: str
    origin: str
    captured_at: datetime
    captured_at_raw: str


@cache
def load_requests(split: str) -> tuple[DeletionRequest, ...]:
    if split not in REQUEST_FILES:
        raise ValueError(f"unknown split {split!r}; expected one of {sorted(REQUEST_FILES)}")
    return tuple(
        DeletionRequest(
            case_id=r["case_id"],
            email=r["email"],
            requested_at=parse_ts(r["requested_at"]),
            requested_at_raw=r["requested_at"],
            reported_status=r["reported_status"],
            reported_at=parse_ts(r["reported_at"]),
            reported_at_raw=r["reported_at"],
            audit_at=parse_ts(r["audit_at"]),
            audit_at_raw=r["audit_at"],
            request_text=r["request_text"],
        )
        for r in _read(REQUEST_FILES[split])
    )


@cache
def load_identities() -> tuple[UserIdentity, ...]:
    return tuple(
        UserIdentity(
            user_id=r["user_id"],
            name=r["name"],
            email=r["email"],
            phone=r["phone"],
            email_hash=r["email_hash"],
            updated_at=parse_ts(r["updated_at"]),
            updated_at_raw=r["updated_at"],
        )
        for r in _read("user_identities.csv")
    )


@cache
def load_audit_changes() -> tuple[AuditChange, ...]:
    return tuple(
        AuditChange(
            audit_id=r["audit_id"],
            user_id=r["user_id"],
            event_time=parse_ts(r["event_time"]),
            event_time_raw=r["event_time"],
            field_name=r["field_name"],
            old_value=r["old_value"],
            new_value=r["new_value"],
        )
        for r in _read("user_audit_log.csv")
    )


@cache
def load_activities() -> tuple[Activity, ...]:
    return tuple(
        Activity(
            activity_id=r["activity_id"],
            event_time=parse_ts(r["event_time"]),
            event_time_raw=r["event_time"],
            event_type=r["event_type"],
            source_system=r["source_system"],
            target_system=r["target_system"],
            resource_id=r["resource_id"],
            reported_result=r["reported_result"],
            details=r["details"],
        )
        for r in _read("system_activity.csv")
    )


@cache
def load_rules() -> tuple[DeletionRule, ...]:
    return tuple(
        DeletionRule(
            rule_id=r["rule_id"],
            data_category=r["data_category"],
            condition=r["condition"],
            required_action=r["required_action"],
        )
        for r in _read("deletion_rules.csv")
    )


@cache
def load_evidence_files() -> tuple[EvidenceFile, ...]:
    return tuple(
        EvidenceFile(
            file_id=r["file_id"],
            file_path=r["file_path"],
            origin=r["origin"],
            captured_at=parse_ts(r["captured_at"]),
            captured_at_raw=r["captured_at"],
        )
        for r in _read("evidence_files.csv")
    )
