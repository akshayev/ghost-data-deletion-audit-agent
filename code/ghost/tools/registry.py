"""Tool registry: the only way agents reach the domain layer.

Each Tool is a thin wrapper over an existing domain function (no new decision logic) with a
JSON-Schema description of its inputs and outputs. ToolRegistry.call validates the inputs,
times the call, counts it and emits a trace event. Agents get a *bound* caller that refuses
any tool the agent did not declare in tools_used.

    python code/main.py tools     # prints this registry (also written to code/docs/TOOLS.md)
"""

import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property

from ghost.trace import Tracer


class ToolInputError(ValueError):
    """A tool was called with inputs that do not match its input_schema."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    fn: Callable


_JSON_TYPES: dict[str, Callable[[object], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list | tuple),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def validate(schema: dict, value, where: str = "input") -> None:
    """A small JSON-Schema subset: type (or list of types), properties, required,
    additionalProperties=false, items."""
    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else list(types)
        if not any(_JSON_TYPES[t](value) for t in allowed):
            got = type(value).__name__
            raise ToolInputError(f"{where}: expected {'|'.join(allowed)}, got {got}")
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        for key in schema.get("required", ()):
            if key not in value:
                raise ToolInputError(f"{where}: missing required {key!r}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(props))
            if extra:
                raise ToolInputError(f"{where}: unexpected {extra}")
        for key, sub in props.items():
            if key in value:
                validate(sub, value[key], f"{where}.{key}")
    if isinstance(value, list | tuple) and "items" in schema:
        for i, item in enumerate(value):
            validate(schema["items"], item, f"{where}[{i}]")


def _summary(result) -> str:
    if result is None:
        return "None"
    if isinstance(result, list | tuple | dict | set | frozenset):
        return f"{type(result).__name__}[{len(result)}]"
    return type(result).__name__


class ToolRegistry:
    def __init__(self, tracer: Tracer | None = None) -> None:
        self.tools: dict[str, Tool] = {}
        self.tracer = tracer or Tracer(enabled=False)
        self.counts: Counter = Counter()
        self.seconds: Counter = Counter()

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"tool {tool.name!r} registered twice")
        self.tools[tool.name] = tool
        return tool

    def list(self) -> list[Tool]:
        return [self.tools[n] for n in sorted(self.tools)]

    def call(self, name: str, *, _agent: str = "", _case: str | None = None, **kwargs):
        if name not in self.tools:
            raise KeyError(f"unknown tool {name!r}")
        tool = self.tools[name]
        validate(tool.input_schema, kwargs)
        started = time.perf_counter()
        result = tool.fn(**kwargs)
        elapsed = time.perf_counter() - started
        self.counts[name] += 1
        self.seconds[name] += elapsed
        args = {k: v if isinstance(v, str | int | float | bool) or v is None else _summary(v)
                for k, v in kwargs.items()}
        self.tracer.emit(_case, _agent, name, f"{name} -> {_summary(result)}",
                         {"args": args, "ms": round(elapsed * 1000, 2)})
        return result

    def bind(self, agent: str, allowed: Iterable[str], case_id: str | None = None) -> "BoundTools":
        return BoundTools(self, agent, frozenset(allowed), case_id)

    def describe(self) -> str:
        lines = ["| tool | purpose | inputs | output |", "|---|---|---|---|"]
        for t in self.list():
            props = t.input_schema.get("properties", {})
            required = set(t.input_schema.get("required", ()))
            inputs = ", ".join(
                f"{k}{'' if k in required else '?'}: {_type_name(v)}" for k, v in props.items()
            ) or "(none)"
            lines.append(f"| `{t.name}` | {t.description} | {inputs} | "
                         f"{t.output_schema.get('description', _type_name(t.output_schema))} |")
        return "\n".join(lines) + "\n"


def _type_name(schema: dict) -> str:
    t = schema.get("type", "any")
    t = "|".join(t) if isinstance(t, list) else t
    if t == "array" and "items" in schema:
        return f"array<{_type_name(schema['items'])}>"
    return t


@dataclass
class BoundTools:
    registry: ToolRegistry
    agent: str
    allowed: frozenset[str]
    case_id: str | None = None

    def call(self, name: str, **kwargs):
        if name not in self.allowed:
            raise PermissionError(f"{self.agent} did not declare tool {name!r}")
        return self.registry.call(name, _agent=self.agent, _case=self.case_id, **kwargs)


# ---------------------------------------------------------------------------
# Tool implementations (thin wrappers over the domain layer)
# ---------------------------------------------------------------------------


@dataclass
class ToolEnv:
    """What the tools can see: the run context (set by the orchestrator) and the per-case
    contexts built so far. Lazily built indexes are cached here, never in the domain layer."""

    run: object = None  # RunContext
    cases: dict = field(default_factory=dict)  # case_id -> CaseContext
    plans: dict = field(default_factory=dict)  # plan_id -> proposed plan (case agent)
    submissions: dict = field(default_factory=dict)  # case_id -> {plan_id, rationale}
    escalations: dict = field(default_factory=dict)  # case_id -> reason
    _ambiguities: dict = field(default_factory=dict)

    def ambiguities(self, case_id: str) -> list:
        if case_id not in self._ambiguities:
            from ghost.ambiguity import case_ambiguities

            self._ambiguities[case_id] = case_ambiguities(self.linker, case_id,
                                                          self.cases[case_id].linked)
        return self._ambiguities[case_id]

    @property
    def linker(self):
        if self.run is None or self.run.assignment is None:
            raise RuntimeError("the global assignment has not run yet")
        return self.run.assignment

    def request(self, case_id: str):
        return self.run.all_requests[case_id]

    @cached_property
    def resolver(self):
        from ghost.identity import IdentityResolver

        corpus = self.run.corpus
        return IdentityResolver(corpus.identities, corpus.audit_changes)

    @cached_property
    def obs_by_key(self) -> dict:
        index: dict = {}
        for o in self.run.corpus.observations:
            for k in o.link_keys:
                index.setdefault(k, []).append(o)
        return index

    @cached_property
    def citable(self) -> dict:
        from ghost.serialize import load_citable_ids

        return load_citable_ids()


def build_registry(env: ToolEnv | None = None, tracer: Tracer | None = None) -> ToolRegistry:
    """Every tool, registered from its module: domain_tools (thin wrappers over the domain
    layer) and agent_tools (the case agent's view)."""
    from ghost.tools import agent_tools, domain_tools

    env = env or ToolEnv()
    reg = ToolRegistry(tracer)
    for register in (*domain_tools.REGISTER, *agent_tools.REGISTER):
        register(reg, env)
    return reg
