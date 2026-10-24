"""Identifier extraction shared by all parsers.

Link keys are normalized so they compare directly with IdentityResolver keys:
emails (lowercased), 64-hex hashes, user_<n>, phone digits (phone-like fields
only) and names as "name:<normalized>" (name-like fields only, never strong keys).
"""

import re
from collections.abc import Iterable, Mapping

from ghost.normalize import norm_email, norm_hash, norm_name, norm_phone
from ghost.systems import RESOURCE_ID_RE

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9A-Fa-f]{64}\b")
USER_ID_RE = re.compile(r"\buser_\d+\b")
# Free-text phones (PDFs only): international format with a leading "+".
PHONE_TEXT_RE = re.compile(r"\+\d[\d ()-]{6,}\d")

NAME_FIELDS = frozenset({"name", "full_name", "display_name", "requester_name"})

__all__ = [
    "RESOURCE_ID_RE",
    "find_resource_ids",
    "keys_in_text",
    "link_keys_from",
    "is_phone_field",
]


def find_resource_ids(text: str) -> list[str]:
    """Resource ids in order of appearance, matched on word boundaries."""
    return [m.group(0) for m in RESOURCE_ID_RE.finditer(text or "")]


def is_phone_field(key: str) -> bool:
    return key == "phone" or key.endswith("_phone")


def keys_in_text(text: str) -> set[str]:
    """Strong identifiers that are safe to pull out of any string: emails, hashes, user ids."""
    keys: set[str] = set()
    for m in EMAIL_RE.finditer(text):
        keys.add(norm_email(m.group(0)))
    for m in HASH_RE.finditer(text):
        h = norm_hash(m.group(0))
        if h:
            keys.add(h)
    keys.update(USER_ID_RE.findall(text))
    return keys


def phones_in_text(text: str) -> set[str]:
    return {p for p in (norm_phone(m.group(0)) for m in PHONE_TEXT_RE.finditer(text)) if p}


def _walk(value, key: str, out: set[str]) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            _walk(v, str(k), out)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            _walk(v, key, out)
        return
    if not isinstance(value, str):
        return
    out |= keys_in_text(value)
    if is_phone_field(key):
        p = norm_phone(value)
        if p:
            out.add(p)
    if key in NAME_FIELDS:
        n = norm_name(value)
        if n:
            out.add(f"name:{n}")


def link_keys_from(*mappings: Mapping | Iterable) -> frozenset[str]:
    """Normalized identifiers found anywhere in the given (nested) mappings."""
    out: set[str] = set()
    for m in mappings:
        _walk(m, "", out)
    return frozenset(out)
