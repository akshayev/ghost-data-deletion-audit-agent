"""OpenAI-SDK wrapper with provider fallback, a disk cache and a usage ledger.

    python -m ghost.llm --check      # which keys are set (true/false only) + model list

Providers are configuration. Every call is cached on disk, so a committed cache makes
the pipeline reproducible without any key. API keys are read from the environment
(.env is loaded with python-dotenv) and are never printed, cached or logged.
"""

import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import openai
from dotenv import load_dotenv

from ghost import paths

CACHE_DIR = paths.CODE / "cache" / "llm"
LEDGER_PATH = paths.CODE / "cache" / "usage_ledger.jsonl"
RUN_ID = os.getenv("GHOST_RUN_ID") or uuid.uuid4().hex[:12]
MAX_TRIES = 3
BACKOFF_SECONDS = 2.0

_ENV_LOADED = False
_sleep = time.sleep
CALLS: Counter = Counter()  # this process: "fresh" network calls vs "cached" replays


class LLMError(RuntimeError):
    """No provider could answer (never contains key material)."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str | None
    vision: bool

    @property
    def model(self) -> str | None:
        return os.getenv(self.model_env) or self.default_model

    @property
    def has_key(self) -> bool:
        return bool(os.getenv(self.key_env))


PROVIDERS = {
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "gemini-3.8-flash",
        vision=True,
    ),
    "groq": Provider(
        "groq",
        "https://api.groq.com/openai/v1",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        None,
        vision=False,
    ),
}


@dataclass(frozen=True)
class LLMResult:
    text: str
    data: object  # parsed JSON when json_mode, else None
    provider: str
    model: str
    cache_hit: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _load_env() -> None:
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv(paths.REPO_ROOT / ".env")
        _ENV_LOADED = True


def _make_client(provider: Provider) -> openai.OpenAI:
    # max_retries=0: retry/backoff is ours, so every attempt is visible and bounded.
    return openai.OpenAI(
        api_key=os.environ[provider.key_env], base_url=provider.base_url, max_retries=0
    )


def _redact(text: str) -> str:
    for p in PROVIDERS.values():
        secret = os.getenv(p.key_env)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _messages_sha(messages: list[dict]) -> str:
    return _sha(json.dumps(messages, sort_keys=True, ensure_ascii=False))


def _cache_key(provider: str, model: str, purpose: str, messages: list[dict], image_sha: str):
    raw = "|".join([provider, model, purpose, _messages_sha(messages), image_sha])
    return _sha(raw)


def parse_json(text: str):
    """JSON from a reply that may be wrapped in a code fence or surrounded by prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON found in reply")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


def _with_image(messages: list[dict], image_bytes: bytes) -> list[dict]:
    url = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
    out = [dict(m) for m in messages]
    last = out[-1]
    content = last["content"]
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    parts.append({"type": "image_url", "image_url": {"url": url}})
    last["content"] = parts
    return out


def _usage(response) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage", None)
    return getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)


def _ledger(**fields) -> None:
    line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **fields, "run_id": RUN_ID}
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _read_cache(path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_cached(
    provider: Provider, purpose: str, messages: list[dict], image_sha: str
) -> dict | None:
    """Exact key first, then an entry for the same prompt/image made under another model
    name (a model switch, e.g. after a quota limit, must not invalidate the cache)."""
    model = provider.model
    if model:
        key = _cache_key(provider.name, model, purpose, messages, image_sha)
        hit = _read_cache(CACHE_DIR / f"{key}.json")
        if hit:
            return hit
    if not CACHE_DIR.is_dir():
        return None
    want = {
        "provider": provider.name,
        "purpose": purpose,
        "messages_sha256": _messages_sha(messages),
        "image_sha256": image_sha,
    }
    for path in sorted(CACHE_DIR.glob("*.json")):
        entry = _read_cache(path)
        if entry and all(entry.get("request", {}).get(k) == v for k, v in want.items()):
            return entry
    return None


def _result_from_cache(provider: Provider, entry: dict, json_mode: bool) -> LLMResult:
    text = entry["response_text"]
    return LLMResult(
        text=text,
        data=parse_json(text) if json_mode else None,
        provider=provider.name,
        model=entry.get("model", ""),
        cache_hit=True,
        prompt_tokens=0,
        completion_tokens=0,
    )


def _call(client, provider: Provider, messages, json_mode: bool):
    """One provider, up to MAX_TRIES attempts with exponential backoff on 429/5xx/connection."""
    kwargs = {"model": provider.model, "messages": messages, "temperature": 0}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    attempt = 0
    while True:
        attempt += 1
        try:
            return client.chat.completions.create(**kwargs)
        except openai.BadRequestError:
            if "response_format" not in kwargs:
                raise
            del kwargs["response_format"]  # provider rejects JSON mode: parse it from the text
            attempt -= 1
        except (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError):
            if attempt >= MAX_TRIES:
                raise
            _sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))


def chat(
    messages: list[dict],
    *,
    purpose: str,
    json_mode: bool = True,
    image_bytes: bytes | None = None,
    providers: tuple[str, ...] = ("gemini", "groq"),
    offline: bool = False,
    model_env: str | None = None,
) -> LLMResult:
    """Ask the first provider that can answer. Cache is consulted before keys, so a
    committed cache works without credentials. offline=True never calls the network."""
    _load_env()
    image_sha = _sha(image_bytes) if image_bytes else ""
    failures: list[str] = []
    for name in providers:
        provider = PROVIDERS[name]
        if model_env and os.getenv(model_env) and name == "gemini":
            provider = replace(provider, model_env=model_env)  # e.g. GEMINI_TEXT_MODEL
        if image_bytes and not provider.vision:
            continue
        cached = _find_cached(provider, purpose, messages, image_sha)
        if cached:
            # replays are free: counted in CALLS (run_summary.json), never in the ledger,
            # so the committed ledger records real API usage only
            CALLS["cached"] += 1
            return _result_from_cache(provider, cached, json_mode)
        if offline or not provider.has_key or not provider.model:
            continue
        sent = _with_image(messages, image_bytes) if image_bytes else messages
        started = time.monotonic()
        try:
            response = _call(_make_client(provider), provider, sent, json_mode)
            text = response.choices[0].message.content or ""
            data = parse_json(text) if json_mode else None
        except (openai.OpenAIError, ValueError, IndexError, AttributeError) as exc:
            failures.append(f"{name}: {type(exc).__name__}: {_redact(str(exc))[:200]}")
            continue
        latency_ms = int((time.monotonic() - started) * 1000)
        CALLS["fresh"] += 1
        prompt_tokens, completion_tokens = _usage(response)
        key = _cache_key(name, provider.model, purpose, messages, image_sha)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "request": {
                "provider": name,
                "purpose": purpose,
                "json_mode": json_mode,
                "messages_sha256": _messages_sha(messages),
                "image_sha256": image_sha,
            },
            "model": provider.model,
            "response_text": text,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        _ledger(
            provider=name,
            model=provider.model,
            purpose=purpose,
            cache_hit=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        return LLMResult(text, data, name, provider.model, False, prompt_tokens, completion_tokens)
    detail = "; ".join(failures) or "no provider available (cache miss, key or model missing)"
    raise LLMError(f"{purpose}: {detail}")


# ---------------------------------------------------------------------------
# Tool calling (the case investigator agent): provider/model chain, rate limits, quota
# ---------------------------------------------------------------------------

GROQ_FALLBACK_ENV = "GROQ_FALLBACK_MODEL"
GROQ_EXTRA_ENV = "GROQ_EXTRA_MODELS"  # optional, comma-separated groq model ids
# (provider, model) -> requests/min and tokens/min (sliding 60 s window); config can override
RATE_LIMITS: dict[str, tuple[int, int]] = {"groq": (25, 7000), "gemini": (10, 250_000)}
RATE_TRIES = 4  # 429 retries per model before moving on
MAX_RETRY_AFTER = 90.0
AGENT_MAX_TOKENS = 1500
EXHAUSTED: set[tuple[str, str]] = set()  # daily quota hit: skipped for the rest of the run
LIMITERS: dict[tuple[str, str], "RateLimiter"] = {}
TOKENS: Counter = Counter()  # (provider, model, "prompt"|"completion"|"calls") -> count
_DAILY_RE = re.compile(r"per day|\bTPD\b|\bRPD\b|PerDay|daily", re.IGNORECASE)


class QuotaExhausted(LLMError):
    """Every model in the chain hit its daily quota."""


class CacheMiss(LLMError):
    """offline=True and no cached reply exists."""


class RequestTooLarge(LLMError):
    """One request is larger than the model's tokens-per-minute budget."""


class RateLimiter:
    """Client-side sliding-window limiter: requests/min and (estimated) tokens/min."""

    def __init__(self, rpm: int, tpm: int, clock=time.monotonic, sleep=None) -> None:
        self.rpm, self.tpm = rpm, tpm
        self.clock, self.sleep = clock, sleep or (lambda s: _sleep(s))
        self.window: list[list[float]] = []  # [time, tokens]

    def _trim(self) -> None:
        cutoff = self.clock() - 60.0
        self.window = [w for w in self.window if w[0] > cutoff]

    def acquire(self, est_tokens: int) -> None:
        if est_tokens > self.tpm:
            raise RequestTooLarge(f"request ~{est_tokens} tokens > {self.tpm} tokens/min")
        while True:
            self._trim()
            used = sum(w[1] for w in self.window)
            if len(self.window) < self.rpm and used + est_tokens <= self.tpm:
                self.window.append([self.clock(), float(est_tokens)])
                return
            self.sleep(max(0.5, self.window[0][0] + 60.0 - self.clock()))

    def record(self, tokens: int | None) -> None:
        """Replace the newest estimate with the real token count once it is known."""
        if self.window and tokens:
            self.window[-1][1] = float(tokens)


def _limiter(provider: str, model: str) -> RateLimiter:
    if (provider, model) not in LIMITERS:
        rpm, tpm = RATE_LIMITS.get(provider, (10, 100_000))
        LIMITERS[(provider, model)] = RateLimiter(rpm, tpm)
    return LIMITERS[(provider, model)]


def groq_extra_models() -> list[str]:
    return [m.strip() for m in os.getenv(GROQ_EXTRA_ENV, "").split(",") if m.strip()]


def agent_chain() -> list[tuple[str, str]]:
    """groq:GROQ_MODEL -> groq:GROQ_FALLBACK_MODEL -> groq:GROQ_EXTRA_MODELS -> gemini:GEMINI_MODEL
    (key and model set; duplicates dropped)."""
    _load_env()
    candidates = [("groq", os.getenv("GROQ_MODEL")), ("groq", os.getenv(GROQ_FALLBACK_ENV))]
    candidates += [("groq", m) for m in groq_extra_models()]
    candidates.append(("gemini", PROVIDERS["gemini"].model))
    out = []
    for name, model in candidates:
        if model and PROVIDERS[name].has_key and (name, model) not in out:
            out.append((name, model))
    return out


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict | None  # None when the model sent unparseable arguments
    error: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class ToolReply:
    content: str
    tool_calls: tuple[ToolCall, ...]
    provider: str
    model: str
    cache_hit: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def assistant_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw}}
                for c in self.tool_calls
            ]
        return msg


def parse_tool_call(call_id: str, name: str, arguments) -> ToolCall:
    """Arguments may arrive as a JSON string or an object; bad JSON is an error for the model."""
    if isinstance(arguments, dict):
        return ToolCall(call_id, name, arguments, None, json.dumps(arguments, sort_keys=True))
    raw = arguments or "{}"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return ToolCall(call_id, name, None, f"invalid JSON arguments: {exc}", str(raw))
    if not isinstance(parsed, dict):
        return ToolCall(call_id, name, None, "arguments must be a JSON object", str(raw))
    return ToolCall(call_id, name, parsed, None, raw)


def _tool_cache_key(purpose: str, messages: list[dict], tools: list[dict]) -> str:
    return _sha("|".join(["tools", purpose, _messages_sha(messages), _messages_sha(tools)]))


def _reply_from_entry(entry: dict, cache_hit: bool) -> ToolReply:
    calls = tuple(parse_tool_call(c["id"], c["name"], c["arguments"])
                  for c in entry["message"].get("tool_calls", ()))
    usage = entry.get("usage", {})
    return ToolReply(entry["message"].get("content") or "", calls, entry["provider"],
                     entry["model"], cache_hit,
                     0 if cache_hit else usage.get("prompt_tokens"),
                     0 if cache_hit else usage.get("completion_tokens"))


def _is_daily(exc: Exception) -> bool:
    body = getattr(exc, "body", None)
    return bool(_DAILY_RE.search(f"{exc} {json.dumps(body, default=str) if body else ''}"))


def _retry_after(exc: Exception, attempt: int) -> float:
    response = getattr(exc, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return min(float(value), MAX_RETRY_AFTER) if value else BACKOFF_SECONDS * 2 ** attempt
    except ValueError:
        return BACKOFF_SECONDS * 2 ** attempt


def _create_tool_call(provider: Provider, model: str, messages, tools):
    kwargs = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
              "temperature": 0, "max_tokens": AGENT_MAX_TOKENS}
    if provider.name == "groq":
        kwargs["reasoning_effort"] = "low"
    client = _make_client(replace(provider, model_env="", default_model=model))
    try:
        return client.chat.completions.create(**kwargs)
    except openai.BadRequestError as exc:
        if "reasoning_effort" in kwargs and "reasoning" in str(exc).lower():
            del kwargs["reasoning_effort"]
            return client.chat.completions.create(**kwargs)
        raise


def tool_chat(
    messages: list[dict],
    tools: list[dict],
    *,
    purpose: str = "case_agent",
    case_id: str | None = None,
    turn: int | None = None,
    chain: list[tuple[str, str]] | None = None,
    offline: bool = False,
) -> ToolReply:
    """One tool-calling turn. Cache first (keyed on messages + tool schemas, any model), then
    the chain in order; a daily-quota error retires that model for the run."""
    _load_env()
    key = _tool_cache_key(purpose, messages, tools)
    cached = _read_cache(CACHE_DIR / f"{key}.json")
    if cached:
        CALLS["cached"] += 1
        return _reply_from_entry(cached, cache_hit=True)
    if offline:
        raise CacheMiss(f"{purpose}: no cached reply (offline)")
    chain = agent_chain() if chain is None else chain
    failures: list[str] = []
    est = len(json.dumps(messages, ensure_ascii=False) + json.dumps(tools)) // 4
    for name, model in chain:
        if (name, model) in EXHAUSTED:
            failures.append(f"{name}:{model}: exhausted")
            continue
        provider = PROVIDERS[name]
        limiter = _limiter(name, model)
        response = None
        for attempt in range(RATE_TRIES):
            try:
                limiter.acquire(est)
                started = time.monotonic()
                response = _create_tool_call(provider, model, messages, tools)
                break
            except RequestTooLarge as exc:
                failures.append(f"{name}:{model}: {exc}")
                break
            except openai.RateLimitError as exc:
                if _is_daily(exc):
                    EXHAUSTED.add((name, model))
                    failures.append(f"{name}:{model}: daily quota")
                    break
                _sleep(_retry_after(exc, attempt))
            except openai.APIStatusError as exc:
                failures.append(f"{name}:{model}: {type(exc).__name__}: {_redact(str(exc))[:200]}")
                if exc.status_code >= 500 and attempt + 1 < RATE_TRIES:
                    _sleep(BACKOFF_SECONDS * 2 ** attempt)
                    continue
                break
            except openai.OpenAIError as exc:
                failures.append(f"{name}:{model}: {type(exc).__name__}: {_redact(str(exc))[:200]}")
                break
        else:
            failures.append(f"{name}:{model}: still rate limited after {RATE_TRIES} tries")
        if response is None:
            continue
        latency_ms = int((time.monotonic() - started) * 1000)
        prompt_tokens, completion_tokens = _usage(response)
        limiter.record((prompt_tokens or 0) + (completion_tokens or 0) or None)
        message = response.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                 for c in (getattr(message, "tool_calls", None) or ())]
        entry = {
            "request": {"purpose": purpose, "messages_sha256": _messages_sha(messages),
                        "tools_sha256": _messages_sha(tools), "case_id": case_id, "turn": turn},
            "provider": name,
            "model": getattr(response, "model", None) or model,
            "message": {"content": message.content or "", "tool_calls": calls},
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        CALLS["fresh"] += 1
        TOKENS[(name, entry["model"], "calls")] += 1
        TOKENS[(name, entry["model"], "prompt")] += prompt_tokens or 0
        TOKENS[(name, entry["model"], "completion")] += completion_tokens or 0
        _ledger(provider=name, model=entry["model"], purpose=purpose, case_id=case_id, turn=turn,
                cache_hit=False, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, latency_ms=latency_ms)
        return _reply_from_entry(entry, cache_hit=False)
    if chain and all((n, m) in EXHAUSTED for n, m in chain):
        raise QuotaExhausted(f"{purpose}: every model hit its daily quota")
    raise LLMError(f"{purpose}: " + ("; ".join(failures) or "no tool-capable model configured"))


PING_TOOL = [{"type": "function", "function": {
    "name": "ping", "description": "Reply to a connectivity check.",
    "parameters": {"type": "object", "properties": {"word": {"type": "string"}},
                   "required": ["word"]}}}]


def check_tool_calls() -> list[tuple[str, str, bool, str]]:
    """One tool-call round trip per configured agent model (never cached; ledgered)."""
    out = []
    msgs = [{"role": "user", "content": "Call the ping tool with word='ok'."}]
    for name, model in agent_chain():
        started = time.monotonic()
        try:
            response = _create_tool_call(PROVIDERS[name], model, msgs, PING_TOOL)
            calls = getattr(response.choices[0].message, "tool_calls", None) or []
            ok = bool(calls) and calls[0].function.name == "ping"
            pt, ct = _usage(response)
            _ledger(provider=name, model=model, purpose="check", case_id=None, turn=None,
                    cache_hit=False, prompt_tokens=pt, completion_tokens=ct,
                    latency_ms=int((time.monotonic() - started) * 1000))
            detail = f"response model id: {getattr(response, 'model', '?')}"
            if calls:
                detail += f", tool={calls[0].function.name} args={calls[0].function.arguments}"
            out.append((name, model, ok, detail))
        except openai.OpenAIError as exc:
            out.append((name, model, False, f"{type(exc).__name__}: {_redact(str(exc))[:160]}"))
    return out


def model_warning(configured: str, model_ids: list[str]) -> str | None:
    """None if the configured model is listed; otherwise a warning naming close matches."""
    ids = [m.removeprefix("models/") for m in model_ids]
    if configured in ids:
        return None
    close = difflib.get_close_matches(configured, ids, n=5, cutoff=0.6)
    hint = f" Close matches: {', '.join(close)}." if close else " No close matches."
    return f"WARNING: model {configured!r} is not in the provider's model list.{hint}"


def check() -> int:
    _load_env()
    print("providers:")
    for p in PROVIDERS.values():
        print(f"  {p.name}: key_set={p.has_key} model_set={bool(p.model)} vision={p.vision}")
    for p in PROVIDERS.values():
        if not p.has_key:
            continue
        try:
            ids = sorted(m.id for m in _make_client(p).models.list())
        except openai.OpenAIError as exc:
            print(f"{p.name}: models.list failed: {type(exc).__name__}: {_redact(str(exc))[:200]}")
            continue
        configured = [m for m in (p.model, os.getenv(GROQ_FALLBACK_ENV) if p.name == "groq"
                                  else None) if m]
        if p.name == "groq":
            configured += groq_extra_models()
        for model in configured:
            warning = model_warning(model, ids)
            print(f"{p.name} configured model {model}: {warning or 'listed'}")
    print("tool-call round trip (agent chain):")
    results = check_tool_calls()
    for name, model, ok, detail in results:
        print(f"  {name}:{model}: {'ok' if ok else 'FAIL'} ({detail})")
    return 0 if results and any(ok for *_, ok, _ in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ghost.llm")
    parser.add_argument("--check", action="store_true", help="show keys set + model ids")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
