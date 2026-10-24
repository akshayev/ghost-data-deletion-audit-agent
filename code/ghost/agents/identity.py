from ghost.agents.base import BaseAgent
from ghost.context import CaseContext


class IdentityAgent(BaseAgent):
    name = "IdentityAgent"
    role = "Resolves the request email to the subject's current and historical identifiers."
    tools_used = ("resolve_identity",)

    def run(self, case: CaseContext) -> CaseContext:
        case.subject = self.tools(case.case_id).call("resolve_identity", email=case.request.email)
        s = case.subject
        self.note(case.case_id,
                  f"subject {', '.join(sorted(s.user_ids))} via {s.matched_via} email"
                  if s else "no matching identity",
                  strong_keys=len(s.strong_keys) if s else 0)
        return case
