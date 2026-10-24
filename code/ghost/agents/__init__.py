"""The audit agents, in pipeline order (see ghost.orchestrator)."""

from ghost.agents.assessor import AssessorAgent
from ghost.agents.evidence import EvidenceAgent
from ghost.agents.identity import IdentityAgent
from ghost.agents.intake import IntakeAgent
from ghost.agents.investigator import InvestigatorAgent
from ghost.agents.planner import PlannerAgent
from ghost.agents.verifier import VerifierAgent

ALL_AGENTS = (
    EvidenceAgent,
    IntakeAgent,
    InvestigatorAgent,
    IdentityAgent,
    AssessorAgent,
    PlannerAgent,
    VerifierAgent,
)

__all__ = [a.__name__ for a in ALL_AGENTS] + ["ALL_AGENTS"]
