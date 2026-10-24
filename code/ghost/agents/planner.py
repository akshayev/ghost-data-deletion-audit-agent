from ghost.agents.base import BaseAgent
from ghost.context import CaseContext


class PlannerAgent(BaseAgent):
    name = "PlannerAgent"
    role = "Turns the assessments into ordered findings, remediation and verification steps."
    tools_used = ("plan_steps",)

    def run(self, case: CaseContext) -> CaseContext:
        if not case.authorized:
            case.status, case.steps = "NOT_APPLICABLE", []
            self.note(case.case_id, "request does not authorize deletion: NOT_APPLICABLE")
            return case
        plan = self.tools(case.case_id).call("plan_steps", case_id=case.case_id)
        case.status, case.steps, case.plan_flags = plan.status, plan.steps, list(plan.flags)
        self.note(case.case_id, f"{plan.status} with {len(plan.steps)} steps",
                  flags=case.plan_flags)
        return case
