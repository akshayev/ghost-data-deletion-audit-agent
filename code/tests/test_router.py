"""Tie records in the linker, ambiguity points per case, and the router (real dataset)."""

import pytest

from ghost import ambiguity, classifier, linker
from ghost.agents.router import route_case


@pytest.fixture(scope="module")
def lk():
    non_deletion = classifier.non_deletion_cases(classifier.classify_requests(use_llm=False))
    return linker.build_linker(non_deletion, "SKIP_MULTI")


def test_skip_multi_groups_are_recorded_with_their_candidates(lk):
    skipped = [g for g in lk.tie_records if g["kind"] == "skip_multi"]
    assert skipped and all(len(g["cases"]) >= 3 for g in skipped)
    tie_2526 = [g for g in skipped if {"case_25", "case_26"} <= set(g["cases"])]
    assert tie_2526 and all(r.startswith("analytics_") for g in tie_2526 for r in g["rids"])
    for g in skipped:  # never assigned to anybody
        for rid in g["rids"]:
            assert not any(rid in lk.worker_links.get(c, {}) for c in lk.case_ids)


def test_every_tie_broken_link_belongs_to_a_recorded_group(lk):
    groups = [g for g in lk.tie_records if g["kind"] == "tie_break"]
    for case_id, links in lk.worker_links.items():
        for rid, vias in links.items():
            if any(v[2] for v in vias):
                assert any(rid in g["rids"] and case_id in g["cases"] for g in groups)


def test_recording_ties_does_not_change_the_assignment(lk):
    assert lk.tie_breaks == sum(
        1 for links in lk.worker_links.values() for vias in links.values()
        if any(v[2] for v in vias))


def test_ambiguities_for_the_known_analytics_tie(lk):
    points = ambiguity.case_ambiguities(lk, "case_25", lk.link("case_25"))
    cands = [a for a in points if a.kind == "tie_candidate"]
    assert cands and all(a.default == "EXCLUDE" for a in cands)
    assert all("case_26" in a.competing_case_ids for a in cands)
    assert [a.id for a in points] == [f"A{i}" for i in range(1, len(points) + 1)]


def test_tie_broken_links_are_ambiguities_with_default_include(lk):
    case_id, rid = next((c, r) for c, links in sorted(lk.worker_links.items())
                        for r, vias in links.items() if any(v[2] for v in vias)
                        and r in {x.resource_id for x in lk.link(c)})
    points = ambiguity.case_ambiguities(lk, case_id, lk.link(case_id))
    (point,) = [a for a in points if a.resource_id == rid]
    assert point.kind == "tie_break" and point.default == "INCLUDE" and point.competing_case_ids


def route(lk, case_id, authorized=True):
    points = ambiguity.case_ambiguities(lk, case_id, lk.link(case_id)) if authorized else []
    return route_case(authorized, [a.as_dict() for a in points])


def test_router_classification(lk):
    routed = route(lk, "case_25")
    assert routed[0] == "AGENT" and "skip_multi" in routed[1]
    assert route(lk, "case_533", authorized=False) == ("FAST", ("not_deletion",))
    fast = [c for c in lk.case_ids if route(lk, c)[0] == "FAST"]
    assert fast  # most cases need no judgement
    for c in fast[:50]:
        assert not ambiguity.case_ambiguities(lk, c, lk.link(c))


def test_identity_conflict_routes_to_the_agent(lk):
    flagged = [(c, r) for c in lk.case_ids for r in lk.link(c) if r.flags.get("identity_conflict")]
    for case_id, _res in flagged:
        where, reasons = route(lk, case_id)
        assert where == "AGENT" and "identity_conflict" in reasons
