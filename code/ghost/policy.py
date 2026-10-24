"""Required treatment per linked resource, from deletion_rules.csv and the evidence.

Priority: deletion-worker classification > ledger record_type > evidence shape >
resource-prefix default. Treatments: DELETE, REDACT, ANONYMIZE, RETAIN, BLOCK, DISABLE.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from ghost import loaders
from ghost.linker import WORKER, LinkedResource, Linker
from ghost.loaders import DeletionRequest
from ghost.observation import Observation

WORKER_ACTION = {
    "DELETE_RECORD": "DELETE",
    "REDACT_RECORD": "REDACT",
    "ANONYMIZE_RECORD": "ANONYMIZE",
    "BLOCK_RESTORE": "BLOCK",
    "CHECK_RESTORE_ACCESS": "BLOCK",
    "DISABLE_DATA_FLOW": "DISABLE",
    "CHECK_DATA_FLOW": "DISABLE",
}

PREFIX_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "export": "USER_EXPORT",
    "cache": "CACHE_ENTRY",
    "marketing": "MARKETING_PROFILE",
    "analytics": "ANALYTICS_PROFILE",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "audit_record": "SECURITY_AUDIT_LOG",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "backup": "BACKUP_ARCHIVE",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "object": "SUPPORT_ATTACHMENT",
    "support": "SUPPORT_MESSAGE",
}
DATA_FLOW_PREFIX = "data_flow"
RETAIN_CATEGORIES = frozenset({"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"})
RETENTION_ATTRS = ("retention_until", "retained_until", "retain_until", "Retention end")

NAME_FIELDS = frozenset({"name", "full_name", "display_name", "requester_name"})
REFERENCE_FIELDS = frozenset({"customer_reference", "user_id", "subject_reference", "user_ref"})


def _category_actions() -> dict[str, str]:
    """data_category -> treatment. BACKUP_ARCHIVE is retained with restore access blocked
    (its rule's condition), so its treatment is BLOCK."""
    out = {}
    for rule in loaders.load_rules():
        action = rule.required_action
        if action == "RETAIN" and "restore" in rule.condition.lower():
            action = "BLOCK"
        out[rule.data_category] = action
    return out


CATEGORY_ACTION = _category_actions()


@dataclass(frozen=True)
class Treatment:
    action: str  # DELETE | REDACT | ANONYMIZE | RETAIN | BLOCK | DISABLE
    category: str | None
    source: str  # worker | ledger | shape | prefix | retention_expired


def prohibited(fields: dict, action: str) -> list[str]:
    """Identifier fields (sorted) that must be null under a REDACT/ANONYMIZE treatment.
    Query keys (lookup_key, subject_key, ...) are the search, not the record, so they never
    count. A user_XX reference is allowed under REDACT."""
    out = []
    for k, v in fields.items():
        if v in (None, ""):
            continue
        is_direct = (
            k in NAME_FIELDS
            or k == "email"
            or k.endswith("_email")
            or k == "phone"
            or k.endswith("_phone")
            or k == "email_hash"
        )
        if is_direct or (action == "ANONYMIZE" and k in REFERENCE_FIELDS):
            out.append(k)
    return sorted(out)


def _prefix(resource_id: str) -> str:
    return resource_id.rpartition("_")[0]


def _worker_action(res: LinkedResource, request: DeletionRequest, lk: Linker) -> str | None:
    acts = [
        a
        for a in (lk.activity(aid) for aid in res.activity_ids)
        if a.source_system == WORKER and a.event_type in WORKER_ACTION
    ]
    actions = {WORKER_ACTION[a.event_type] for a in acts}
    if len(actions) > 1:  # conflicting classifications: trust the ones in this case's windows
        inside = {WORKER_ACTION[a.event_type] for a in acts if lk.in_window(a, request.case_id)}
        actions = inside or actions
    if not actions:
        return None
    if len(actions) > 1:  # still ambiguous: the latest classification wins
        latest = max(acts, key=lambda a: (a.event_time, a.activity_id))
        return WORKER_ACTION[latest.event_type]
    return next(iter(actions))


def _ledger_category(observations) -> str | None:
    types = sorted(
        {
            o.attrs["record_type"]
            for o in observations
            if o.attrs.get("record_type") in CATEGORY_ACTION
        }
    )
    return types[0] if types else None


def _events(o: Observation) -> list[str]:
    events = o.attrs.get("events") or []
    if isinstance(events, str):
        events = [events]
    single = o.attrs.get("event")
    return [*events, *([single] if single else [])]


def _support_shape(observations) -> str | None:
    for o in observations:
        if (
            o.collection == "ticket_attachments"
            or "attachment_pii_scan" in _events(o)
            or "file" in o.attrs
            or o.copy in ("pdf", "alias")
        ):
            return "SUPPORT_ATTACHMENT"
    for o in observations:
        legacy = (
            o.source_system == "support_replica"
            or o.attrs.get("source") == "legacy_contact_index"
        )
        has_message = any(k in o.attrs for k in ("message_text", "message_excerpt"))
        if legacy and not has_message:
            return "SUPPORT_ATTACHMENT"  # subject-only copy: deleted like an attachment
    return None


def _analytics_shape(observations) -> str | None:
    for o in observations:
        if "email_opt_in" in o.attrs or any(e.startswith("contact_lookup") for e in _events(o)):
            return "MARKETING_PROFILE"
    return None


def _retention_expired(observations, audit_at: datetime) -> bool:
    from ghost.parsers.common import safe_ts

    dates = []
    for o in observations:
        if o.captured_at > audit_at:
            continue
        for key in RETENTION_ATTRS:
            value = o.attrs.get(key)
            if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
                dates.append(safe_ts(value if "T" in value else f"{value}T00:00:00Z", audit_at))
    return bool(dates) and max(dates) <= audit_at


def treatment(res: LinkedResource, request: DeletionRequest, lk: Linker) -> Treatment:
    prefix = _prefix(res.resource_id)
    obs = res.observations
    if action := _worker_action(res, request, lk):
        return Treatment(action, None, "worker")
    if prefix == DATA_FLOW_PREFIX:
        return Treatment("DISABLE", None, "prefix")
    if category := _ledger_category(obs):
        source = "ledger"
    elif prefix == "support" and (category := _support_shape(obs)):
        source = "shape"
    elif prefix == "analytics" and (category := _analytics_shape(obs)):
        source = "shape"
    else:
        category, source = PREFIX_CATEGORY.get(prefix), "prefix"
    action = CATEGORY_ACTION.get(category or "", "DELETE")
    if category in RETAIN_CATEGORIES and _retention_expired(obs, request.audit_at):
        return Treatment("DELETE", category, "retention_expired")
    return Treatment(action, category, source)
