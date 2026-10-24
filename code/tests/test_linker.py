"""Linker: which resources belong to each request's subject (no state/policy yet)."""

from datetime import UTC, datetime

import pytest

from ghost import linker, loaders
from ghost.loaders import Activity


@pytest.fixture(scope="module")
def lk():
    return linker.build_linker()


def req(case_id):
    for split in ("sample", "full"):
        for r in loaders.load_requests(split):
            if r.case_id == case_id:
                return r
    raise KeyError(case_id)


def linked(lk, case_id):
    return {r.resource_id: r for r in lk.link(case_id)}


def channels(res):
    return {v[0] for v in res.via}


# ---------- pure helpers ----------


def act(details, event_type="DELETE_RECORD", rid="account_1"):
    t = datetime(2026, 1, 1, tzinfo=UTC)
    return Activity("activity_x", t, "", event_type, "deletion_worker", "", rid, "SUCCESS", details)


def test_template_replaces_resource_ids_and_cuts_to_40():
    a = act("Deletion worker submitted primary profile account_6440 to account_service.")
    assert linker.template(a) == "DELETE_RECORD|Deletion worker submitted primary profil"
    b = act("Policy action completed for analytics_5335.", "ANONYMIZE_RECORD", "analytics_5335")
    assert linker.template(b) == "ANONYMIZE_RECORD|Policy action completed for X."


@pytest.mark.parametrize(
    "details,expected",
    [
        (
            "Data catalog linked export_1842 (NST-2026-0718-1842) to ingrid.dahl@outlook.com "
            "and user_18.",
            ("export_1842", "NST-2026-0718-1842", "ingrid.dahl@outlook.com", "user_18"),
        ),
        (
            "Attachment catalog confirmed object_19 (delivery_confirmation_19.pdf) belongs only "
            "to priya.raman@gmail.com and user_07; no other subject is linked.",
            ("object_19", "delivery_confirmation_19.pdf", "priya.raman@gmail.com", "user_07"),
        ),
    ],
)
def test_parse_catalog_both_phrasings(details, expected):
    assert linker.parse_catalog(details) == expected


def test_parse_catalog_rejects_other_text():
    assert linker.parse_catalog("Customer search-index refresh processed search_9729.") is None


# ---------- KEY channel ----------


def test_case_12_links_analytics_9914_via_old_email_hash(lk):
    res = linked(lk, "case_12")["analytics_9914"]
    keys = {v[1] for v in res.via if v[0] == "key"}
    current_hashes = {i.email_hash for i in loaders.load_identities()}
    assert keys and all(len(k) == 64 for k in keys)
    assert not (keys & current_hashes)  # only the historical hash matches


def test_case_10_links_support_002_via_yahoo_email_in_evidence_61(lk):
    res = linked(lk, "case_10")["support_002"]
    assert ("key", "nadia.ionescu@yahoo.com", "evidence_61") in res.via


def test_names_never_link_case_06(lk):
    got = linked(lk, "case_06")
    assert "account_8814" not in got and "search_8814" not in got


# ---------- WORKER-WINDOW channel ----------


def test_case_21_worker_window_without_tie_break(lk):
    got = linked(lk, "case_21")
    for rid in ("account_6440", "analytics_5335"):
        assert "worker_window" in channels(got[rid]), rid
    (via,) = [v for v in got["account_6440"].via if v[0] == "worker_window"]
    assert via[2] is False  # assigned without a tie-break
    # PARTIAL / FAILED accounts in the same window cannot belong to a COMPLETE case
    for rid in ("account_294", "account_8832", "account_7015", "account_410", "account_9155"):
        assert rid not in got


def test_learned_account_window_case_30_not_case_04(lk):
    assert "account_4163" in linked(lk, "case_30")
    assert "account_4163" not in linked(lk, "case_04")


def test_learned_windows_shape(lk):
    lo, hi = lk.windows["DELETE_RECORD|Deletion worker submitted primary profil"][:2]
    assert -1.4 < lo < -1.1 and -0.9 < hi < -0.6


def test_no_resource_assigned_by_worker_window_to_two_cases(lk):
    owner = {}
    for case_id in lk.case_ids:
        for r in lk.link(case_id):
            if any(v[0] == "worker_window" for v in r.via):
                assert owner.setdefault(r.resource_id, case_id) == case_id, r.resource_id


def test_non_deletion_cases_get_no_worker_resources():
    lk = linker.build_linker(non_deletion=frozenset({"case_21"}))
    assert not any(
        v[0] == "worker_window" for r in lk.link("case_21") for v in r.via
    )


# ---------- CATALOG channel + alias bridge ----------


def test_case_63_catalog_marketing_001_with_identity_conflict(lk):
    assert req("case_63").email == "kwame-mensah@proton.me"
    res = linked(lk, "case_63")["marketing_001"]
    assert "catalog" in channels(res)
    assert res.flags.get("identity_conflict") is True


def test_alias_bridge_export_1842_gets_synthetic_observation(lk):
    alias_obs = [o for o in lk.resource_observations("export_1842") if o.copy == "alias"]
    assert alias_obs
    for o in alias_obs:
        assert o.present is True and o.attrs["alias"] == "NST-2026-0718-1842"
        assert {"ingrid.dahl@outlook.com", "user_18"} <= o.link_keys
    texts = linker.file_texts()
    assert all("NST-2026-0718-1842" in texts[o.file_id] for o in alias_obs)


# ---------- STALE filter ----------


def test_case_17_stale_filter(lk):
    got = linked(lk, "case_17")
    for rid in ("account_6523", "object_19", "tombstone_17"):
        assert rid not in got, rid
    for rid in ("account_4092", "backup_3245"):
        assert rid in got, rid


# ---------- output contract ----------


def test_linked_resource_shape_and_sorting(lk):
    out = lk.link("case_21")
    assert [r.resource_id for r in out] == sorted(r.resource_id for r in out)
    for r in out:
        assert r.system_id and r.via
        assert all(o.resource_id == r.resource_id for o in r.observations)
        assert list(r.activity_ids) == sorted(r.activity_ids, key=linker.case_num)


def test_deterministic_across_builds(lk):
    other = linker.Linker.from_dataset()
    for case_id in lk.case_ids:
        assert lk.link(case_id) == other.link(case_id), case_id


def test_link_case_uses_cached_linker():
    r = req("case_21")
    assert linker.link_case(r) is linker.link_case(r)


def test_alias_already_keying_another_resource_does_not_bridge(lk):
    # "user_17" in evidence_01 is the lookup key of audit_record_1797, not a tombstone sighting
    files = {o.file_id for o in lk.resource_observations("tombstone_17") if o.copy == "alias"}
    assert "evidence_42" in files  # the deleted-account reference screen
    assert "evidence_01" not in files


def test_name_only_observation_never_links():
    from ghost.identity import IdentityResolver
    from ghost.observation import Observation

    r = req("case_06")
    resolver = IdentityResolver(loaders.load_identities(), loaders.load_audit_changes())
    subject = resolver.resolve(r.email)
    (name,) = subject.names
    t = r.reported_at
    obs = Observation(
        "evidence_x", t, t, "account_service", "", "account_99991", True, "primary", "t",
        link_keys=frozenset({f"name:{name}"}),
    )
    lk = linker.Linker([r], [obs], [], [], resolver, {}, {})
    assert lk.link("case_06") == []


def test_worker_window_never_duplicates_a_held_type(lk):
    for case_id in lk.case_ids:
        out = lk.link(case_id)
        worker = [r for r in out if any(v[0] == "worker_window" for v in r.via)]
        other_types = {
            linker.rtype(rid)
            for rid in (*lk.key_links[case_id], *lk.catalog_links[case_id])
        }
        types = [linker.rtype(r.resource_id) for r in worker]
        assert len(types) == len(set(types)), (case_id, types)
        assert not (set(types) & other_types), case_id
