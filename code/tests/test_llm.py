"""llm.py: provider fallback, retries, disk cache and usage ledger.

The OpenAI client is replaced by a scripted fake; nothing here touches the network.
"""

import base64
import json
from types import SimpleNamespace

import openai
import pytest

from ghost import llm

try:  # openai >= 3 ships its HTTP layer as httpx2
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

REQ = httpx.Request("POST", "http://test.invalid/chat/completions")


def api_error(cls, status: int):
    return cls("boom", response=httpx.Response(status, request=REQ), body=None)


def completion(text: str, prompt_tokens=11, completion_tokens=7):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """create() pops scripted outcomes: an Exception is raised, anything else is returned."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(llm, "_ENV_LOADED", True)  # never read the real .env in tests
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GEMINI_MODEL", "gem-test")
    monkeypatch.setenv("GROQ_MODEL", "groq-test")
    return tmp_path


def install(monkeypatch, **clients):
    made: list[str] = []

    def make(provider):
        made.append(provider.name)
        return clients[provider.name]

    monkeypatch.setattr(llm, "_make_client", make)
    return made


MSGS = [{"role": "user", "content": "say hi as json"}]
OK = '{"ok": true}'


def ledger(env):
    return [json.loads(x) for x in (env / "ledger.jsonl").read_text().splitlines()]


def test_cache_hit_skips_network(env, monkeypatch):
    gem = FakeClient([completion(OK)])
    install(monkeypatch, gemini=gem)
    first = llm.chat(MSGS, purpose="t")
    second = llm.chat(MSGS, purpose="t")
    assert len(gem.calls) == 1
    assert (first.cache_hit, second.cache_hit) == (False, True)
    assert second.data == {"ok": True} and second.provider == "gemini"
    assert len(list((env / "cache").glob("*.json"))) == 1


def test_cache_is_served_without_any_key(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t")
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GROQ_API_KEY")
    made = install(monkeypatch)  # any client construction would KeyError
    again = llm.chat(MSGS, purpose="t")
    assert again.cache_hit and again.data == {"ok": True}
    assert made == []


def test_cache_file_holds_no_key_and_no_image(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t", image_bytes=b"\x89PNGsecretpixels")
    blob = next((env / "cache").glob("*.json")).read_text()
    assert "test-gemini" not in blob
    assert base64.b64encode(b"\x89PNGsecretpixels").decode() not in blob
    stored = json.loads(blob)
    assert stored["response_text"] == OK and stored["model"] == "gem-test"
    assert stored["usage"] == {"prompt_tokens": 11, "completion_tokens": 7}


def test_rate_limit_then_success_retries(env, monkeypatch):
    gem = FakeClient([api_error(openai.RateLimitError, 429), completion(OK)])
    install(monkeypatch, gemini=gem)
    res = llm.chat(MSGS, purpose="t")
    assert len(gem.calls) == 2
    assert res.provider == "gemini" and res.data == {"ok": True}


def test_gemini_failing_falls_back_to_groq_for_text(env, monkeypatch):
    gem = FakeClient([api_error(openai.InternalServerError, 503)] * 3)
    groq = FakeClient([completion(OK)])
    install(monkeypatch, gemini=gem, groq=groq)
    res = llm.chat(MSGS, purpose="t")
    assert len(gem.calls) == 3  # three tries, then give up on gemini
    assert len(groq.calls) == 1
    assert res.provider == "groq" and res.model == "groq-test"


def test_image_call_never_goes_to_groq(env, monkeypatch):
    gem = FakeClient([api_error(openai.InternalServerError, 500)] * 3)
    groq = FakeClient([completion(OK)])
    made = install(monkeypatch, gemini=gem, groq=groq)
    with pytest.raises(llm.LLMError):
        llm.chat(MSGS, purpose="t", image_bytes=b"png")
    assert groq.calls == [] and "groq" not in made


def test_image_is_sent_as_data_url_with_temperature_zero(env, monkeypatch):
    gem = FakeClient([completion(OK)])
    install(monkeypatch, gemini=gem)
    llm.chat(MSGS, purpose="t", image_bytes=b"png-bytes")
    call = gem.calls[0]
    assert call["temperature"] == 0 and call["model"] == "gem-test"
    assert call["response_format"] == {"type": "json_object"}
    parts = call["messages"][-1]["content"]
    url = next(p["image_url"]["url"] for p in parts if p["type"] == "image_url")
    assert url == "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()


def test_missing_key_provider_is_skipped(env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    groq = FakeClient([completion(OK)])
    made = install(monkeypatch, groq=groq)
    res = llm.chat(MSGS, purpose="t")
    assert res.provider == "groq" and made == ["groq"]


def test_provider_without_model_is_skipped(env, monkeypatch):
    monkeypatch.delenv("GROQ_MODEL")
    monkeypatch.delenv("GEMINI_API_KEY")
    made = install(monkeypatch)
    with pytest.raises(llm.LLMError):
        llm.chat(MSGS, purpose="t")
    assert made == []


def test_ledger_line_is_written_for_fresh_calls_only(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK, 123, 45)]))
    before = llm.CALLS["cached"]
    llm.chat(MSGS, purpose="classify")
    llm.chat(MSGS, purpose="classify")  # cache hit: counted, never written to the ledger
    (first,) = ledger(env)
    assert first["provider"] == "gemini" and first["model"] == "gem-test"
    assert first["purpose"] == "classify" and first["cache_hit"] is False
    assert (first["prompt_tokens"], first["completion_tokens"]) == (123, 45)
    assert isinstance(first["latency_ms"], int) and first["run_id"] and first["ts"]
    assert llm.CALLS["cached"] == before + 1


def test_missing_usage_is_recorded_as_none(env, monkeypatch):
    bare = SimpleNamespace(choices=completion(OK).choices)  # no .usage at all
    install(monkeypatch, gemini=FakeClient([bare]))
    llm.chat(MSGS, purpose="t")
    (line,) = ledger(env)
    assert line["prompt_tokens"] is None and line["completion_tokens"] is None


def test_json_mode_rejected_retries_without_response_format(env, monkeypatch):
    gem = FakeClient([api_error(openai.BadRequestError, 400), completion('```json\n{"a": 1}\n```')])
    install(monkeypatch, gemini=gem)
    res = llm.chat(MSGS, purpose="t")
    assert "response_format" in gem.calls[0] and "response_format" not in gem.calls[1]
    assert res.data == {"a": 1}


def test_unparseable_json_is_not_cached_and_fails_over(env, monkeypatch):
    gem = FakeClient([completion("sorry, no json")])
    groq = FakeClient([completion(OK)])
    install(monkeypatch, gemini=gem, groq=groq)
    res = llm.chat(MSGS, purpose="t")
    assert res.provider == "groq"
    assert len(list((env / "cache").glob("*.json"))) == 1


def test_offline_uses_cache_only(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t")
    made = install(monkeypatch)
    assert llm.chat(MSGS, purpose="t", offline=True).cache_hit
    with pytest.raises(llm.LLMError):
        llm.chat(MSGS, purpose="other", offline=True)
    assert made == []


def test_offline_finds_entry_made_with_another_model_name(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t", image_bytes=b"img")
    monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
    res = llm.chat(MSGS, purpose="t", image_bytes=b"img", offline=True)
    assert res.cache_hit and res.model == "gem-test"


def test_close_model_warning_names_near_matches():
    ids = ["models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-3.0-flash"]
    warning = llm.model_warning("gemini-3.8-flash", ids)
    assert warning and "gemini-3.0-flash" in warning
    assert llm.model_warning("gemini-2.5-flash", ids) is None


def test_offline_replay_writes_no_ledger_line(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t")
    llm.chat(MSGS, purpose="t", offline=True)
    assert len(ledger(env)) == 1  # replays are free and must not grow the committed ledger


def test_online_call_reuses_entry_made_under_another_model_name(env, monkeypatch):
    install(monkeypatch, gemini=FakeClient([completion(OK)]))
    llm.chat(MSGS, purpose="t")
    monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
    gem = FakeClient([])  # any network call would pop from an empty script
    install(monkeypatch, gemini=gem)
    res = llm.chat(MSGS, purpose="t")
    assert res.cache_hit and gem.calls == []
