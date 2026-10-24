import csv
import json

import pytest

from ghost import paths
from ghost.extract import RESOURCE_ID_RE, find_resource_ids, link_keys_from
from ghost.normalize import email_to_hash
from ghost.observation import Coverage, Observation
from ghost.parsers import registry
from ghost.parsers.logs import parse_line
from ghost.systems import PREFIX_SYSTEMS, canonical


@pytest.fixture(scope="module")
def parsed():
    return registry.parse_all()


@pytest.fixture(scope="module")
def observations(parsed):
    return parsed[0]


@pytest.fixture(scope="module")
def coverages(parsed):
    return parsed[1]


def obs(observations, file_id, resource_id):
    found = [o for o in observations if o.file_id == file_id and o.resource_id == resource_id]
    assert found, f"no observation for {resource_id} in {file_id}"
    return found


# ---------- systems ----------


def test_canonical_system_by_prefix():
    assert canonical("account_005") == "account_service"
    assert canonical("data_flow_9522") == "integration_service"
    assert canonical("audit_record_3737") == "security_service"
    assert canonical("tombstone_440") == "identity_service"
    assert canonical("legal_hold_438") == "legal_service"
    assert canonical("email_delivery_12") == "email_delivery_service"
    assert canonical("object_7") == "object_storage"
    assert canonical("user_10") is None
    assert canonical("support_864x") is None


def test_every_gold_system_id_is_canonical():
    with open(paths.DATASET / "sample_output.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            for s in json.loads(row["steps_json"]):
                assert canonical(s["resource_id"]) == s["system_id"], s


def test_prefix_table_has_all_fourteen_prefixes():
    assert len(PREFIX_SYSTEMS) == 14


# ---------- extract ----------


def test_resource_ids_respect_word_boundaries():
    text = "support_864 and support_8642 near xsupport_1 and archive/backup_5061.tar"
    assert find_resource_ids(text) == ["support_864", "support_8642", "backup_5061"]
    assert RESOURCE_ID_RE.fullmatch("audit_record_9394")


def test_link_keys_emails_hashes_user_ids_exact():
    h = email_to_hash("a@b.com")
    keys = link_keys_from(
        {
            "lookup_key": "G.SILVA76.JR@GMAIL.COM",
            "note": f"see user_10 and {h.upper()}",
            "email": None,
        }
    )
    assert "g.silva76.jr@gmail.com" in keys
    assert "g.silva76@gmail.com" not in keys
    assert "user_10" in keys
    assert h in keys


def test_phones_only_from_phone_fields_and_names_prefixed():
    keys = link_keys_from(
        {"phone": "+91 98765 73142", "order": "ORD-20264827 12345678", "full_name": "Priya  RAMAN"}
    )
    assert "919876573142" in keys
    assert "12345678" not in keys
    assert "name:priya raman" in keys
    assert "priya raman" not in keys


# ---------- acceptance: specific evidence ----------


def test_evidence_54_replication_drift(observations, coverages):
    found = obs(observations, "evidence_54", "account_005")
    assert all(o.present is True for o in found)
    assert all(o.copy.startswith("replica:") for o in found)
    assert not [o for o in found if o.copy == "primary"]
    assert any(c.file_id == "evidence_54" and c.system_id == "account_service" for c in coverages)
    primaries = {c.scope for c in coverages if c.file_id == "evidence_54"}
    assert "customer_profiles_primary" in primaries


def test_evidence_01_trace_links_lookup_key_to_404(observations):
    (o,) = obs(observations, "evidence_01", "account_4092")
    assert o.present is False
    assert "leonie.g9352@outlook.com" in o.link_keys
    (b,) = obs(observations, "evidence_01", "backup_3245")
    assert b.attrs["eligible"] == "true"
    assert b.attrs["deletion_block"] == "false"
    assert b.present is True


def test_evidence_01_correlation_key_hash(observations):
    (o,) = obs(observations, "evidence_01", "analytics_9914")
    assert any(k.startswith("44a65154bfd8") for k in o.link_keys)


def test_evidence_61_support_replica(observations):
    (o,) = obs(observations, "evidence_61", "support_002")
    assert o.present is True
    assert "nadia.ionescu@yahoo.com" in o.link_keys
    assert o.attrs["marker_found"] == "false"
    (p,) = obs(observations, "evidence_61", "support_5914")
    assert p.fields.get("lookup_key") == "nadia.popescu@yahoo.com"
    assert "nadia.popescu@yahoo.com" in p.link_keys


def test_evidence_57_search_primary(observations):
    (o,) = obs(observations, "evidence_57", "search_001")
    assert o.present is True
    assert o.attrs["source_resource_id"] == "account_006"
    assert o.copy == "primary"


def test_referenced_resource_gets_mention_observation(observations):
    (o,) = obs(observations, "evidence_57", "account_006")
    assert o.present is None
    assert o.attrs["referenced_by"] == "search_001"
    assert o.attrs["reference_field"] == "source_resource_id"
    assert o.system_id == "account_service"
    assert "jonas.berg@outlook.com" in o.link_keys


def test_evidence_03_handover(observations):
    (a,) = obs(observations, "evidence_03", "analytics_2034")
    assert a.attrs["record_type"] == "ANALYTICS_PROFILE"
    assert a.attrs["result"] == "IDENTIFIABLE"
    assert a.present is True
    assert "novak.tomasz@icloud.com" in a.link_keys
    (b,) = obs(observations, "evidence_03", "analytics_6830")
    assert b.link_keys == frozenset()
    (c,) = obs(observations, "evidence_03", "account_2381")
    assert "sipho.mokoena@gmail.com" in c.link_keys
    assert c.present is False


def test_evidence_58_pdf(observations):
    (o,) = obs(observations, "evidence_58", "support_001")
    assert o.present is True
    assert o.copy == "pdf"
    assert {"priya.raman@gmail.com", "919876573142"} <= o.link_keys
    assert o.attrs["doc_text_excerpt"]


def test_legal_register_pdf_becomes_file_facts(parsed):
    facts = [f for f in parsed[2] if f.file_id == "evidence_27"]
    assert len(facts) == 1
    assert "user_12" in facts[0].user_ids
    assert "ORD-20264207" in facts[0].order_refs
    assert facts[0].retain_until


def test_evidence_92_service_export(observations):
    (a,) = obs(observations, "evidence_92", "account_6440")
    assert a.present is False
    assert a.attrs["status_code"] == 404
    (s,) = obs(observations, "evidence_92", "search_1993")
    assert s.present is True
    assert "noah.f438085@outlook.com" in s.link_keys


def test_evidence_90_empty_inventory_still_has_coverage(observations, coverages):
    assert [o for o in observations if o.file_id == "evidence_90"] == []
    cov = [c for c in coverages if c.file_id == "evidence_90"]
    assert any(c.system_id == "account_service" and c.complete for c in cov)


def test_bundle_nested_search_document_is_flattened(observations):
    found = [o for o in observations if o.copy == "bundle" and o.system_id == "search_service"]
    assert found
    assert any("display_name" in o.fields and "email" in o.fields for o in found)


def test_bundle_coverage_keeps_snapshot_time(coverages):
    cov = [c for c in coverages if c.file_id == "evidence_02"]
    assert {c.captured_at.isoformat() for c in cov} == {"2026-09-17T12:00:00+00:00"}
    assert min(c.observed_at for c in cov).isoformat() == "2026-09-09T12:00:00+00:00"


def test_ledger_coverage_is_incomplete(coverages):
    ledger = [c for c in coverages if c.file_id == "evidence_50"]
    assert ledger
    assert not any(c.complete for c in ledger)


# ---------- log formats a-f ----------

LINES = {
    "kv": '2026-09-15T11:51:19Z level=INFO service=account_service instance=account-6 '
    "pod=account-581a region=us-east-2 trace_id=tr-4708586 resource_id=account_4092 "
    "event=profile_lookup_completed rows=0 http_status=404 duration_ms=33",
    "bracket": '2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-6074163] '
    'profile_lookup_started: instance="account-5" pod="account-886d" region="eu-west-1" '
    'lookup_key="l.nascimento40@gmail.com" attempt="1"',
    "pipe": '2026-07-03T07:52:15Z | INFO | account_service | tr-9500772 | '
    'profile_lookup_completed | instance="account-6" pod="account-9a54" '
    'region="ap-south-1" resource_id="account_4512" rows="0" http_status="404" duration_ms="38"',
    "json": '2026-09-10T08:56:17Z {"severity":"DEBUG","logger":"account_service",'
    '"service":"account_service","instance":"account-6","pod":"account-ca1e",'
    '"region":"us-east-2","trace_id":"tr-5563868","event":"profile_lookup_started",'
    '"lookup_key":"collins.grace@icloud.com","attempt":"1"}',
    "syslog": '2026-08-22T10:51:11Z <15>1 node-1 account_service tr-4973247 - - '
    'level=DEBUG instance="account-2" pod="account-0525" region="eu-west-1" '
    'event="profile_lookup_started" lookup_key="nana.b9576@outlook.com" attempt="1"',
    "cloud": '2026-06-08T09:51:26Z requestId=tr-7753246 level=DEBUG function=account_service '
    'message="instance=\\"account-4\\" pod=\\"account-7efc\\" region=\\"ap-south-1\\" '
    'event=\\"profile_lookup_started\\" lookup_key=\\"cchukwu7021@gmail.com\\" attempt=\\"1\\""',
}


@pytest.mark.parametrize(
    "fmt,trace,service,key,value",
    [
        ("kv", "tr-4708586", "account_service", "resource_id", "account_4092"),
        ("bracket", "tr-6074163", "account_service", "lookup_key", "l.nascimento40@gmail.com"),
        ("pipe", "tr-9500772", "account_service", "resource_id", "account_4512"),
        ("json", "tr-5563868", "account_service", "lookup_key", "collins.grace@icloud.com"),
        ("syslog", "tr-4973247", "account_service", "lookup_key", "nana.b9576@outlook.com"),
        ("cloud", "tr-7753246", "account_service", "lookup_key", "cchukwu7021@gmail.com"),
    ],
)
def test_parse_line_each_format(fmt, trace, service, key, value):
    line = parse_line(LINES[fmt])
    assert line is not None
    assert line.fmt == fmt
    assert line.trace_id == trace
    assert line.service == service
    assert line.kv[key] == value
    assert "event" in line.kv


def test_parse_line_null_becomes_none():
    line = parse_line(
        "2026-09-11T04:50:55Z level=DEBUG service=analytics_platform trace_id=rb-1 "
        "event=analytics_profile_read_started resource_id=analytics_5059 lookup_key=null"
    )
    assert line.kv["lookup_key"] is None


@pytest.mark.parametrize(
    "file_id,resource,key,present",
    [
        ("evidence_01", "search_1874", "g.silva76.jr@gmail.com", True),  # a: key=value
        ("evidence_16", "account_7947", "l.nascimento40@gmail.com", False),  # b: bracket
        ("evidence_21", "account_4512", "mei-tanaka@gmail.com", False),  # c: pipe
        ("evidence_06", "account_6633", "collins.grace@icloud.com", True),  # d: JSON
        ("evidence_11", "account_1785", "nana.b9576@outlook.com", True),  # e: syslog
        ("evidence_25", "account_4331", "cchukwu7021@gmail.com", False),  # f: cloud
    ],
)
def test_trace_grouping_attaches_lookup_key(observations, file_id, resource, key, present):
    found = obs(observations, file_id, resource)
    linked = [o for o in found if key in o.link_keys]
    assert linked, [sorted(o.link_keys) for o in found]
    assert linked[0].present is present
    assert linked[0].copy == "log"


def test_same_resource_in_two_traces_gives_two_observations(observations):
    found = obs(observations, "evidence_21", "account_1956")
    assert sorted(o.present for o in found) == [False, True]


def test_ledger_log_lines(observations):
    found = obs(observations, "evidence_41", "account_022")
    assert found[0].present is False
    assert found[0].attrs["record_type"] == "USER_PROFILE"
    assert "zofia-novak@gmail.com" in found[0].link_keys


# ---------- global invariants ----------


def test_every_observation_is_well_formed(observations):
    assert len(observations) > 1000
    for o in observations:
        assert isinstance(o, Observation)
        assert o.captured_at is not None and o.observed_at is not None
        assert RESOURCE_ID_RE.fullmatch(o.resource_id), o.raw_ref
        assert o.system_id == canonical(o.resource_id), o.raw_ref
        assert o.present in (True, False, None)
        assert all(not k.startswith("name:") or len(k) > 5 for k in o.link_keys)


def test_coverages_are_well_formed(coverages):
    assert coverages
    for c in coverages:
        assert isinstance(c, Coverage)
        assert c.system_id and c.scope and c.captured_at is not None


def test_png_files_are_deferred_and_nothing_else_unparsed():
    results = registry.parse_results()
    assert len(results) == 94
    for fid, r in results.items():
        if r.path.endswith(".png"):
            assert r.family == "deferred:P4"
        else:
            assert not r.family.startswith(("deferred", "unknown")), (fid, r.family)
            assert r.unparsed == 0, (fid, r.unparsed)


def test_parse_all_is_cached():
    assert registry.parse_all() is registry.parse_all()
