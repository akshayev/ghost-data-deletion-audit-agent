"""llm.tool_chat: tool calling, provider/model chain, rate limiting, quota handling, cache.

The OpenAI client is a scripted fake; nothing here touches the network.
"""

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
TOOLS = [{"type": "function", "function": {
    "name": "ping", "description": "echo",
    "parameters": {"type": "object", "properties": {"word": {"type": "string"}}}}}]
MSGS = [{"role": "user", "content": "call ping"}]


def rate_error(message: str, retry_after: str | None = None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError(message, response=httpx.Response(429, request=REQ,
                                                                  headers=headers), body=None)


def reply(arguments='{"word": "ok"}', name="ping", content=None, model="big", pt=50, ct=5):
    call = SimpleNamespace(id="call_1", type="function",
                           function=SimpleNamespace(name=name, arguments=arguments))
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[call]))],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
    )


class FakeClient:
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
    monkeypatch.setattr(llm, "_ENV_LOADED", True)
    slept: list[float] = []
    monkeypatch.setattr(llm, "_sleep", slept.append)
    monkeypatch.setattr(llm, "EXHAUSTED", set())
    monkeypatch.setattr(llm, "LIMITERS", {})
    monkeypatch.delenv("GROQ_EXTRA_MODELS", raising=False)  # the real .env may define extras
    for k, v in {"GROQ_API_KEY": "k1", "GEMINI_API_KEY": "k2", "GROQ_MODEL": "big",
                 "GROQ_FALLBACK_MODEL": "small", "GEMINI_MODEL": "gem"}.items():
        monkeypatch.setenv(k, v)
    return SimpleNamespace(path=tmp_path, slept=slept)


def install(monkeypatch, clients: dict):
    """clients: model name -> FakeClient (one client per model in the chain)."""
    made = []

    def make(provider):
        made.append(provider.model)
        return clients[provider.model]

    monkeypatch.setattr(llm, "_make_client", make)
    return made


def ledger(env):
    path = env.path / "ledger.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_default_chain_is_groq_big_then_groq_small_then_gemini(env):
    assert llm.agent_chain() == [("groq", "big"), ("groq", "small"), ("gemini", "gem")]


def test_extra_groq_models_sit_between_fallback_and_gemini_deduplicated(env, monkeypatch):
    monkeypatch.setenv("GROQ_EXTRA_MODELS", " x1, small ,x2,,x1 ")
    assert llm.agent_chain() == [("groq", "big"), ("groq", "small"), ("groq", "x1"),
                                 ("groq", "x2"), ("gemini", "gem")]


def test_tool_call_is_parsed_and_logged_with_case_and_turn(env, monkeypatch):
    big = FakeClient([reply(model="big")])
    install(monkeypatch, {"big": big})
    r = llm.tool_chat(MSGS, TOOLS, case_id="case_25", turn=3)
    assert r.provider == "groq" and r.model == "big" and not r.cache_hit
    (call,) = r.tool_calls
    assert call.name == "ping" and call.arguments == {"word": "ok"} and call.error is None
    sent = big.calls[0]
    assert sent["tools"] == TOOLS and sent["tool_choice"] == "auto" and sent["temperature"] == 0
    (line,) = ledger(env)
    assert line["purpose"] == "case_agent" and line["case_id"] == "case_25" and line["turn"] == 3
    assert (line["provider"], line["model"], line["prompt_tokens"]) == ("groq", "big", 50)


def test_malformed_arguments_become_a_structured_error_not_an_exception(env, monkeypatch):
    install(monkeypatch, {"big": FakeClient([reply(arguments='{"word": "ok"')])})
    (call,) = llm.tool_chat(MSGS, TOOLS).tool_calls
    assert call.arguments is None and "invalid JSON" in call.error


def test_arguments_given_as_an_object_are_accepted(env, monkeypatch):
    install(monkeypatch, {"big": FakeClient([reply(arguments={"word": "x"})])})
    assert llm.tool_chat(MSGS, TOOLS).tool_calls[0].arguments == {"word": "x"}


def test_429_honours_retry_after_then_succeeds(env, monkeypatch):
    big = FakeClient([rate_error("Rate limit reached: tokens per minute (TPM)", "7"), reply()])
    install(monkeypatch, {"big": big})
    r = llm.tool_chat(MSGS, TOOLS)
    assert len(big.calls) == 2 and r.model == "big"
    assert 7.0 in env.slept


def test_daily_quota_marks_the_model_exhausted_and_moves_to_the_next(env, monkeypatch):
    big = FakeClient([rate_error("Rate limit reached for model `big` on tokens per day (TPD)")])
    small = FakeClient([reply(model="small"), reply(model="small", arguments='{"word": "b"}')])
    made = install(monkeypatch, {"big": big, "small": small})
    first = llm.tool_chat(MSGS, TOOLS)
    assert first.model == "small" and ("groq", "big") in llm.EXHAUSTED
    llm.tool_chat(MSGS + [{"role": "user", "content": "again"}], TOOLS)
    assert made.count("big") == 1  # never tried again in this run


def test_all_models_exhausted_raises_quota_exhausted(env, monkeypatch):
    daily = "quota exceeded: requests per day (RPD)"
    install(monkeypatch, {m: FakeClient([rate_error(daily)]) for m in ("big", "small", "gem")})
    with pytest.raises(llm.QuotaExhausted):
        llm.tool_chat(MSGS, TOOLS)


def test_cache_includes_the_tool_schema_and_replays_offline(env, monkeypatch):
    big = FakeClient([reply(), reply()])
    install(monkeypatch, {"big": big})
    llm.tool_chat(MSGS, TOOLS)
    again = llm.tool_chat(MSGS, TOOLS, offline=True)
    assert again.cache_hit and again.tool_calls[0].arguments == {"word": "ok"}
    other_tools = [dict(TOOLS[0], function=dict(TOOLS[0]["function"], description="changed"))]
    with pytest.raises(llm.LLMError):
        llm.tool_chat(MSGS, other_tools, offline=True)
    assert len(big.calls) == 1 and len(ledger(env)) == 1  # replay: no network, no ledger line


def test_rate_limiter_waits_for_the_token_window():
    now = [0.0]
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    lim = llm.RateLimiter(rpm=25, tpm=7000, clock=lambda: now[0], sleep=sleep)
    lim.acquire(5000)
    lim.record(5000)
    lim.acquire(3000)  # 5000 + 3000 > 7000: must wait for the first call to leave the window
    assert slept and now[0] >= 60.0
    with pytest.raises(llm.RequestTooLarge):
        lim.acquire(8000)


def test_rate_limiter_caps_requests_per_minute():
    now = [0.0]

    def sleep(s):
        now[0] += s

    lim = llm.RateLimiter(rpm=2, tpm=10**6, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        lim.acquire(10)
        lim.record(10)
    assert now[0] >= 60.0
