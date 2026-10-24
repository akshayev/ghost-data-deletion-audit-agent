"""Screenshot extraction -> Observations.

Unit tests feed a synthetic extraction to the converter. The ground-truth tests read the
CACHED vision extraction (committed under code/cache/llm), so they run offline.
"""

from datetime import UTC, datetime

import pytest

from ghost import llm
from ghost.observation import FileFacts
from ghost.parsers import images, registry
from ghost.parsers.common import FileCtx

CTX = FileCtx("evidence_99", "evidence/images/x.png", datetime(2026, 9, 1, 6, 0, tzinfo=UTC))
KNOWN = frozenset({"g.silva76@gmail.com", "ingrid.dahl@outlook.com", "areed1433@gmail.com"})


def rec(rid, source="table_row", status=None, attributes=None, **fields):
    full = {"name": None, "email": None, "phone": None, "user_ref": None, "email_hash": None}
    full.update(fields)
    return {
        "resource_id": rid,
        "row_source": source,
        "fields": full,
        "status": status,
        "attributes": attributes or {},
    }


def extraction(records=(), mentions=(), **refs):
    references = {
        k: [] for k in ("user_refs", "order_refs", "ticket_refs", "file_names", "other")
    }
    references.update(refs)
    return {
        "screen": {"product": "Search Console", "title": "Customer Search", "subtitle": None},
        "records": list(records),
        "mentions": list(mentions),
        "references": references,
    }


def convert(data):
    return images.convert(CTX, data, KNOWN)


def by_id(observations):
    return {o.resource_id: o for o in observations}


# ---------- conversion ----------


def test_table_row_becomes_present_image_observation():
    data = extraction(
        [
            rec(
                "search_428",
                status="Live",
                attributes={"Score": "9.84"},
                name="Gabriel Silva",
                email="G.Silva76@Gmail.com",
                user_ref="user_13",
            )
        ]
    )
    observations, facts, stats = convert(data)
    (o,) = observations
    assert (o.present, o.copy, o.collection) == (True, "image", "Customer Search")
    assert o.system_id == "search_service" and o.source_system == "Search Console"
    assert o.observed_at == o.captured_at == CTX.captured_at and o.file_id == "evidence_99"
    assert o.fields["email"] == "g.silva76@gmail.com"
    assert {"g.silva76@gmail.com", "user_13", "name:gabriel silva"} <= o.link_keys
    assert o.attrs["status"] == "Live" and o.attrs["Score"] == "9.84"
    assert o.attrs["row_source"] == "table_row"
    assert stats.records == 1 and facts == []


def test_link_keys_never_cross_rows():
    data = extraction(
        [
            rec("search_428", email="g.silva76@gmail.com", user_ref="user_13"),
            rec("search_1874", email="g.silva76.jr@gmail.com"),
        ]
    )
    observations, _, _ = convert(data)
    a, b = by_id(observations)["search_428"], by_id(observations)["search_1874"]
    assert not (a.link_keys & b.link_keys)
    assert "user_13" not in b.link_keys


def test_detail_panel_fields_flow_to_its_own_resource_only():
    data = extraction(
        [
            rec("backup_9291", email="bluepeakco@outlook.com"),
            rec("backup_9287", email=None),
            rec("backup_9287", source="detail_panel", email="areed1433@gmail.com"),
        ]
    )
    observations, _, _ = convert(data)
    rows = [o for o in observations if o.resource_id == "backup_9287"]
    assert len(rows) == 2 and all("areed1433@gmail.com" in o.link_keys for o in rows)
    other = by_id(observations)["backup_9291"]
    assert other.link_keys == frozenset({"bluepeakco@outlook.com"})


def test_normalizes_email_phone_and_hash_and_drops_bad_hash():
    good = "a" * 64
    data = extraction(
        [rec("account_1", email=" X@Y.COM ", phone="+91 98765-43210", email_hash=good.upper())]
    )
    (o,), _, _ = convert(data)
    assert o.fields["email"] == "x@y.com"
    assert "919876543210" in o.link_keys and good in o.link_keys
    (o2,), _, _ = convert(extraction([rec("account_2", email_hash="not-a-hash")]))
    assert "email_hash" not in o2.fields and not o2.link_keys


def test_mentions_are_present_none_without_link_keys():
    data = extraction([rec("backup_9287", email="areed1433@gmail.com")], mentions=["backup_9281"])
    observations, _, stats = convert(data)
    m = by_id(observations)["backup_9281"]
    assert m.present is None and not m.link_keys and m.copy == "image"
    assert stats.mentions == 1


def test_mention_that_is_also_a_record_is_not_duplicated():
    data = extraction([rec("backup_9287", email="areed1433@gmail.com")], mentions=["backup_9287"])
    observations, _, stats = convert(data)
    assert len(observations) == 1 and stats.mentions == 0


def test_invalid_ids_are_dropped_and_counted():
    data = extraction(
        [rec("ghost_77", email="a@b.co"), rec("search_12x"), rec("search_5")],
        mentions=["nonsense_1"],
    )
    observations, _, stats = convert(data)
    assert [o.resource_id for o in observations] == ["search_5"]
    assert sorted(stats.dropped_ids) == ["ghost_77", "nonsense_1", "search_12x"]


def test_dot_where_underscore_belongs_is_repaired():
    observations, _, stats = convert(extraction([rec("data_flow.1121", email="a@b.co")]))
    assert [o.resource_id for o in observations] == ["data_flow_1121"]
    assert stats.dropped_ids == []


@pytest.mark.parametrize(
    "email,flagged",
    [
        ("g.silva76.jr@gmail.com", False),  # real decoy: local part is 3 edits away
        ("g.silva76@gmail.com", False),  # exactly an identity email
        ("ingrid.dahl@outiook.com", True),  # domain misread, local part identical
        ("g.silva67@gmail.com", True),  # local part 2 edits from g.silva76
        ("someone.else@fastmail.com", False),
    ],
)
def test_suspect_email_flag(email, flagged):
    (o,), _, stats = convert(extraction([rec("search_5", email=email)]))
    assert ("suspect_email" in o.attrs) is flagged
    assert (email in stats.suspect_emails) is flagged
    assert o.fields["email"] == email  # flagged, never dropped
    assert email in o.link_keys


def test_references_become_file_facts():
    data = extraction(
        [rec(None, email="kagyeman8947@fastmail.com", user_ref="user_17")],
        user_refs=["user_17"],
        order_refs=["ORD-20265034"],
        ticket_refs=["CS-5034"],
        file_names=["delivery_confirmation_19.pdf"],
    )
    observations, facts, _ = convert(data)
    assert observations == []  # no resource id, so nothing to observe
    (f,) = facts
    assert isinstance(f, FileFacts) and f.file_id == "evidence_99"
    assert f.user_ids == {"user_17"} and f.order_refs == {"ORD-20265034"}
    assert f.ticket_refs == {"CS-5034"} and f.file_names == {"delivery_confirmation_19.pdf"}
    assert f.emails == {"kagyeman8947@fastmail.com"}


def test_screen_without_references_has_no_facts():
    assert convert(extraction([rec("search_5")]))[1] == []


# ---------- offline paths ----------


def test_cache_miss_without_llm_is_skipped_and_never_touches_network(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "LEDGER_PATH", tmp_path / "l.jsonl")
    monkeypatch.setattr(llm, "_ENV_LOADED", True)
    monkeypatch.setattr(llm, "_make_client", lambda p: pytest.fail("network used"))
    monkeypatch.setattr(images, "ocr_lines", lambda path: None)  # no tesseract
    png = tmp_path / "shot.png"
    png.write_bytes(b"not really a png")
    result = images.parse_image(CTX, png, use_llm=False)
    assert result.family == "image:skipped" and result.observations == []


def test_ocr_fallback_links_only_within_the_same_line(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm, "LEDGER_PATH", tmp_path / "l.jsonl")
    monkeypatch.setattr(llm, "_ENV_LOADED", True)
    lines = [
        "search_428  Gabriel Silva  g.silva76@gmail.com  user_13",
        "search_1874  g.silva76.jr@gmail.com",
        "backup_9281 restored Completed",
    ]
    monkeypatch.setattr(images, "ocr_lines", lambda path: lines)
    png = tmp_path / "shot.png"
    png.write_bytes(b"x")
    result = images.parse_image(CTX, png, use_llm=False)
    assert result.family == "image:ocr"
    got = by_id(result.observations)
    assert all(o.present is None and o.copy == "image" for o in got.values())
    assert got["search_428"].link_keys == frozenset({"g.silva76@gmail.com", "user_13"})
    assert got["search_1874"].link_keys == frozenset({"g.silva76.jr@gmail.com"})
    assert got["backup_9281"].link_keys == frozenset()


# ---------- ground truth, from the cached vision extraction ----------


@pytest.fixture(scope="module")
def shots():
    results = registry.parse_results()
    return {fid: r for fid, r in results.items() if r.path.endswith(".png")}


def of(shots, file_id, resource_id, source=None):
    found = [
        o
        for o in shots[file_id].observations
        if o.resource_id == resource_id and (source is None or o.attrs.get("row_source") == source)
    ]
    assert found, f"{resource_id} not extracted from {file_id}"
    return found


def keys(shots, file_id, resource_id):
    return frozenset().union(*(o.link_keys for o in of(shots, file_id, resource_id)))


def test_all_eleven_pngs_were_extracted_from_cache(shots):
    assert len(shots) == 11
    for fid, r in shots.items():
        assert r.family == "image:vision", (fid, r.family)
        assert r.observations or r.facts, fid


def test_evidence_09_search_rows_do_not_share_keys(shots):
    a, b = keys(shots, "evidence_09", "search_428"), keys(shots, "evidence_09", "search_1874")
    assert {"g.silva76@gmail.com", "user_13", "name:gabriel silva"} <= a
    assert "g.silva76.jr@gmail.com" in b
    assert not any(k.startswith("user_") for k in b)
    assert not (a & b)


def test_evidence_32_backups_each_link_their_own_email(shots):
    assert "areed1433@gmail.com" in keys(shots, "evidence_32", "backup_9287")
    assert "bluepeakco@outlook.com" in keys(shots, "evidence_32", "backup_9291")
    assert "miller.logistics@company.com" in keys(shots, "evidence_32", "backup_9288")
    assert "summit-retail@company.com" in keys(shots, "evidence_32", "backup_9286")
    for other in ("backup_9291", "backup_9288", "backup_9286"):
        assert "areed1433@gmail.com" not in keys(shots, "evidence_32", other)
    (m,) = of(shots, "evidence_32", "backup_9281")
    assert m.present is None and not m.link_keys


def test_evidence_28_support_ticket_and_references(shots):
    assert "kagyeman8947@fastmail.com" in keys(shots, "evidence_28", "support_1034")
    (facts,) = shots["evidence_28"].facts
    assert "ORD-20265034" in facts.order_refs and "CS-5034" in facts.ticket_refs


def test_evidence_37_audit_records_all_user_03(shots):
    for n in range(1, 7):
        rid = f"audit_record_{n:03d}"
        assert all(o.present is True for o in of(shots, "evidence_37", rid))
        assert "user_03" in keys(shots, "evidence_37", rid), rid


def test_evidence_60_marketing_profile(shots):
    found = of(shots, "evidence_60", "marketing_001")
    assert {"name:soo-jin park", "sj.park@naver.com"} <= keys(shots, "evidence_60", "marketing_001")
    assert any(str(o.attrs.get("status", "")).lower() == "live" for o in found)


def test_evidence_04_account_admin(shots):
    k = keys(shots, "evidence_04", "account_5762")
    assert {"user_11", "haruka.s1838@zoho.com"} <= k
    assert of(shots, "evidence_04", "support_9538")


def test_evidence_19_analytics_profile(shots):
    k = keys(shots, "evidence_19", "analytics_7438")
    assert {"user_20", "darwish.omar@icloud.com"} <= k


def test_evidence_14_connectors(shots):
    assert "jain.aditi@gmail.com" in keys(shots, "evidence_14", "data_flow_1121")
    assert "lucia-torres@gmail.com" in keys(shots, "evidence_14", "data_flow_9846")


def test_evidence_23_export_email_is_outlook_not_outiook(shots):
    k = keys(shots, "evidence_23", "export_1260")
    assert "ingrid.dahl@outlook.com" in k
    assert not any("outiook" in x for x in k)


def test_evidence_46_object_storage_has_files_but_no_resources(shots):
    r = shots["evidence_46"]
    assert not r.observations
    (facts,) = r.facts
    assert "delivery_confirmation_19.pdf" in facts.file_names


def test_evidence_42_deleted_account_reference(shots):
    (facts,) = shots["evidence_42"].facts
    assert "user_17" in facts.user_ids and "ORD-20263245" in facts.order_refs
