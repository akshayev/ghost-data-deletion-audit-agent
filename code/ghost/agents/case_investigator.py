"""CaseInvestigatorAgent: an LLM tool-calling loop over one routed case.

The model sees only the agent tools (brief, candidates, inspect, ambiguities, propose, submit,
escalate). It can change nothing but the case's ambiguity points; propose_plan runs the
deterministic planner and verifier on its decisions, and only a verifier-passed plan can be
submitted. Limits: max_tool_calls tool calls and max_rejections rejected proposals, then the
deterministic plan is kept (FALLBACK_<reason>). Outcomes are checkpointed per case, so a rerun
replays finished cases through the same gate without calling the model.
"""

import hashlib
import json
from pathlib import Path

from ghost import llm, paths
from ghost.agents.base import BaseAgent
from ghost.context import CaseContext
from ghost.tools.registry import ToolInputError

SYSTEM_PROMPT = paths.CODE / "prompts" / "case_agent_system.md"
AGENT_TOOLS = ("get_case_brief", "get_ambiguities", "list_candidate_resources",
               "inspect_resource", "propose_plan", "submit_case", "escalate_case")
NUDGE = ("Reply only with tool calls. Continue the workflow and finish with submit_case "
         "(or escalate_case).")
MAX_NUDGES = 2
FINAL = ("AGENT_SUBMITTED", "AGENT_ESCALATED", "FALLBACK_TOOL_CAP", "FALLBACK_REJECTED",
         "FALLBACK_NO_TOOL_CALL")  # outcomes worth checkpointing (quota/offline ones are not)


def tool_schemas(registry) -> list[dict]:
    """OpenAI function-calling schemas for the agent tools, from the registry."""
    out = []
    for name in AGENT_TOOLS:
        t = registry.tools[name]
        params = json.loads(json.dumps(t.input_schema))
        if name == "propose_plan":
            params["properties"]["decisions"]["items"] = {
                "type": "object",
                "properties": {"ambiguity_id": {"type": "string"},
                               "choice": {"type": "string", "enum": ["INCLUDE", "EXCLUDE"]}},
                "required": ["ambiguity_id", "choice"],
            }
        out.append({"type": "function",
                    "function": {"name": name, "description": t.description,
                                 "parameters": params}})
    return out


class CaseInvestigatorAgent(BaseAgent):
    name = "CaseInvestigatorAgent"
    role = "Resolves a routed case's open linking decisions with an LLM tool-calling loop."
    tools_used = AGENT_TOOLS

    def __init__(self, registry, chat=None, *, max_tool_calls: int = 8,
                 max_rejections: int = 2, offline: bool = False,
                 checkpoint_dir: Path | str | None = None, salt: str = "") -> None:
        super().__init__(registry)
        self.chat = chat or llm.tool_chat
        self.max_tool_calls, self.max_rejections = max_tool_calls, max_rejections
        self.offline = offline
        self.system = SYSTEM_PROMPT.read_text(encoding="utf-8")
        self.schemas = tool_schemas(registry)
        self.config_hash = hashlib.sha256(json.dumps(
            [self.system, self.schemas, max_tool_calls, max_rejections, salt],
            sort_keys=True).encode()).hexdigest()[:12]
        base = Path(checkpoint_dir) if checkpoint_dir else paths.CODE / "cache" / "agent_runs"
        self.checkpoint_dir = base / self.config_hash

    # ----- checkpoints -----

    def _checkpoint(self, case_id: str) -> Path:
        return self.checkpoint_dir / f"{case_id}.json"

    def _save(self, case_id: str, out: dict) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoint(case_id).write_text(json.dumps(out, indent=1, ensure_ascii=False)
                                             + "\n", encoding="utf-8")

    def _replay(self, case: CaseContext, saved: dict) -> dict:
        """Re-run the saved decisions through the same gate (no model call)."""
        out = {**saved, "replayed": True}
        if saved["outcome"] != "AGENT_SUBMITTED":
            return out
        tools = self.tools(case.case_id)
        plan = tools.call("propose_plan", case_id=case.case_id, decisions=saved["decisions"])
        ok = plan.get("verifier", {}).get("ok")
        done = ok and tools.call("submit_case", case_id=case.case_id, plan_id=plan["plan_id"],
                                 rationale=saved["rationale"]).get("accepted")
        if not done:  # the gate changed since the checkpoint: never trust a stale answer
            return {**out, "outcome": "FALLBACK_STALE_CHECKPOINT"}
        return {**out, "plan_id": plan["plan_id"], "changes": plan["changes_vs_default"]}

    # ----- one tool call -----

    def _execute(self, case_id: str, call: llm.ToolCall) -> dict | list:
        if call.error:
            return {"error": f"{call.error}; resend the call with a valid JSON object"}
        if call.name not in self.tools_used:
            return {"error": f"unknown tool {call.name!r}; available: {list(self.tools_used)}"}
        args = dict(call.arguments or {})
        if args.setdefault("case_id", case_id) != case_id:
            return {"error": f"you may only work on {case_id}"}
        try:
            return self.tools(case_id).call(call.name, **args)
        except ToolInputError as exc:
            return {"error": f"bad arguments for {call.name}: {exc}"}
        except Exception as exc:  # a tool failure is information for the model, not a crash
            return {"error": f"{call.name} failed: {type(exc).__name__}: {exc}"}

    # ----- the loop -----

    def run(self, case: CaseContext) -> CaseContext:
        cid = case.case_id
        saved = self._checkpoint(cid)
        if saved.exists():
            case.agent = self._replay(case, json.loads(saved.read_text(encoding="utf-8")))
            self.note(cid, f"checkpoint replay: {case.agent['outcome']}",
                      config_hash=self.config_hash)
            return case
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": f"Audit {cid}. Begin with get_case_brief and "
                                                f"get_ambiguities."}]
        out = {"case_id": cid, "outcome": None, "rationale": "", "provider": "", "model": "",
               "models": [], "tool_calls": 0, "turns": 0, "rejections": 0,
               "prompt_tokens": 0, "completion_tokens": 0, "decisions": [], "changes": [],
               "plan_id": None, "config_hash": self.config_hash, "replayed": False}
        proposals: dict[str, list] = {}
        nudges = 0
        while out["outcome"] is None:
            out["turns"] += 1
            try:
                reply = self.chat(messages, self.schemas, case_id=cid, turn=out["turns"],
                                  offline=self.offline)
            except llm.QuotaExhausted:
                out["outcome"] = "FALLBACK_QUOTA"
                break
            except llm.CacheMiss:  # offline replay without a checkpoint (run was time-boxed)
                out["outcome"], out["error"] = "FALLBACK_QUOTA", "no checkpoint (offline replay)"
                break
            except llm.LLMError as exc:
                out["outcome"], out["error"] = "FALLBACK_LLM_ERROR", str(exc)[:300]
                break
            out["provider"], out["model"] = reply.provider, reply.model
            if reply.model not in out["models"]:
                out["models"].append(reply.model)
            out["prompt_tokens"] += reply.prompt_tokens or 0
            out["completion_tokens"] += reply.completion_tokens or 0
            messages.append(reply.assistant_message())
            self.note(cid, f"turn {out['turns']}: {len(reply.tool_calls)} tool call(s)",
                      model=reply.model, cache_hit=reply.cache_hit, content=reply.content,
                      calls=[{"name": c.name, "arguments": c.arguments, "error": c.error}
                             for c in reply.tool_calls])
            if not reply.tool_calls:
                nudges += 1
                if nudges > MAX_NUDGES:
                    out["outcome"] = "FALLBACK_NO_TOOL_CALL"
                    break
                messages.append({"role": "user", "content": NUDGE})
                continue
            for call in reply.tool_calls:
                out["tool_calls"] += 1
                if out["tool_calls"] > self.max_tool_calls:
                    out["outcome"] = "FALLBACK_TOOL_CAP"
                    break
                result = self._execute(cid, call)
                content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                self.note(cid, f"tool {call.name} -> {content[:120]}", result=result)
                if call.name == "propose_plan" and isinstance(result, dict):
                    if result.get("verifier", {}).get("ok"):
                        proposals[result["plan_id"]] = (call.arguments or {}).get("decisions",
                                                                                   [])
                    else:
                        out["rejections"] += 1
                        if out["rejections"] > self.max_rejections:
                            out["outcome"] = "FALLBACK_REJECTED"
                            break
                if call.name == "submit_case" and isinstance(result, dict) and \
                        result.get("accepted"):
                    plan_id = result["plan_id"]
                    out.update(outcome="AGENT_SUBMITTED", plan_id=plan_id,
                               rationale=(call.arguments or {}).get("rationale", "").strip(),
                               decisions=proposals.get(plan_id, []),
                               changes=self.registry_plan_changes(cid, plan_id, messages))
                    break
                if call.name == "escalate_case" and isinstance(result, dict) and \
                        result.get("accepted"):
                    out.update(outcome="AGENT_ESCALATED",
                               rationale=(call.arguments or {}).get("reason", "").strip())
                    break
        out["transcript"] = messages[1:]  # the system prompt is in the prompts/ file
        case.agent = out
        self.note(cid, f"outcome {out['outcome']}", tool_calls=out["tool_calls"],
                  rationale=out["rationale"], changes=out["changes"])
        if out["outcome"] in FINAL:
            self._save(cid, out)
        return case

    @staticmethod
    def registry_plan_changes(case_id: str, plan_id: str, messages: list[dict]) -> list:
        """changes_vs_default of the submitted plan, read back from its propose result."""
        for m in reversed(messages):
            if m["role"] == "tool" and f'"plan_id":"{plan_id}"' in m["content"]:
                result = json.loads(m["content"])
                if "changes_vs_default" in result:
                    return result["changes_vs_default"]
        return []
