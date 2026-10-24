from ghost.agents.base import BaseAgent
from ghost.context import CaseContext


class InvalidRow(ValueError):
    pass


class VerifierAgent(BaseAgent):
    """Placeholder until P10: checks the row against the output contract only."""

    name = "VerifierAgent"
    role = "Rejects any row that breaks the output contract before it can be written."
    tools_used = ("validate_row",)

    def run(self, case: CaseContext) -> CaseContext:
        errors = self.tools(case.case_id).call("validate_row", case_id=case.case_id,
                                               status=case.status, steps=case.steps)
        if errors:
            raise InvalidRow(f"{case.case_id}: " + "; ".join(errors))
        self.note(case.case_id, "row valid")
        return case
