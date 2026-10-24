"""Request-intent classifier: rules, LLM batching/validation, and wiring."""

import json
from types import SimpleNamespace

import pytest

from ghost import classifier, llm, loaders

NON_DELETION_IDS = {f"case_{n}" for n in range(533, 543)}


# ---------- rules ----------


@pytest.mark.parametrize(
    "text,label",
    [
        ("Please delete my account and all my personal data.", "DELETION"),
        ("Please erase everything you hold about me.", "DELETION"),
        ("Quiero que eliminen mi cuenta y mis datos personales.", "DELETION"),
        ("Por favor, borrar mi cuenta.", "DELETION"),
        ("Veuillez supprimer mon compte et mes données.", "DELETION"),
        ("Gostaria de solicitar a exclusão da minha conta.", "DELETION"),
        ("アカウントの削除をお願いします。", "DELETION"),
        ("계정 삭제를 요청합니다.", "DELETION"),
        ("Please verify that my earlier deletion really happened.", "DELETION"),
        ("Audit whether my account was removed; the request failed last time.", "DELETION"),
        ("Please confirm my data is gone and was deleted.", "DELETION"),
        ("Change my address but keep my account.", "NOT_DELETION"),
        ("Send me my data, don't delete it.", "NOT_DELETION"),
        ("Update my phone and leave the account active.", "NOT_DELETION"),
        ("I am not asking for deletion, just stop the newsletters.", "NOT_DELETION"),
        ("Unsubscribe me from marketing lists only.", "NOT_DELETION"),
        (
            "Quiero cambiar mi correo, no eliminar la cuenta; mantener la cuenta activa.",
            "NOT_DELETION",
        ),
        (
            "Corrigez mon nom, mais ne pas supprimer mon compte; je veux garder le compte.",
            "NOT_DELETION",
        ),
        (
            "Preciso atualizar o telefone, não excluir a conta; manter a conta ativa.",
            "NOT_DELETION",
        ),
        ("What is the weather today?", "UNSURE"),
    ],
)
def test_rule_labels(text, label):
    assert classifier.rule_label(text) == label


# ---------- LLM batching and validation ----------


def fake_chat(script):
    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        outcome = script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(data=outcome, cache_hit=False)

    return chat, calls


def results(*items):
    return {"results": [{"id": i, "label": lab, "reason": "r"} for i, lab in items]}


def test_batches_of_forty_and_dedup(monkeypatch):
    texts = [f"text {i}" for i in range(85)]
    script = [
        results(*[(i, "DELETION") for i in range(40)]),
        results(*[(i, "NOT_DELETION") for i in range(40)]),
        results(*[(i, "DELETION") for i in range(5)]),
    ]
    chat, calls = fake_chat(script)
    monkeypatch.setattr(classifier.llm, "chat", chat)
    out = classifier.llm_labels(texts + texts[:3])
    assert len(calls) == 3 and len(out) == 85
    assert out["text 0"][0] == "DELETION" and out["text 40"][0] == "NOT_DELETION"
    kw = calls[0][1]
    assert kw["purpose"] == "classify_request" and kw["providers"] == ("gemini",)
    assert json.loads(calls[0][0][-1]["content"])[0] == {"id": 0, "text": "text 0"}


def test_missing_id_retries_batch_once_then_rule_fallback(monkeypatch):
    texts = ["Please delete my account.", "Please keep the account, only change my phone."]
    partial = results((0, "DELETION"))
    chat, calls = fake_chat([partial, partial, partial, partial])
    monkeypatch.setattr(classifier.llm, "chat", chat)
    out = classifier.llm_labels(texts)
    assert len(calls) == 2  # one retry only
    assert texts[0] in out and texts[1] not in out  # missing id is left to the rules
    final = classifier.classify_texts(texts, use_llm=True)
    assert final[texts[1]].source == "rules" and final[texts[1]].final == "NOT_DELETION"


def test_invalid_label_or_duplicate_id_counts_as_invalid(monkeypatch):
    bad = {"results": [{"id": 0, "label": "MAYBE", "reason": "r"}]}
    dup = results((0, "DELETION"), (0, "DELETION"))
    chat, calls = fake_chat([bad, dup])
    monkeypatch.setattr(classifier.llm, "chat", chat)
    assert classifier.llm_labels(["x"]) == {}
    assert len(calls) == 2


def test_llm_error_falls_back_to_rules(monkeypatch):
    chat, _ = fake_chat([llm.LLMError("down"), llm.LLMError("down")])
    monkeypatch.setattr(classifier.llm, "chat", chat)
    out = classifier.classify_texts(["Please delete my account."], use_llm=True)
    assert out["Please delete my account."].source == "rules"


def test_unsure_defaults_to_deletion_and_llm_wins(monkeypatch):
    c = classifier.decide("What is the weather?", None)
    assert c.final == "DELETION" and c.source == "rules"
    c = classifier.decide("Please delete my account.", ("NOT_DELETION", "why"))
    assert c.final == "NOT_DELETION" and c.source == "llm" and c.rule_label == "DELETION"


# ---------- real data (cached run) ----------


@pytest.fixture(scope="module")
def decisions():
    return classifier.classify_requests(use_llm=False)  # cached labels, else rules


@pytest.fixture(scope="module")
def requests_all():
    return {r.case_id: r for s in ("sample", "full") for r in loaders.load_requests(s)}


def test_non_deletion_set_is_exactly_533_to_542(decisions, requests_all):
    got = {c for c, r in requests_all.items() if decisions[r.request_text].final == "NOT_DELETION"}
    assert got == NON_DELETION_IDS
    assert not any(c in got for c in (r.case_id for r in loaders.load_requests("sample")))


def test_rules_alone_find_the_same_ten(requests_all):
    got = {
        c
        for c, r in requests_all.items()
        if classifier.rule_label(r.request_text) == "NOT_DELETION"
    }
    assert got == NON_DELETION_IDS


def test_llm_labels_are_cached(decisions):
    assert sum(1 for d in decisions.values() if d.source == "llm") == len(decisions) == 398


# ---------- wiring ----------


def test_non_deletion_cases_own_no_worker_resources_and_are_not_applicable():
    import main as cli

    ctx = cli.build_context(use_llm=False)
    assert ctx.lk.non_deletion == frozenset(NON_DELETION_IDS)
    for case_id in NON_DELETION_IDS:
        assert not any(v[0] == "worker_window" for r in ctx.lk.link(case_id) for v in r.via)
        req = {"case_id": case_id}
        assert cli.predict_case(req, ctx) == ("NOT_APPLICABLE", [])

