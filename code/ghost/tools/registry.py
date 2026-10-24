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

_STR = {"type": "string"}
_TIME = {"type": ["string", "null"], "description": "ISO-8601 cutoff; null = no cutoff"}


def _obj(required: dict, optional: dict | None = None) -> dict:
    return {
        "type": "object",
        "properties": {**required, **(optional or {})},
        "required": list(required),
        "additionalProperties": False,
    }


def _out(type_: str, description: str) -> dict:
    return {"type": type_, "description": description}


@dataclass
class ToolEnv:
    """What the tools can see: the run context (set by the orchestrator) and the per-case
    contexts built so far. Lazily built indexes are cached here, never in the domain layer."""

    run: object = None  # RunContext
    cases: dict = field(default_factory=dict)  # case_id -> CaseContext

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


def _until(value):
    from ghost.loaders import parse_ts

    return parse_ts(value) if value else None


def build_registry(env: ToolEnv | None = None, tracer: Tracer | None = None) -> ToolRegistry:
    env = env or ToolEnv()
    reg = ToolRegistry(tracer)

    def load_evidence_corpus():
        from ghost import loaders
        from ghost.context import Corpus
        from ghost.parsers.registry import parse_all, parse_results

        observations, coverages, facts = parse_all()
        results = parse_results().values()
        families = Counter(r.family.split(":")[0] for r in results)
        return Corpus(
            identities=loaders.load_identities(),
            audit_changes=loaders.load_audit_changes(),
            activities=loaders.load_activities(),
            rules=loaders.load_rules(),
            evidence_files=loaders.load_evidence_files(),
            observations=observations,
            coverages=coverages,
            facts=facts,
            stats={
                "files": len(results),
                "families": dict(sorted(families.items())),
                "observations": len(observations),
                "coverages": len(coverages),
                "file_facts": len(facts),
                "resources": len({o.resource_id for o in observations}),
                "unparsed_units": sum(r.unparsed for r in results),
                "skipped_files": sorted(r.file_id for r in results
                                        if r.family.endswith(("skipped", "unknown"))),
            },
        )

    reg.register(Tool(
        "load_evidence_corpus",
        "Load the dataset CSVs and parse every evidence file (SQL, JSON, logs, text, PDF, "
        "cached screenshot extraction) into observations, coverages and file facts.",
        _obj({}), _out("object", "Corpus (inputs + evidence index + stats)"),
        load_evidence_corpus,
    ))

    def extract_screenshot(file_id: str, use_llm: bool = False):
        from ghost import paths
        from ghost.parsers.images import load_extraction

        ef = next(e for e in env.run.corpus.evidence_files if e.file_id == file_id)
        result = load_extraction(paths.DATASET / ef.file_path, use_llm)
        return result.data if result else None

    reg.register(Tool(
        "extract_screenshot",
        "Vision transcription of one PNG screenshot (cached; use_llm=false never calls the API).",
        _obj({"file_id": _STR}, {"use_llm": {"type": "boolean"}}),
        _out("object", "transcribed screen JSON, or null when no cached extraction exists"),
        extract_screenshot,
    ))

    def classify_request(texts: list, use_llm: bool):
        from ghost import classifier

        return classifier.classify_texts(list(texts), use_llm)

    reg.register(Tool(
        "classify_request",
        "Label request texts DELETION / NOT_DELETION (multilingual rules + batched, cached LLM).",
        _obj({"texts": {"type": "array", "items": _STR}, "use_llm": {"type": "boolean"}}),
        _out("object", "text -> decision (rule_label, llm_label, final, reason, source)"),
        classify_request,
    ))

    def assign_worker_windows(non_deletion: list, tiebreak: str):
        from ghost import linker

        return linker.build_linker(frozenset(non_deletion), tiebreak)

    reg.register(Tool(
        "assign_worker_windows",
        "Global pass over all requests: learn worker windows and assign identifier-less "
        "deletion-worker resources to cases (key, catalog and propagation links included).",
        _obj({"non_deletion": {"type": "array", "items": _STR}, "tiebreak": _STR}),
        _out("object", "global assignment table (Linker)"),
        assign_worker_windows,
    ))

    def resolve_identity(email: str):
        return env.resolver.resolve(email)

    reg.register(Tool(
        "resolve_identity",
        "Resolve a request email (current or historical) to the subject's current and "
        "historical identifiers (user ids, emails, phones, hashes, names).",
        _obj({"email": _STR}), _out("object", "SubjectIdentity, or null if unknown"),
        resolve_identity,
    ))

    def search_evidence(keys: list):
        seen, out = set(), []
        for k in keys:
            for o in env.obs_by_key.get(k, ()):
                if id(o) not in seen:
                    seen.add(id(o))
                    out.append(o)
        return out

    reg.register(Tool(
        "search_evidence",
        "Find every observation that carries any of the given normalized identifiers.",
        _obj({"keys": {"type": "array", "items": _STR}}),
        _out("array", "observations"),
        search_evidence,
    ))

    def get_observations(resource_id: str, until: str | None = None):
        cutoff = _until(until)
        obs = env.linker.resource_observations(resource_id)
        return [o for o in obs if cutoff is None or o.captured_at <= cutoff]

    reg.register(Tool(
        "get_observations",
        "All observations of one resource (catalog alias sightings included), optionally "
        "only those captured at or before a cutoff.",
        _obj({"resource_id": _STR}, {"until": _TIME}),
        _out("array", "observations, oldest first"),
        get_observations,
    ))

    def get_coverage(system_id: str, until: str | None = None):
        cutoff = _until(until)
        return [c for c in env.run.corpus.coverages
                if c.system_id == system_id and (cutoff is None or c.captured_at <= cutoff)]

    reg.register(Tool(
        "get_coverage",
        "Which evidence files inspected a system (complete=true means a missing record is "
        "absent at that time), optionally up to a cutoff.",
        _obj({"system_id": _STR}, {"until": _TIME}),
        _out("array", "coverage entries"),
        get_coverage,
    ))

    def get_activities(resource_id: str):
        return list(env.linker.acts_by_resource.get(resource_id, ()))

    reg.register(Tool(
        "get_activities",
        "Every system_activity event on one resource, in time order.",
        _obj({"resource_id": _STR}), _out("array", "activities"),
        get_activities,
    ))

    def link_resources(case_id: str):
        return env.linker.link(case_id)

    reg.register(Tool(
        "link_resources",
        "The resources that belong to a case's subject (key, catalog, worker-window and "
        "propagation channels, then the stale filter).",
        _obj({"case_id": _STR}), _out("array", "linked resources, sorted by id"),
        link_resources,
    ))

    def assess_resource(case_id: str, resource_id: str):
        from ghost import planner

        case = env.cases[case_id]
        res = next(r for r in case.linked if r.resource_id == resource_id)
        return planner.decide(res, env.request(case_id), env.linker)

    reg.register(Tool(
        "assess_resource",
        "Required treatment (policy) and state at audit_at (evidence) for one linked resource.",
        _obj({"case_id": _STR, "resource_id": _STR}),
        _out("object", "Decision (treatment, state, violation)"),
        assess_resource,
    ))

    def plan_steps(case_id: str):
        from ghost import planner

        case = env.cases[case_id]
        return planner.plan_case(env.request(case_id), case.linked, env.linker,
                                 decisions=case.assessments)

    reg.register(Tool(
        "plan_steps",
        "Findings, ordered remediation and verification steps plus status for one case, from "
        "its assessments.",
        _obj({"case_id": _STR}), _out("object", "PlanResult (status, steps, flags)"),
        plan_steps,
    ))

    def validate_row(case_id: str, status: str, steps: list):
        from ghost import serialize

        return serialize.validate_row(status, list(steps), env.request(case_id).audit_at_raw,
                                      env.citable)

    reg.register(Tool(
        "validate_row",
        "Check one output row against the output contract (schema, allowed operations, phase "
        "order, evidence rules, evidence captured <= audit_at).",
        _obj({"case_id": _STR, "status": _STR,
              "steps": {"type": "array", "items": {"type": "object"}}}),
        _out("array", "contract violations (empty = valid)"),
        validate_row,
    ))
    return reg
