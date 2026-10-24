from ghost.agents.base import BaseAgent
from ghost.context import RunContext


class EvidenceAgent(BaseAgent):
    name = "EvidenceAgent"
    role = "Builds the evidence corpus from every file in the manifest and reports coverage."
    tools_used = ("load_evidence_corpus", "extract_screenshot")

    def run(self, ctx: RunContext) -> RunContext:
        tools = self.tools()
        corpus = tools.call("load_evidence_corpus")
        ctx.corpus = corpus  # the screenshot tool reads the manifest from the run context
        screenshots = [ef.file_id for ef in corpus.evidence_files
                       if ef.file_path.lower().endswith(".png")]
        cached = [f for f in screenshots
                  if tools.call("extract_screenshot", file_id=f, use_llm=False) is not None]
        corpus.stats["screenshots_cached"] = f"{len(cached)}/{len(screenshots)}"
        self.note(None, "evidence corpus ready", **corpus.stats)
        return ctx
