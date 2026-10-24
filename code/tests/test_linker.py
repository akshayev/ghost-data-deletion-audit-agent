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
    assert "worker_window" in channels(got["account_6440"])
    (via,) = [v for v in got["account_6440"].via if v[0] == "worker_window"]
    assert via[2] is False  # a unique candidate: assigned without a tie-break
    # PARTIAL / FAILED accounts in the same window cannot belong to a COMPLETE case
    for rid in ("account_294", "account_8832", "account_7015", "account_410", "account_9155"):
        assert rid not in got


def test_case_21_analytics_needs_a_tie_break_so_only_order_links_it():
    order = {r.resource_id: r for r in linker.build_linker(frozenset(), "ORDER").link("case_21")}
    assert "worker_window" in channels(order["analytics_5335"])
    skip_multi = linker.build_linker(frozenset(), "SKIP_MULTI").link("case_21")
    assert "analytics_5335" not in {r.resource_id for r in skip_multi}


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


# ---------- propagation channel (sync / restore repopulation) ----------


def _propagation_linker(events, extra_obs=(), requested_days_before=0):
    """A one-case linker: the subject's data flow is linked by key; `events` are activities."""
    from datetime import timedelta

    from ghost.identity import IdentityResolver
    from ghost.observation import Observation

    r = req("case_06")
    resolver = IdentityResolver(loaders.load_identities(), loaders.load_audit_changes())
    subject = resolver.resolve(r.email)
    email = sorted(k for k in subject.strong_keys if "@" in k)[0]
    seen = r.requested_at + timedelta(days=1)
    flow = Observation("evidence_x", seen, seen, "integration_service", "", "data_flow_9001", True,
                       "primary", "t", fields={"customer_email": email},
                       link_keys=frozenset({email}))
    acts = [
        Activity(f"activity_{9000 + i}", t, "", et, "integration_service", "", src, "SUCCESS", text)
        for i, (t, et, src, text) in enumerate(events)
    ]
    return linker.Linker([r], [flow, *extra_obs], [], acts, resolver, {}, {}), r


def _at(r, days):
    from datetime import timedelta

    return r.audit_at + timedelta(days=days)


def test_propagation_links_a_target_without_identifiers():
    from datetime import UTC, datetime

    r = req("case_06")
    lk, r = _propagation_linker([
        (r.requested_at, "SYNC_RECORD", "data_flow_9001",
         "Active connector repopulated downstream resource search_7001."),
    ])
    got = {x.resource_id: x for x in lk.link("case_06")}
    assert "search_7001" in got
    assert ("propagation", "activity_9000", "data_flow_9001") in got["search_7001"].via
    assert lk.propagation_stats["new"] == 1 and datetime.now(UTC)


def test_propagation_after_audit_at_is_ignored():
    r = req("case_06")
    lk, r = _propagation_linker([
        (_at(r, 1), "SYNC_RECORD", "data_flow_9001",
         "Active connector repopulated downstream resource search_7001."),
    ])
    assert "search_7001" not in {x.resource_id for x in lk.link("case_06")}


def test_propagation_skips_target_owned_by_another_user():
    from ghost.observation import Observation

    r = req("case_06")
    other = next(i for i in loaders.load_identities() if i.email.lower() != r.email.lower())
    t = r.requested_at
    foreign = Observation("evidence_y", t, t, "search_service", "", "search_7001", True, "primary",
                          "t", fields={"email": other.email},
                          link_keys=frozenset({other.email.lower()}))
    lk, r = _propagation_linker(
        [(t, "SYNC_RECORD", "data_flow_9001",
          "Active connector repopulated downstream resource search_7001.")],
        extra_obs=[foreign],
    )
    assert "search_7001" not in {x.resource_id for x in lk.link("case_06")}
    assert lk.propagation_stats["blocked"] == 1


def test_propagation_restore_phrasing_and_one_hop_only():
    r = req("case_06")
    lk, r = _propagation_linker([
        (r.requested_at, "RESTORE_BACKUP", "data_flow_9001",
         "Restore job repopulated downstream resource search_7001."),
        (r.requested_at, "SYNC_RECORD", "search_7001",
         "Active connector repopulated downstream resource search_7002."),
    ])
    ids = {x.resource_id for x in lk.link("case_06")}
    assert "search_7001" in ids and "search_7002" not in ids


def test_propagation_target_is_still_subject_to_the_stale_filter():
    from datetime import timedelta

    from ghost.observation import Observation

    r = req("case_06")
    old = r.requested_at - timedelta(days=30)
    seen_before = Observation("evidence_z", old, old, "search_service", "", "search_7001", True,
                              "primary", "t")
    lk, r = _propagation_linker(
        [(r.requested_at, "SYNC_RECORD", "data_flow_9001",
          "Active connector repopulated downstream resource search_7001.")],
        extra_obs=[seen_before],
    )
    assert "search_7001" not in {x.resource_id for x in lk.link("case_06")}


def test_propagation_does_not_change_the_state_decision():
    # linking only adds the resource; with no evidence <= audit_at it is assumed compliant
    from ghost import planner

    r = req("case_06")
    lk, r = _propagation_linker([
        (r.requested_at, "SYNC_RECORD", "data_flow_9001",
         "Active connector repopulated downstream resource search_7001."),
    ])
    linked = lk.link("case_06")
    res = next(x for x in linked if x.resource_id == "search_7001")
    assert planner.decide(res, r, lk).state.unknown


# ---------- tie-break strategies ----------


def _tie_fixture(n_cases, strategy):
    """n cases sharing reported_at, two free accounts with in-window worker events.
    Case ids and requested_at run in OPPOSITE order (case_5 requested last)."""
    from datetime import timedelta

    from ghost.identity import IdentityResolver

    reported = datetime(2026, 6, 1, 12, tzinfo=UTC)
    reqs = []
    for i in range(n_cases):
        requested = reported - timedelta(days=2 + i)  # case_5 is the most recently requested
        reqs.append(loaders.DeletionRequest(
            f"case_{5 + i}", f"nobody{i}@example.test", requested, "", "COMPLETE", reported, "",
            reported + timedelta(days=7), "", "delete my account",
        ))
    acts = [
        Activity(f"activity_{800 + k}", reported + timedelta(hours=-1 + 0.1 * k), "",
                 "DELETE_RECORD", "deletion_worker", "account_service", f"account_90{k + 1}",
                 "SUCCESS",
                 f"Deletion worker submitted primary profile account_90{k + 1} to account_service.")
        for k in range(2)
    ]
    return linker.Linker(reqs, [], [], acts, IdentityResolver([], []), {}, {}, tiebreak=strategy)


def _owner_of(lk, rid):
    return [c for c in lk.case_ids if any(r.resource_id == rid for r in lk.link(c))]


def test_tiebreak_order_pairs_case_ascending_with_event_ascending():
    lk = _tie_fixture(2, "ORDER")
    assert _owner_of(lk, "account_901") == ["case_5"]
    assert _owner_of(lk, "account_902") == ["case_6"]
    assert lk.tie_breaks == 2


def test_tiebreak_requested_pairs_by_requested_at():
    lk = _tie_fixture(2, "REQUESTED")
    assert _owner_of(lk, "account_901") == ["case_6"]  # case_6 was requested first
    assert _owner_of(lk, "account_902") == ["case_5"]


def test_tiebreak_skip_all_assigns_nothing_on_a_tie():
    lk = _tie_fixture(2, "SKIP_ALL")
    assert _owner_of(lk, "account_901") == [] and _owner_of(lk, "account_902") == []
    assert lk.tie_breaks == 0


def test_tiebreak_skip_multi_assigns_pairs_but_not_three_way_ties():
    assert lk_owner_count(_tie_fixture(2, "SKIP_MULTI")) == 2
    assert lk_owner_count(_tie_fixture(3, "SKIP_MULTI")) == 0
    assert lk_owner_count(_tie_fixture(3, "ORDER")) == 2


def lk_owner_count(lk):
    return sum(len(_owner_of(lk, rid)) for rid in ("account_901", "account_902"))


def test_unique_candidate_assignments_ignore_the_strategy():
    for strategy in linker.TIEBREAKS:
        lk = _tie_fixture(1, strategy)  # a single candidate case: no tie at all
        assert _owner_of(lk, "account_901") == ["case_5"], strategy
        assert lk.tie_breaks == 0


def test_unknown_strategy_is_rejected_and_env_selects_the_default(monkeypatch):
    with pytest.raises(ValueError, match="tie-break"):
        _tie_fixture(2, "NOPE")
    monkeypatch.setenv("GHOST_TIEBREAK", "SKIP_ALL")
    assert linker.default_tiebreak() == "SKIP_ALL"
    monkeypatch.setenv("GHOST_TIEBREAK", "bogus")
    with pytest.raises(ValueError, match="tie-break"):
        linker.default_tiebreak()
    monkeypatch.delenv("GHOST_TIEBREAK")
    assert linker.default_tiebreak() in linker.TIEBREAKS


def test_real_data_tie_counts_are_monotone_in_strategy():
    ties = {s: linker.build_linker(frozenset(), s).tie_breaks for s in linker.TIEBREAKS}
    assert ties["SKIP_ALL"] == 0
    assert ties["SKIP_ALL"] <= ties["SKIP_MULTI"] <= ties["ORDER"]
    assert ties["REQUESTED"] == ties["ORDER"] or ties["REQUESTED"] > 0


def test_build_linker_follows_the_env_strategy_without_stale_cache(monkeypatch):
    monkeypatch.setenv("GHOST_TIEBREAK", "SKIP_ALL")
    assert linker.build_linker().tiebreak == "SKIP_ALL"
    monkeypatch.setenv("GHOST_TIEBREAK", "ORDER")
    assert linker.build_linker().tiebreak == "ORDER"
