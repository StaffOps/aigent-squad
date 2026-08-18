"""Tests for src/core/capability_gate.py — capability tier enforcement (spec 43).

Tests against the CONTRACT:
- Tier 0 denies ALL write actions.
- Non-write actions always pass regardless of tier.
- Unknown/None agent defaults to Tier 0 (deny writes).
- Tiers 1-3 pass through (Phase 1 behavior).
- Singleton lifecycle (init/get).
- CapabilityDeniedError carries correct context fields.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from src.core.capability_gate import (
    CapabilityDeniedError,
    CapabilityGate,
    WRITE_ACTION_TYPES,
    get_capability_gate,
    init_capability_gate,
)
from src.core.agent_config import AgentConfig


def _make_config(name: str, tier: int, write_scope: list[str] | None = None) -> AgentConfig:
    """Create a minimal AgentConfig for gating tests."""
    return AgentConfig(
        name=name,
        description=f"Test agent {name}",
        domain="testing",
        capabilities=["test-cap"],
        capability_tier=tier,
        read_only=(tier == 0),
        write_scope=write_scope or ([] if tier == 0 else ["some_tool"]),
    )


# ─── Tier 0 — Read-only (deny all writes) ────────────────────────────────────


class TestTier0:
    def test_tier0_write_action_denied(self):
        """Tier 0 agent attempting any write action → CapabilityDeniedError."""
        configs = {"observer": _make_config("observer", tier=0)}
        gate = CapabilityGate(configs)

        with pytest.raises(CapabilityDeniedError) as exc_info:
            gate.authorize("observer", "kubectl", "write")

        assert exc_info.value.agent_id == "observer"
        assert exc_info.value.tool_name == "kubectl"
        assert exc_info.value.action_type == "write"
        assert exc_info.value.tier == 0

    def test_tier0_all_write_action_types_denied(self):
        """Tier 0 denies every action type in WRITE_ACTION_TYPES."""
        configs = {"observer": _make_config("observer", tier=0)}
        gate = CapabilityGate(configs)

        for action_type in WRITE_ACTION_TYPES:
            with pytest.raises(CapabilityDeniedError):
                gate.authorize("observer", "some_tool", action_type)

    def test_tier0_read_action_passes(self):
        """Tier 0 agent calling a read action → no error."""
        configs = {"observer": _make_config("observer", tier=0)}
        gate = CapabilityGate(configs)

        # Should not raise
        gate.authorize("observer", "kubectl", "read")
        gate.authorize("observer", "kubectl", "list")
        gate.authorize("observer", "kubectl", "describe")


# ─── Unknown/None agent — defaults to Tier 0 ─────────────────────────────────


class TestUnknownAgent:
    def test_none_agent_id_write_denied(self):
        """None agent_id → defaults to Tier 0 → write denied."""
        configs = {"known": _make_config("known", tier=1)}
        gate = CapabilityGate(configs)

        with pytest.raises(CapabilityDeniedError) as exc_info:
            gate.authorize(None, "kubectl", "write")

        assert exc_info.value.agent_id == "__unknown__"
        assert exc_info.value.tier == 0

    def test_unknown_agent_name_write_denied(self):
        """Agent name not in configs → defaults to Tier 0 → write denied."""
        configs = {"known": _make_config("known", tier=1)}
        gate = CapabilityGate(configs)

        with pytest.raises(CapabilityDeniedError) as exc_info:
            gate.authorize("ghost-agent", "kubectl", "create")

        assert exc_info.value.agent_id == "ghost-agent"
        assert exc_info.value.tier == 0

    def test_none_agent_id_read_passes(self):
        """None agent_id + read action → passes (reads unrestricted)."""
        configs = {}
        gate = CapabilityGate(configs)

        gate.authorize(None, "kubectl", "read")  # no raise


# ─── Tier 1 — Scoped writes (Phase 1: pass-through) ──────────────────────────


class TestTier1:
    def test_tier1_write_action_passes(self):
        """Tier 1 agent writing → passes in Phase 1 (no scope enforcement yet)."""
        configs = {"writer": _make_config("writer", tier=1, write_scope=["kubectl"])}
        gate = CapabilityGate(configs)

        # Should not raise (Phase 1 pass-through for tier >= 1)
        gate.authorize("writer", "kubectl", "write")
        gate.authorize("writer", "kubectl", "create")
        gate.authorize("writer", "kubectl", "delete")

    def test_tier1_read_passes(self):
        """Tier 1 agent reading → always passes."""
        configs = {"writer": _make_config("writer", tier=1)}
        gate = CapabilityGate(configs)

        gate.authorize("writer", "kubectl", "read")


# ─── Tier 2 — Broad writes (Phase 1: pass-through) ───────────────────────────


class TestTier2:
    def test_tier2_write_action_passes(self):
        """Tier 2 agent writing → passes in Phase 1."""
        configs = {"ops": _make_config("ops", tier=2, write_scope=["helm", "kubectl"])}
        gate = CapabilityGate(configs)

        gate.authorize("ops", "helm", "apply")
        gate.authorize("ops", "kubectl", "patch")

    def test_tier2_write_outside_scope_still_passes_phase1(self):
        """Tier 2 writing outside write_scope → passes in Phase 1 (scope not enforced yet)."""
        configs = {"ops": _make_config("ops", tier=2, write_scope=["helm"])}
        gate = CapabilityGate(configs)

        # Phase 1 does not enforce scope; tiers >= 1 pass all writes
        gate.authorize("ops", "other_tool", "mutate")


# ─── Singleton lifecycle ──────────────────────────────────────────────────────


class TestSingleton:
    def test_init_capability_gate_creates_instance(self):
        """init_capability_gate() creates and returns a CapabilityGate."""
        configs = {"a": _make_config("a", tier=0)}

        with patch("src.core.capability_gate._gate_instance", None):
            gate = init_capability_gate(configs)

        assert isinstance(gate, CapabilityGate)

    def test_get_capability_gate_returns_none_before_init(self):
        """get_capability_gate() returns None if init hasn't been called."""
        with patch("src.core.capability_gate._gate_instance", None):
            assert get_capability_gate() is None

    def test_get_capability_gate_returns_instance_after_init(self):
        """get_capability_gate() returns the gate after init_capability_gate()."""
        configs = {"a": _make_config("a", tier=0)}

        with patch("src.core.capability_gate._gate_instance", None):
            init_capability_gate(configs)
            result = get_capability_gate()

        assert result is not None
        assert isinstance(result, CapabilityGate)


# ─── CapabilityDeniedError ────────────────────────────────────────────────────


class TestCapabilityDeniedError:
    def test_error_fields(self):
        """CapabilityDeniedError stores all context fields."""
        err = CapabilityDeniedError(
            agent_id="test-agent",
            tool_name="kubectl",
            action_type="delete",
            tier=0,
        )

        assert err.agent_id == "test-agent"
        assert err.tool_name == "kubectl"
        assert err.action_type == "delete"
        assert err.tier == 0

    def test_error_message_format(self):
        """Error message contains agent_id, tier, action_type, and tool_name."""
        err = CapabilityDeniedError(
            agent_id="observer",
            tool_name="helm",
            action_type="apply",
            tier=0,
        )

        msg = str(err)
        assert "observer" in msg
        assert "tier=0" in msg
        assert "apply" in msg
        assert "helm" in msg

    def test_error_is_permission_error(self):
        """CapabilityDeniedError is a PermissionError subclass."""
        err = CapabilityDeniedError("a", "b", "c", 0)
        assert isinstance(err, PermissionError)


# ─── Non-write action types always pass ──────────────────────────────────────


class TestNonWriteActions:
    @pytest.mark.parametrize("action_type", ["read", "list", "get", "describe", "query", "watch"])
    def test_non_write_actions_always_pass(self, action_type):
        """Any action not in WRITE_ACTION_TYPES passes regardless of tier."""
        configs = {"locked": _make_config("locked", tier=0)}
        gate = CapabilityGate(configs)

        # Should not raise even for Tier 0
        gate.authorize("locked", "any_tool", action_type)
