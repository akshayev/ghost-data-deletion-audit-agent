"""Normalizers for identifiers, so values from different sources compare equal."""

import hashlib
import re
import unicodedata

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_PHONE_DIGITS = 8


def norm_email(s: str | None) -> str:
    return (s or "").strip().lower()


def norm_phone(s: str | None) -> str:
    """Digits only; too-short numbers are not usable as a key, so return ''."""
    digits = "".join(ch for ch in (s or "") if ch.isdigit())
    return digits if len(digits) >= MIN_PHONE_DIGITS else ""


def norm_name(s: str | None) -> str:
    text = unicodedata.normalize("NFKC", s or "").casefold()
    return " ".join(text.split())


def norm_hash(s: str | None) -> str:
    h = (s or "").strip().lower()
    return h if _HASH_RE.match(h) else ""


def email_to_hash(email: str | None) -> str:
    """email_hash in the dataset is sha256 of the lowercase email."""
    return hashlib.sha256(norm_email(email).encode("utf-8")).hexdigest()
