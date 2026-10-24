"""Resolve a request email (current or historical) to a full subject identity."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ghost.loaders import AuditChange, UserIdentity
from ghost.normalize import email_to_hash, norm_email, norm_hash, norm_name, norm_phone


@dataclass(frozen=True)
class SubjectIdentity:
    user_ids: frozenset[str]
    emails: frozenset[str]
    phones: frozenset[str]
    hashes: frozenset[str]
    names: frozenset[str]
    # (event_time, field_name, old_value, new_value), oldest first, raw values
    history: tuple[tuple[str, str, str, str], ...]
    matched_via: str  # "current" or "historical"

    @property
    def strong_keys(self) -> frozenset[str]:
        return self.user_ids | self.emails | self.hashes | self.phones

    @property
    def weak_keys(self) -> frozenset[str]:
        # Names are shared by different users, so they never link a record alone.
        return self.names


class IdentityResolver:
    def __init__(
        self,
        identities: Iterable[UserIdentity],
        audit_changes: Iterable[AuditChange],
    ) -> None:
        self._identities = {i.user_id: i for i in identities}
        self._changes: dict[str, list[AuditChange]] = defaultdict(list)
        for c in sorted(audit_changes, key=lambda c: (c.event_time, c.audit_id)):
            self._changes[c.user_id].append(c)

        self._current_email: dict[str, str] = {}
        self._any_email: dict[str, set[str]] = defaultdict(set)
        for ident in self._identities.values():
            e = norm_email(ident.email)
            if e:
                self._current_email[e] = ident.user_id
                self._any_email[e].add(ident.user_id)
        for uid, changes in self._changes.items():
            for c in changes:
                if c.field_name == "email":
                    for v in (c.old_value, c.new_value):
                        e = norm_email(v)
                        if e:
                            self._any_email[e].add(uid)

    def resolve(self, email: str | None) -> SubjectIdentity | None:
        e = norm_email(email)
        owners = self._any_email.get(e)
        if not owners:
            return None
        if len(owners) > 1:
            # Prefer the user who currently holds the address.
            current = self._current_email.get(e)
            if current is None:
                return None
            owners = {current}
        (uid,) = owners
        return self._build(uid, matched_current=self._current_email.get(e) == uid)

    def resolve_user(self, user_id: str) -> SubjectIdentity:
        return self._build(user_id, matched_current=True)

    def _build(self, uid: str, matched_current: bool) -> SubjectIdentity:
        ident = self._identities.get(uid)
        emails, phones, hashes, names = set(), set(), set(), set()
        if ident:
            emails.add(norm_email(ident.email))
            phones.add(norm_phone(ident.phone))
            hashes.add(norm_hash(ident.email_hash))
            names.add(norm_name(ident.name))
        history = []
        for c in self._changes.get(uid, []):
            history.append((c.event_time_raw, c.field_name, c.old_value, c.new_value))
            for v in (c.old_value, c.new_value):
                if c.field_name == "email":
                    emails.add(norm_email(v))
                elif c.field_name == "phone":
                    phones.add(norm_phone(v))
                elif c.field_name == "email_hash":
                    hashes.add(norm_hash(v))
                elif c.field_name == "name":
                    names.add(norm_name(v))
        emails.discard("")
        hashes |= {email_to_hash(e) for e in emails}
        phones.discard("")
        hashes.discard("")
        names.discard("")
        return SubjectIdentity(
            user_ids=frozenset({uid}),
            emails=frozenset(emails),
            phones=frozenset(phones),
            hashes=frozenset(hashes),
            names=frozenset(names),
            history=tuple(history),
            matched_via="current" if matched_current else "historical",
        )

    def all_keys_index(self) -> dict[str, str]:
        """Every strong key (user_id, email, hash, phone) -> owning user_id."""
        index: dict[str, str] = {}
        for uid in self._identities:
            for key in self._build(uid, True).strong_keys:
                index[key] = uid
        return index
