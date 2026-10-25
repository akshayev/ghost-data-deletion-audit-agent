"""Domain tools: thin wrappers over the domain layer, one registration function per tool.

Moved verbatim from registry.build_registry (P13); names, descriptions and schemas are
unchanged. Nothing here makes a decision; each tool calls one domain function."""

from collections import Counter

from ghost.tools._format import _STR, _TIME, _obj, _out
from ghost.tools.registry import Tool, ToolEnv, ToolRegistry


def _until(value):
    from ghost.loaders import parse_ts

    return parse_ts(value) if value else None


def register_load_evidence_corpus(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_extract_screenshot(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_classify_request(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_assign_worker_windows(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_resolve_identity(reg: ToolRegistry, env: ToolEnv) -> None:
    def resolve_identity(email: str):
        return env.resolver.resolve(email)

    reg.register(Tool(
        "resolve_identity",
        "Resolve a request email (current or historical) to the subject's current and "
        "historical identifiers (user ids, emails, phones, hashes, names).",
        _obj({"email": _STR}), _out("object", "SubjectIdentity, or null if unknown"),
        resolve_identity,
    ))


def register_search_evidence(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_get_observations(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_get_coverage(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_get_activities(reg: ToolRegistry, env: ToolEnv) -> None:
    def get_activities(resource_id: str):
        return list(env.linker.acts_by_resource.get(resource_id, ()))

    reg.register(Tool(
        "get_activities",
        "Every system_activity event on one resource, in time order.",
        _obj({"resource_id": _STR}), _out("array", "activities"),
        get_activities,
    ))


def register_link_resources(reg: ToolRegistry, env: ToolEnv) -> None:
    def link_resources(case_id: str):
        return env.linker.link(case_id)

    reg.register(Tool(
        "link_resources",
        "The resources that belong to a case's subject (key, catalog, worker-window and "
        "propagation channels, then the stale filter).",
        _obj({"case_id": _STR}), _out("array", "linked resources, sorted by id"),
        link_resources,
    ))


def register_assess_resource(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_plan_steps(reg: ToolRegistry, env: ToolEnv) -> None:
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


def register_validate_row(reg: ToolRegistry, env: ToolEnv) -> None:
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


REGISTER = (
    register_load_evidence_corpus,
    register_extract_screenshot,
    register_classify_request,
    register_assign_worker_windows,
    register_resolve_identity,
    register_search_evidence,
    register_get_observations,
    register_get_coverage,
    register_get_activities,
    register_link_resources,
    register_assess_resource,
    register_plan_steps,
    register_validate_row,
)
