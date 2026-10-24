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
            result = _result_from_cache(provider, cached, json_mode)
            CALLS["cached"] += 1
            if not offline:  # offline replays are free; they must not grow the ledger
                _ledger(
                    provider=name,
                    model=result.model,
                    purpose=purpose,
                    cache_hit=True,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                )
            return result
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
        shown = [i for i in ids if "flash" in i] if p.name == "gemini" else ids
        print(f"{p.name} models ({len(shown)} of {len(ids)}):")
        for i in shown:
            print(f"  {i}")
        if p.model:
            print(f"{p.name} configured model: {p.model}")
            warning = model_warning(p.model, ids)
            if warning:
                print(warning)
    return 0


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
