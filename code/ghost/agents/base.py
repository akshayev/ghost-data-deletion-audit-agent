"""Agent protocol. An agent owns one step of the audit and reaches the domain layer ONLY
through the tool registry, using a caller bound to the tools it declares in tools_used."""

from typing import Protocol

from ghost.tools.registry import BoundTools, ToolRegistry


class Agent(Protocol):
    name: str
    role: str  # one sentence
    tools_used: tuple[str, ...]

    def run(self, ctx): ...


class BaseAgent:
    name = ""
    role = ""
    tools_used: tuple[str, ...] = ()

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def tools(self, case_id: str | None = None) -> BoundTools:
        return self.registry.bind(self.name, self.tools_used, case_id)

    def note(self, case_id: str | None, summary: str, **data) -> None:
        """A trace event that is not a tool call (the agent's own conclusion)."""
        self.registry.tracer.emit(case_id, self.name, None, summary, data)
