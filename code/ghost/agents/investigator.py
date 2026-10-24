from ghost.agents.base import BaseAgent
from ghost.context import CaseContext, RunContext


class InvestigatorAgent(BaseAgent):
    name = "InvestigatorAgent"
    role = "Finds which resources belong to each subject, assigning shared worker events globally."
    tools_used = ("assign_worker_windows", "link_resources")

    def global_assign(self, ctx: RunContext) -> RunContext:
        table = self.tools().call("assign_worker_windows", non_deletion=sorted(ctx.non_deletion),
                                  tiebreak=ctx.config.tiebreak)
        ctx.assignment = table
        self.note(None, f"global assignment ({ctx.config.tiebreak})",
                  tie_breaks=table.tie_breaks, propagation=dict(table.propagation_stats))
        return ctx

    def run(self, case: CaseContext) -> CaseContext:
        if not case.authorized:
            return case
        case.linked = self.tools(case.case_id).call("link_resources", case_id=case.case_id)
        channels: dict[str, int] = {}
        for r in case.linked:
            for v in {v[0] for v in r.via}:
                channels[v] = channels.get(v, 0) + 1
        self.note(case.case_id, f"{len(case.linked)} linked resources", channels=channels)
        return case
