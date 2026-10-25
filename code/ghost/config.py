"""Run configuration: code/config.yaml with environment and CLI overrides.

Precedence: CLI > environment > yaml > built-in defaults. The defaults equal the values the
pipeline used before the config existed, so a missing or empty config changes nothing.
"""

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ghost import paths

CONFIG_PATH = paths.CODE / "config.yaml"

DEFAULTS: dict = {
    "dataset": "dataset",
    "output": "output.csv",
    "runs_dir": "code/evaluation/runs",
    "cache_dir": "code/cache",
    "trace": True,
    "mode": "auto",  # agent | deterministic | auto (= agent when a tool-capable key is set)
    "agent": {
        "max_tool_calls": 8,
        "max_rejections": 2,
        "max_agent_cases": None,
        "only_cases": None,
        "checkpoint_dir": "code/cache/agent_runs",
        "rate_limits": {"groq": {"rpm": 25, "tpm": 7000}, "gemini": {"rpm": 10, "tpm": 250000}},
    },
    "llm": {
        "providers": ["gemini", "groq"],
        "gemini_model": "gemini-3.8-flash",
        "gemini_text_model": None,
        "groq_model": None,
    },
    "linker": {
        "tiebreak": "SKIP_MULTI",
        "learn_range_hours": 6.0,
        "window_pad_hours": 0.1,
        "default_window": [-1.5, 2.5],
    },
}

# environment variable -> key path in the config
ENV_KEYS = {
    "GHOST_DATASET": ("dataset",),
    "GHOST_TIEBREAK": ("linker", "tiebreak"),
    "GHOST_TRACE": ("trace",),
    "GHOST_MODE": ("mode",),
    "GEMINI_MODEL": ("llm", "gemini_model"),
    "GEMINI_TEXT_MODEL": ("llm", "gemini_text_model"),
    "GROQ_MODEL": ("llm", "groq_model"),
}

# CLI option -> key path
CLI_KEYS = {
    "dataset": ("dataset",),
    "output": ("output",),
    "runs_dir": ("runs_dir",),
    "tiebreak": ("linker", "tiebreak"),
    "trace": ("trace",),
    "mode": ("mode",),
    "max_agent_cases": ("agent", "max_agent_cases"),
    "agent_cases": ("agent", "only_cases"),
}


@dataclass(frozen=True)
class Config:
    dataset: Path
    output: Path
    runs_dir: Path
    cache_dir: Path
    trace: bool
    tiebreak: str
    llm: dict
    linker: dict
    no_llm: bool = False
    mode: str = "deterministic"  # resolved: agent | deterministic
    agent: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict, compare=False)


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _set(d: dict, keys: tuple, value) -> None:
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _env_value(name: str, value: str):
    if name == "GHOST_TRACE":
        return value.strip().lower() not in ("0", "false", "no", "off")
    return value


def _path(value) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else paths.REPO_ROOT / p).resolve()


def load_config(path: Path | str | None = None, cli: dict | None = None) -> Config:
    raw = copy.deepcopy(DEFAULTS)
    yml = Path(path) if path else CONFIG_PATH
    if yml.exists():
        _merge(raw, yaml.safe_load(yml.read_text(encoding="utf-8")) or {})
    for name, keys in ENV_KEYS.items():
        if os.environ.get(name):
            _set(raw, keys, _env_value(name, os.environ[name]))
    cli = {k: v for k, v in (cli or {}).items() if v is not None}
    for name, keys in CLI_KEYS.items():
        if name in cli:
            _set(raw, keys, cli[name])
    return Config(
        dataset=_path(raw["dataset"]),
        output=_path(raw["output"]),
        runs_dir=_path(raw["runs_dir"]),
        cache_dir=_path(raw["cache_dir"]),
        trace=bool(raw["trace"]),
        tiebreak=str(raw["linker"]["tiebreak"]).upper(),
        llm=raw["llm"],
        linker=raw["linker"],
        no_llm=bool(cli.get("no_llm", False)),
        mode=resolve_mode(str(raw["mode"]).lower()),
        agent=raw["agent"],
        raw=raw,
    )


def resolve_mode(mode: str) -> str:
    """auto -> agent when a tool-capable key (GROQ_API_KEY / GEMINI_API_KEY) is set."""
    if mode in ("agent", "deterministic"):
        return mode
    if mode != "auto":
        raise ValueError(f"unknown mode {mode!r}; expected agent, deterministic or auto")
    from ghost import llm

    llm._load_env()
    return "agent" if any(os.environ.get(k) for k in ("GROQ_API_KEY", "GEMINI_API_KEY")) \
        else "deterministic"


def apply(config: Config) -> None:
    """Point the domain layer at this config. With the default values this is a no-op for
    every decision, which is what the golden lock checks."""
    from ghost import linker

    if config.dataset != paths.DATASET:
        paths.use_dataset(config.dataset)
    for env, key in (("GEMINI_MODEL", "gemini_model"), ("GEMINI_TEXT_MODEL", "gemini_text_model"),
                     ("GROQ_MODEL", "groq_model")):
        if config.llm.get(key) and not os.environ.get(env):
            os.environ[env] = str(config.llm[key])
    from ghost import llm

    for provider, lim in (config.agent.get("rate_limits") or {}).items():
        llm.RATE_LIMITS[provider] = (int(lim["rpm"]), int(lim["tpm"]))
    linker.LEARN_RANGE_HOURS = float(config.linker["learn_range_hours"])
    linker.WINDOW_PAD_HOURS = float(config.linker["window_pad_hours"])
    linker.DEFAULT_WINDOW = tuple(config.linker["default_window"])
