from ghost.agents.base import BaseAgent
from ghost.context import RunContext

NOT_DELETION = "NOT_DELETION"


class IntakeAgent(BaseAgent):
    name = "IntakeAgent"
    role = "Decides for every request whether its text actually authorizes a deletion."
    tools_used = ("classify_request",)

    def run(self, ctx: RunContext) -> RunContext:
        # every request of both splits, in file order (also the classifier's cache order)
        texts = [r.request_text for r in ctx.all_requests.values()]
        decisions = self.tools().call("classify_request", texts=texts,
                                      use_llm=not ctx.config.no_llm)
        ctx.intake = decisions
        ctx.non_deletion = frozenset(
            case_id for case_id, r in ctx.all_requests.items()
            if decisions[r.request_text].final == NOT_DELETION
        )
        sources = sorted({d.source for d in decisions.values()})
        self.note(None, f"{len(ctx.non_deletion)} non-deletion requests",
                  unique_texts=len(decisions), sources=sources,
                  non_deletion=sorted(ctx.non_deletion))
        return ctx
