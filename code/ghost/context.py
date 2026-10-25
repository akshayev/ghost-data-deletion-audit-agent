"""Shared state for one run (RunContext) and for one request (CaseContext).

The RunContext is filled by the global agents (evidence corpus, intake labels, the global
worker-window assignment) and then frozen; per-case agents only read it. Each CaseContext
collects one request's subject, linked resources, assessments, steps, status and errors.
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from ghost.config import Config
from ghost.loaders import DeletionRequest
from ghost.trace import Tracer


@dataclass
class Corpus:
    """Everything loaded from the dataset: the input files plus the parsed evidence index."""

    identities: tuple = ()
    audit_changes: tuple = ()
    activities: tuple = ()
    rules: tuple = ()
    evidence_files: tuple = ()
    observations: tuple = ()
    coverages: tuple = ()
    facts: tuple = ()
    stats: dict = field(default_factory=dict)


class FrozenError(AttributeError):
    pass


@dataclass
class RunContext:
    config: Config
    split: str
    requests: dict[str, DeletionRequest]  # this split's requests, in file order
    all_requests: dict[str, DeletionRequest]  # both splits (global assignment needs them)
    tracer: Tracer
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)
    corpus: Corpus | None = None
    intake: dict = field(default_factory=dict)  # request_text -> classifier decision
    non_deletion: frozenset[str] = frozenset()
    assignment: object = None  # the global linker (worker-window assignment table)
    _frozen: bool = False

    def freeze(self) -> "RunContext":
        object.__setattr__(self, "_frozen", True)
        return self

    def __setattr__(self, name, value):
        if getattr(self, "_frozen", False):
            raise FrozenError(f"RunContext is frozen; cannot set {name}")
        object.__setattr__(self, name, value)

    @property
    def dataset_root(self):
        return self.config.dataset


@dataclass
class CaseContext:
    request: DeletionRequest
    run: RunContext
    authorized: bool = True  # intake: does request_text authorize deletion?
    subject: object = None  # SubjectIdentity | None
    linked: list = field(default_factory=list)  # LinkedResource
    assessments: list = field(default_factory=list)  # Decision per linked resource
    steps: list[dict] = field(default_factory=list)
    status: str | None = None
    confidence: float | None = None
    plan_flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started: datetime | None = None
    route: str | None = None  # FAST | AGENT (RouterAgent)
    route_reasons: tuple[str, ...] = ()
    agent: dict | None = None  # CaseInvestigatorAgent outcome (agent mode, routed cases)

    @property
    def case_id(self) -> str:
        return self.request.case_id

    @property
    def trace(self):
        return self.run.tracer.for_case(self.case_id)
