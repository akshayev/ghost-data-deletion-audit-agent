import hashlib
from collections import Counter
from datetime import UTC, datetime

import pytest

from ghost import loaders
from ghost.identity import IdentityResolver, SubjectIdentity
from ghost.normalize import email_to_hash, norm_email, norm_hash, norm_name, norm_phone


@pytest.fixture(scope="module")
def resolver():
    return IdentityResolver(loaders.load_identities(), loaders.load_audit_changes())


@pytest.fixture(scope="module")
def requests_by_split():
    return {s: loaders.load_requests(s) for s in ("full", "sample")}


# ---------- normalize ----------


def test_norm_email():
    assert norm_email("  Foo.BAR@Gmail.COM ") == "foo.bar@gmail.com"
    assert norm_email(None) == ""
    assert norm_email("   ") == ""


def test_norm_phone_strips_formatting():
    assert norm_phone("+971 50 482 7316") == "971504827316"
    assert norm_phone("(555) 123-4567") == "5551234567"


def test_norm_phone_short_is_empty():
    assert norm_phone("12345") == ""
    assert norm_phone("+1 23") == ""
    assert norm_phone(None) == ""


def test_norm_hash_validates_sha256_hex():
    h = "A" * 64
    assert norm_hash(f"  {h} ") == "a" * 64
    assert norm_hash("z" * 64) == ""
    assert norm_hash("abc") == ""
    assert norm_hash(None) == ""


def test_norm_name_nfkc_casefold_whitespace():
    assert norm_name("  José   ÁLVAREZ ") == "josé álvarez"
    assert norm_name("Ｓｔｒａｓｓｅ") == "strasse"  # full-width -> ascii, casefold
    assert norm_name("Weiß") == "weiss"
    assert norm_name(None) == ""


def test_email_to_hash_is_sha256_of_lowercase_email():
    expected = hashlib.sha256(b"a@b.com").hexdigest()
    assert email_to_hash(" A@B.com ") == expected


# ---------- loaders ----------


def test_parse_ts_accepts_z_and_is_utc_aware():
    dt = loaders.parse_ts("2026-09-15T11:59:39Z")
    assert dt == datetime(2026, 9, 15, 11, 59, 39, tzinfo=UTC)
    assert dt.utcoffset().total_seconds() == 0


def test_loader_row_counts():
    assert len(loaders.load_requests("full")) == 518
    assert len(loaders.load_requests("sample")) == 32
    assert len(loaders.load_identities()) == 850
    assert len(loaders.load_audit_changes()) == 306
    assert len(loaders.load_activities()) == 4011
    assert len(loaders.load_evidence_files()) == 94
    assert len(loaders.load_rules()) >= 10


def test_loaders_keep_raw_timestamp_and_parsed_value():
    r = loaders.load_requests("sample")[0]
    assert r.case_id == "case_01"
    assert r.audit_at_raw == "2026-09-03T11:30:00Z"
    assert r.audit_at == loaders.parse_ts(r.audit_at_raw)
    ef = loaders.load_evidence_files()[0]
    assert ef.file_id == "evidence_01"
    assert ef.captured_at_raw == "2026-09-15T11:59:39Z"
    assert ef.origin == "central_observability_export"


def test_loaders_are_cached():
    assert loaders.load_identities() is loaders.load_identities()
    assert loaders.load_requests("full") is loaders.load_requests("full")


def test_load_requests_rejects_unknown_split():
    with pytest.raises(ValueError):
        loaders.load_requests("bogus")


# ---------- resolver over all requests ----------


def test_all_550_request_emails_resolve_to_exactly_one_user(resolver, requests_by_split):
    total = 0
    for reqs in requests_by_split.values():
        for r in reqs:
            subj = resolver.resolve(r.email)
            assert subj is not None, r.case_id
            assert len(subj.user_ids) == 1, r.case_id
            total += 1
    assert total == 550


def test_matched_via_counts(resolver, requests_by_split):
    for split, expected in (("sample", (29, 3)), ("full", (418, 100))):
        c = Counter(resolver.resolve(r.email).matched_via for r in requests_by_split[split])
        assert (c["current"], c["historical"]) == expected, split


def test_unknown_email_returns_none(resolver):
    assert resolver.resolve("nobody.at.all@example.invalid") is None
    assert resolver.resolve("") is None
    assert resolver.resolve(None) is None


def test_uppercase_input_resolves_to_same_user(resolver):
    lower = resolver.resolve("sipho.mokoena@gmail.com")
    upper = resolver.resolve("SIPHO.MOKOENA@GMAIL.COM")
    assert lower is not None
    assert upper == lower


# ---------- chain following ----------


def test_case_10_two_change_chain(resolver, requests_by_split):
    req = next(r for r in requests_by_split["sample"] if r.case_id == "case_10")
    assert req.email == "nadia.ionescu@yahoo.com"
    subj = resolver.resolve(req.email)
    assert subj.user_ids == frozenset({"user_10"})
    assert subj.matched_via == "historical"
    emails = {"nadia.ionescu@yahoo.com", "nadia.petrescu@yahoo.com", "n.petrescu@yahoo.com"}
    assert subj.emails == frozenset(emails)
    assert {email_to_hash(e) for e in emails} <= subj.hashes
    assert subj.hashes >= {email_to_hash(e) for e in emails}


def test_resolving_any_email_in_chain_gives_same_subject(resolver):
    a = resolver.resolve("nadia.ionescu@yahoo.com")
    b = resolver.resolve("nadia.petrescu@yahoo.com")
    c = resolver.resolve("n.petrescu@yahoo.com")
    assert a.emails == b.emails == c.emails
    assert c.matched_via == "current"
    assert b.matched_via == "historical"


def test_history_is_recorded_in_time_order(resolver):
    subj = resolver.resolve("n.petrescu@yahoo.com")
    emails_changes = [h for h in subj.history if h[1] == "email"]
    assert [h[2:] for h in emails_changes] == [
        ("nadia.ionescu@yahoo.com", "nadia.petrescu@yahoo.com"),
        ("nadia.petrescu@yahoo.com", "n.petrescu@yahoo.com"),
    ]
    times = [h[0] for h in subj.history]
    assert times == sorted(times)


def test_old_email_hash_from_audit_log_is_in_hashes(resolver):
    rows = [c for c in loaders.load_audit_changes() if c.field_name == "email_hash"]
    assert rows
    for c in rows:
        subj = resolver.resolve_user(c.user_id)
        for v in (c.old_value, c.new_value):
            if norm_hash(v):
                assert norm_hash(v) in subj.hashes, c.audit_id


def test_phone_history_old_and_new_in_phones(resolver):
    rows = [c for c in loaders.load_audit_changes() if c.field_name == "phone"]
    assert rows
    for c in rows:
        subj = resolver.resolve_user(c.user_id)
        for v in (c.old_value, c.new_value):
            if norm_phone(v):
                assert norm_phone(v) in subj.phones, c.audit_id


def test_current_values_are_always_included(resolver):
    for ident in loaders.load_identities():
        subj = resolver.resolve_user(ident.user_id)
        assert norm_email(ident.email) in subj.emails
        assert norm_hash(ident.email_hash) in subj.hashes
        if norm_phone(ident.phone):
            assert norm_phone(ident.phone) in subj.phones
        assert norm_name(ident.name) in subj.names


# ---------- key classes ----------


def test_names_are_weak_only(resolver):
    subj = resolver.resolve("n.petrescu@yahoo.com")
    assert subj.names
    assert subj.weak_keys == subj.names
    assert not (subj.names & subj.strong_keys)
    assert subj.strong_keys == subj.user_ids | subj.emails | subj.hashes | subj.phones


def test_subject_identity_is_frozen(resolver):
    subj = resolver.resolve("n.petrescu@yahoo.com")
    assert isinstance(subj, SubjectIdentity)
    with pytest.raises(AttributeError):
        subj.emails = frozenset()  # type: ignore[misc]


def test_global_strong_key_collision_check(resolver):
    owners: dict[str, str] = {}
    collisions = []
    for ident in loaders.load_identities():
        subj = resolver.resolve_user(ident.user_id)
        for key in subj.strong_keys:
            if owners.setdefault(key, ident.user_id) != ident.user_id:
                collisions.append((key, owners[key], ident.user_id))
    assert collisions == []


def test_all_keys_index_maps_every_strong_key_to_its_user(resolver):
    index = resolver.all_keys_index()
    for ident in loaders.load_identities():
        subj = resolver.resolve_user(ident.user_id)
        for key in subj.strong_keys:
            assert index[key] == ident.user_id
    assert index["user_10"] == "user_10"
    assert index[email_to_hash("nadia.ionescu@yahoo.com")] == "user_10"
