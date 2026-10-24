from ghost.agents.base import BaseAgent
from ghost.context import CaseContext


class AssessorAgent(BaseAgent):
    name = "AssessorAgent"
    role = "Determines each resource's required treatment and its true state at audit_at."
    tools_used = ("assess_resource",)

    def run(self, case: CaseContext) -> CaseContext:
        if not case.authorized:
            return case
        tools = self.tools(case.case_id)
        case.assessments = [
            tools.call("assess_resource", case_id=case.case_id, resource_id=r.resource_id)
            for r in case.linked
        ]
        # confidence: share of resources decided by evidence <= audit_at AND linked without
        # a guessed (tie-broken) assignment. Reported only; it never changes a decision.
        guessed = {r.resource_id for r in case.linked
                   if any(v[0] == "worker_window" and v[2] for v in r.via)}
        solid = [d for d in case.assessments
                 if not d.state.unknown and d.resource_id not in guessed]
        case.confidence = round(len(solid) / len(case.assessments), 3) if case.assessments else None
        self.note(case.case_id, f"{sum(d.violation for d in case.assessments)} violations",
                  confidence=case.confidence,
                  assumed_compliant=sum(d.state.unknown for d in case.assessments))
        return case
