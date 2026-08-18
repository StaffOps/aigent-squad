"""Agent-as-tools: an agent can request data from another agent (1 hop max)."""
import contextvars
from typing import Any

# Tracks call depth per async context
_call_depth: contextvars.ContextVar[int] = contextvars.ContextVar("agent_call_depth", default=0)


class AgentToolError(Exception):
    pass


class AgentTools:
    """Helper that lets a GenericAgent call other agents (depth=1 max)."""

    def __init__(self, supervisor: Any) -> None:
        self._supervisor = supervisor

    async def ask(self, agent_name: str, query: str, user_id: str = "agent-tool", session_id: str = "agent-tool") -> str:
        depth = _call_depth.get()
        if depth >= 1:
            raise AgentToolError(f"Max agent-as-tools depth reached (depth={depth}). Cannot call {agent_name}.")

        target = self._supervisor.agents.get(agent_name)
        if not target:
            raise AgentToolError(f"Unknown agent: {agent_name}")

        token = _call_depth.set(depth + 1)
        try:
            result = await target.process_request(
                input_text=query, user_id=user_id, session_id=session_id, chat_history=[]
            )
            content: str = result.content
            return content
        finally:
            _call_depth.reset(token)
