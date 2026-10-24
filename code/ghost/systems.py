"""Canonical owning system for a resource, derived from its id prefix.

Verified against every data_store/system field in the evidence and every
system_id in sample_output.csv.
"""

import re

PREFIX_SYSTEMS = {
    "account": "account_service",
    "search": "search_service",
    "support": "support_platform",
    "analytics": "analytics_platform",
    "export": "export_service",
    "backup": "backup_service",
    "data_flow": "integration_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "object": "object_storage",
}

# Longest prefixes first so "audit_record" wins over any shorter alternative.
_PREFIX_ALT = "|".join(sorted(PREFIX_SYSTEMS, key=len, reverse=True))
RESOURCE_ID_RE = re.compile(rf"\b({_PREFIX_ALT})_\d+\b")


def canonical(resource_id: str) -> str | None:
    m = RESOURCE_ID_RE.fullmatch(resource_id or "")
    return PREFIX_SYSTEMS[m.group(1)] if m else None
