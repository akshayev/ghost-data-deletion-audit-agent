from collections import Counter

from ghost.agents.base import BaseAgent
from ghost.context import CaseContext

# ambiguity -> why the case needs judgement
_REASON = {"tie_break": "tie_break", "identity_conflict": "identity_conflict"}


def route_case(authorized: bool, ambiguities: list[dict]) -> tuple[str, tuple[str, ...]]:
    """AGENT when any linking decision is open (a tie-broken link, an identity conflict, or a
    candidate lost in a tie / left unassigned by SKIP_MULTI); otherwise the FAST path.
    NOT_DELETION cases never reach the agent."""
    if not authorized:
        return "FAST", ("not_deletion",)
    reasons = set()
    for a in ambiguities:
        if a["kind"] == "tie_candidate":
            reasons.add("skip_multi" if a["source"] == "skip_multi" else "tie_lost")
        else:
            reasons.add(_REASON[a["kind"]])
    return ("AGENT", tuple(sorted(reasons))) if reasons else ("FAST", ())


class RouterAgent(BaseAgent):
    name = "RouterAgent"
    role = "Sends cases with open linking decisions to the case investigator, the rest fast."
    tools_used = ("get_ambiguities",)

    def __init__(self, registry) -> None:
        super().__init__(registry)
        self.stats: Counter = Counter()

    def run(self, case: CaseContext) -> CaseContext:
        points = (self.tools(case.case_id).call("get_ambiguities", case_id=case.case_id)
                  if case.authorized else [])
        case.route, case.route_reasons = route_case(case.authorized, points)
        self.stats[case.route] += 1
        for r in case.route_reasons:
            self.stats[f"{case.route}:{r}"] += 1
        self.note(case.case_id, f"route {case.route}", reasons=list(case.route_reasons),
                  ambiguities=len(points))
        return case
