"""Capability gate — enforces write-permission tiering per agent (spec 43).

The gate is the single enforcement point for capability tiers. It runs
BEFORE tool execution and denies write actions for agents without the
appropriate tier.

Tier semantics:
  0 — Read-only. ALL write actions denied.
  1 — Scoped writes (write_scope allowlist). No HITL required.
  2 — Broad writes (write_scope allowlist). No HITL required.
  3 — Full writes (write_scope allowlist) + HITL approval required.

Phase 1 only enforces Tier 0 (deny all writes). Tiers 1-3 pass through
for future enforcement in Phase 2.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

from src.core.agent_config import AgentConfig


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context variable: set by the agentic loop to identify the current agent.
# If unset (backwards compat), defaults to None → treated as Tier 0.
# ---------------------------------------------------------------------------

current_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_agent_id", default=None
)


# ---------------------------------------------------------------------------
# Action types that constitute a "write" for gating purposes.
# ---------------------------------------------------------------------------

WRITE_ACTION_TYPES: frozenset[str] = frozenset({
    "write",
    "create",
    "update",
    "delete",
    "mutate",
    "apply",
    "patch",
    "exec",
})


class CapabilityDeniedError(PermissionError):
    """Raised when an agent attempts an action above its capability tier."""

    def __init__(
        self,
        agent_id: str,
        tool_name: str,
        action_type: str,
        tier: int,
    ) -> None:
        self.agent_id = agent_id
        self.tool_name = tool_name
        self.action_type = action_type
        self.tier = tier
        super().__init__(
            f"[capability_gate] DENIED: agent={agent_id!r} tier={tier} "
            f"attempted {action_type!r} via tool={tool_name!r}"
        )


class CapabilityGate:
    """Stateless gate that checks whether an agent may perform an action.

    Usage:
        gate = CapabilityGate(agent_configs)
        gate.authorize(agent_id, tool_name, action_type)
        # raises CapabilityDeniedError if denied
    """

    def __init__(self, agent_configs: dict[str, AgentConfig]) -> None:
        self._configs = agent_configs

    def authorize(
        self,
        agent_id: str | None,
        tool_name: str,
        action_type: str,
    ) -> None:
        """Check if the agent is allowed to perform this action.

        Args:
            agent_id: Agent name. None or unknown → defaults to Tier 0.
            tool_name: The MCP/adapter tool being called.
            action_type: Semantic action type (read, write, create, etc.).

        Raises:
            CapabilityDeniedError: if the action is denied by the agent's tier.
        """
        # Non-write actions always pass (reads are unrestricted).
        if action_type not in WRITE_ACTION_TYPES:
            return

        # Resolve tier — unknown/missing agents default to Tier 0 (safest).
        tier = 0
        resolved_id = agent_id or "__unknown__"
        if agent_id and agent_id in self._configs:
            tier = self._configs[agent_id].capability_tier

        # Phase 1: Tier 0 denies ALL writes.
        if tier == 0:
            logger.warning(
                "Capability gate denied write action",
                extra={
                    "agent_id": resolved_id,
                    "tool_name": tool_name,
                    "action_type": action_type,
                    "capability_tier": tier,
                    "event": "capability_denied",
                },
            )
            raise CapabilityDeniedError(
                agent_id=resolved_id,
                tool_name=tool_name,
                action_type=action_type,
                tier=tier,
            )

        # Tiers 1-3: pass through (Phase 2 will add scope + HITL checks).


# ---------------------------------------------------------------------------
# Module-level singleton (initialized lazily by the application startup).
# ---------------------------------------------------------------------------

_gate_instance: CapabilityGate | None = None


def init_capability_gate(agent_configs: dict[str, AgentConfig]) -> CapabilityGate:
    """Initialize the global capability gate with loaded agent configs."""
    global _gate_instance
    _gate_instance = CapabilityGate(agent_configs)
    return _gate_instance


def get_capability_gate() -> CapabilityGate | None:
    """Return the initialized gate, or None if not yet initialized."""
    return _gate_instance
