"""Independent tests for spec 38 bugfix — tier routing on ALL request paths.

Tests the CONTRACT: _resolve_tier_model must fire on every traffic path:
  - Path A: force_agent (non-streaming + streaming)
  - Path B: investigation orchestration
  - Path C: auto-route single agent (pre-existing, covered by existing tests)
  - Path D: fan-out (pre-existing, covered by existing tests)

The BUG: prior to the fix, force_agent and investigation paths never called
_resolve_tier_model → Opus/Haiku were never used despite config. These tests
verify the fix makes tier routing reachable on the previously-dead paths.

Run via Docker (no local SDK):
    docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
      'pip install -q -r .local-stubs/requirements.no-otel.txt && \
       pip install -q opentelemetry-api && \
       PYTHONPATH=.local-stubs:. pytest tests/test_spec38_tier_routing_all_paths.py \
       --cov=src.supervisor.agent --cov=src.supervisor.investigation \
       --cov-report=term-missing -v'
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.classifier import ClassifierResult, AgentMatch, Classifier
from src.core.state_store import ConversationMessage


# ══════════════════════════════════════════════════════════════════════════════
# Constants — real model IDs from the production overlay
# ══════════════════════════════════════════════════════════════════════════════

FAST_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
STANDARD_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEEP_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"
HIGH_CONFIDENCE = 0.85


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Reset tier settings before each test — isolated."""
    from src.core.config import settings
    monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", FAST_MODEL)
    monkeypatch.setattr(settings, "bedrock_tier_standard_model_id", STANDARD_MODEL)
    monkeypatch.setattr(settings, "bedrock_tier_deep_model_id", DEEP_MODEL)
    monkeypatch.setattr(settings, "aigent_tier_routing_enabled", True)
    monkeypatch.setattr(settings, "aigent_tier_deep_enabled", True)  # Deep enabled (Opus reachable)
    monkeypatch.setattr(settings, "aigent_tier_confidence_high", HIGH_CONFIDENCE)


def _make_investigation_agent(response: str = "[]"):
    """Create a mock agent matching the GenericAgent interface for investigation."""
    agent = MagicMock()
    agent.has_agentic_tools = MagicMock(return_value=True)
    agent.process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant",
            content=response,
            timestamp="2026-07-23T00:00:00Z",
        )
    )
    agent.process_request_streaming = AsyncMock(return_value=iter([]))
    return agent


def _make_mock_registry():
    """Create a mock registry matching the AgentConfig interface."""
    from src.core.agent_config import AgentConfig
    config = AgentConfig(
        name="observability",
        description="Observability test agent",
        domain="monitoring",
        capabilities=["test"],
        datasources=[],
    )
    registry = MagicMock()
    registry.list_agents.return_value = [config]
    registry.get_prompt.return_value = "You are a test agent."
    registry.agent_names.return_value = ["observability"]
    registry.agents = {"observability": config}
    return registry


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: force_agent NON-STREAMING path applies tier routing
# ══════════════════════════════════════════════════════════════════════════════


class TestForceAgentNonStreamingTierRouting:
    """Verify that force_agent (non-streaming) calls _resolve_tier_model
    and threads model_id_override through to _single_agent_call."""

    def test_short_query_heuristic_is_simple(self):
        """Short query (≤60 chars) + 1 agent → simple complexity."""
        match = AgentMatch(agent="observability", confidence=1.0)
        cx = Classifier._heuristic_complexity([match], "status?")
        assert cx == "simple"

    def test_short_query_resolves_to_haiku(self):
        """simple + high confidence (1.0 ≥ 0.85) → FAST tier → Haiku."""
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="observability", confidence=1.0)],
            reasoning="forced to observability",
            complexity="simple",
        )
        assert _resolve_tier_model(cr) == FAST_MODEL

    def test_long_query_heuristic_is_standard(self):
        """Long query (>60 chars) + 1 agent → standard complexity."""
        long_query = "Please analyze the CPU utilization trends for all production pods over the past 7 days"
        match = AgentMatch(agent="observability", confidence=1.0)
        cx = Classifier._heuristic_complexity([match], long_query)
        assert cx == "standard"

    def test_long_query_resolves_to_sonnet(self):
        """standard + high confidence → STANDARD tier → Sonnet."""
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="observability", confidence=1.0)],
            reasoning="forced to observability",
            complexity="standard",
        )
        assert _resolve_tier_model(cr) == STANDARD_MODEL

    @pytest.mark.asyncio
    async def test_force_agent_calls_resolve_tier_and_passes_override(self):
        """Integration: process_request with force_agent calls _resolve_tier_model
        and passes model_id_override to _single_agent_call."""
        from src.supervisor.agent import SupervisorAgent, _resolve_tier_model

        mock_registry = _make_mock_registry()
        mock_agent = _make_investigation_agent()

        with patch("src.supervisor.agent.create_adapters", return_value=[]):
            supervisor = SupervisorAgent(mock_registry)

        supervisor.agents = {"observability": mock_agent}

        with patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent.should_investigate", return_value=False), \
             patch("src.supervisor.agent._resolve_tier_model", wraps=_resolve_tier_model) as spy:
            mock_storage.save_chat_message = AsyncMock()
            mock_storage.fetch_chat = AsyncMock(return_value=[])
            mock_storage.fetch_all_chats = AsyncMock(return_value=[])

            # Patch _single_agent_call to avoid full Bedrock call
            supervisor._single_agent_call = AsyncMock(return_value={
                "response": "ok", "agent": "observability", "confidence": 1.0
            })

            await supervisor.process_request(
                user_input="status?",
                user_id="u1",
                session_id="s1",
                force_agent="observability",
            )

            # CONTRACT: _resolve_tier_model MUST be called for force_agent path
            spy.assert_called_once()
            passed_cr = spy.call_args[0][0]
            assert passed_cr.complexity == "simple"  # short query

            # _single_agent_call receives model_id_override
            call_kwargs = supervisor._single_agent_call.call_args
            # Check positional or keyword — the 7th positional is model_id_override
            # OR it's passed as kwarg
            all_args = call_kwargs.args + tuple(call_kwargs.kwargs.values())
            assert FAST_MODEL in all_args or call_kwargs.kwargs.get("model_id_override") == FAST_MODEL


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: force_agent STREAMING path applies tier routing
# ══════════════════════════════════════════════════════════════════════════════


class TestForceAgentStreamingTierRouting:
    """Verify that force_agent streaming (Path A) applies tier routing."""

    @pytest.mark.asyncio
    async def test_streaming_force_agent_calls_resolve_tier(self):
        """process_request_streaming Path A calls _resolve_tier_model."""
        from src.supervisor.agent import SupervisorAgent, _resolve_tier_model

        mock_registry = _make_mock_registry()
        mock_agent = _make_investigation_agent()

        with patch("src.supervisor.agent.create_adapters", return_value=[]):
            supervisor = SupervisorAgent(mock_registry)

        supervisor.agents = {"observability": mock_agent}

        with patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent._resolve_tier_model", wraps=_resolve_tier_model) as spy:
            mock_storage.save_chat_message = AsyncMock()
            mock_storage.fetch_chat = AsyncMock(return_value=[])

            await supervisor.process_request_streaming(
                user_input="status?",
                user_id="u1",
                session_id="s1",
                force_agent="observability",
            )

            # CONTRACT: _resolve_tier_model called on streaming force_agent path
            spy.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_short_query_passes_haiku_override(self):
        """Short streaming query → Haiku model_id_override to agent."""
        from src.supervisor.agent import SupervisorAgent

        mock_registry = _make_mock_registry()
        mock_agent = _make_investigation_agent()

        with patch("src.supervisor.agent.create_adapters", return_value=[]):
            supervisor = SupervisorAgent(mock_registry)

        supervisor.agents = {"observability": mock_agent}

        with patch("src.supervisor.agent.storage") as mock_storage:
            mock_storage.save_chat_message = AsyncMock()
            mock_storage.fetch_chat = AsyncMock(return_value=[])

            await supervisor.process_request_streaming(
                user_input="up?",  # ≤60 chars → simple → FAST
                user_id="u1",
                session_id="s1",
                force_agent="observability",
            )

            # agent.process_request_streaming must receive model_id_override=Haiku
            mock_agent.process_request_streaming.assert_called_once()
            kwargs = mock_agent.process_request_streaming.call_args.kwargs
            assert kwargs.get("model_id_override") == FAST_MODEL, (
                f"Short streaming query should get Haiku override, got: {kwargs.get('model_id_override')}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: Investigation path applies tier routing (→ Opus when deep enabled)
# ══════════════════════════════════════════════════════════════════════════════


class TestInvestigationTierRouting:
    """Verify that investigation path threads tier routing → Opus for complex."""

    def test_investigation_classification_is_complex(self):
        """The fix creates ClassifierResult with complexity='complex' for investigation."""
        from src.supervisor.agent import _resolve_tier_model
        inv_cr = ClassifierResult(
            agents=[AgentMatch(agent="investigation", confidence=0.9)],
            reasoning="investigation mode",
            complexity="complex",
        )
        model_id = _resolve_tier_model(inv_cr)
        # deep enabled + complex → deep tier → Opus
        assert model_id == DEEP_MODEL

    def test_investigation_falls_back_to_sonnet_when_deep_disabled(self, monkeypatch):
        """With deep disabled, investigation falls back complex → standard → Sonnet."""
        from src.core.config import settings
        monkeypatch.setattr(settings, "aigent_tier_deep_enabled", False)

        from src.supervisor.agent import _resolve_tier_model
        inv_cr = ClassifierResult(
            agents=[AgentMatch(agent="investigation", confidence=0.9)],
            reasoning="investigation mode",
            complexity="complex",
        )
        assert _resolve_tier_model(inv_cr) == STANDARD_MODEL

    @pytest.mark.asyncio
    async def test_run_investigation_threads_model_override_to_agents(self):
        """run_investigation passes model_id_override to each agent.process_request."""
        from src.supervisor.investigation import run_investigation

        evidence_json = json.dumps([
            {"signal_type": "metric", "timestamp": "2026-07-23T00:00:00Z",
             "strength": "forte", "summary": "CPU spike"},
        ])
        rca_json = json.dumps({
            "hypothesis": "OOM caused by memory leak",
            "reasoning": "evidence consistent",
            "contradicting_evidence_indices": [],
            "prevention": ["add memory alert"],
        })
        mock_agent = _make_investigation_agent(evidence_json)
        agents = {"observability": mock_agent, "kubernetes": _make_investigation_agent(evidence_json)}

        with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
            mock_bedrock.invoke = AsyncMock(return_value=rca_json)
            await run_investigation(
                symptom="OOMKill on tempo-distributor",
                agents=agents,
                user_id="u1",
                session_id="s1",
                model_id_override=DEEP_MODEL,
            )

        # Each agent.process_request must have model_id_override=DEEP_MODEL
        for name, a in agents.items():
            for c in a.process_request.call_args_list:
                assert c.kwargs.get("model_id_override") == DEEP_MODEL, (
                    f"Agent '{name}' process_request must receive model_id_override={DEEP_MODEL}"
                )

    @pytest.mark.asyncio
    async def test_run_investigation_accepts_none_override(self):
        """When routing disabled, model_id_override=None passes through cleanly."""
        from src.supervisor.investigation import run_investigation

        evidence_json = json.dumps([
            {"signal_type": "log", "timestamp": "2026-07-23T00:00:00Z",
             "strength": "media", "summary": "error logged"},
        ])
        rca_json = json.dumps({
            "hypothesis": "Unknown",
            "reasoning": "insufficient",
            "contradicting_evidence_indices": [],
            "prevention": [],
        })
        mock_agent = _make_investigation_agent(evidence_json)
        agents = {"observability": mock_agent}

        with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
            mock_bedrock.invoke = AsyncMock(return_value=rca_json)
            await run_investigation(
                symptom="test",
                agents=agents,
                user_id="u1",
                session_id="s1",
                model_id_override=None,
            )

        kwargs = mock_agent.process_request.call_args.kwargs
        assert kwargs.get("model_id_override") is None

    @pytest.mark.asyncio
    async def test_process_request_investigation_branch_threads_tier(self):
        """Supervisor.process_request investigation branch calls _resolve_tier_model
        and passes result to run_investigation."""
        from src.supervisor.agent import SupervisorAgent, _resolve_tier_model

        mock_registry = _make_mock_registry()
        mock_agent = _make_investigation_agent()

        with patch("src.supervisor.agent.create_adapters", return_value=[]):
            supervisor = SupervisorAgent(mock_registry)

        supervisor.agents = {"observability": mock_agent}

        with patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent.should_investigate", return_value=True), \
             patch("src.supervisor.agent.run_investigation", new_callable=AsyncMock) as mock_inv, \
             patch("src.supervisor.agent._resolve_tier_model", wraps=_resolve_tier_model) as spy:
            mock_storage.save_chat_message = AsyncMock()
            mock_storage.fetch_chat = AsyncMock(return_value=[])
            mock_storage.fetch_all_chats = AsyncMock(return_value=[])

            mock_rca = MagicMock()
            mock_rca.hypothesis = "test hypothesis"
            mock_rca.confidence = 0.8
            mock_rca.to_dict.return_value = {}
            mock_inv.return_value = mock_rca

            await supervisor.process_request(
                user_input="why is tempo OOMKilling?",
                user_id="u1",
                session_id="s1",
            )

            # CONTRACT: _resolve_tier_model called for investigation
            spy.assert_called_once()
            cr = spy.call_args[0][0]
            assert cr.complexity == "complex"

            # run_investigation receives model_id_override = Opus (deep enabled)
            inv_kwargs = mock_inv.call_args.kwargs
            assert inv_kwargs.get("model_id_override") == DEEP_MODEL


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Routing-disabled still produces None (no regression)
# ══════════════════════════════════════════════════════════════════════════════


class TestRoutingDisabledAllPaths:
    """When AIGENT_TIER_ROUTING_ENABLED=False, all paths get model_id_override=None."""

    @pytest.fixture(autouse=True)
    def _disable_routing(self, monkeypatch):
        from src.core.config import settings
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", False)

    def test_force_agent_disabled_returns_none(self):
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="obs", confidence=1.0)],
            reasoning="forced",
            complexity="simple",
        )
        assert _resolve_tier_model(cr) is None

    def test_investigation_disabled_returns_none(self):
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="investigation", confidence=0.9)],
            reasoning="investigation",
            complexity="complex",
        )
        assert _resolve_tier_model(cr) is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: Heuristic complexity correctness for force_agent scenarios
# ══════════════════════════════════════════════════════════════════════════════


class TestHeuristicComplexityForForceAgent:
    """Verify _heuristic_complexity produces correct tiers for forced agent inputs."""

    def test_very_short_query_is_simple(self):
        match = AgentMatch(agent="obs", confidence=1.0)
        assert Classifier._heuristic_complexity([match], "hi") == "simple"

    def test_exactly_60_chars_is_simple(self):
        match = AgentMatch(agent="obs", confidence=1.0)
        assert Classifier._heuristic_complexity([match], "a" * 60) == "simple"

    def test_61_chars_is_standard(self):
        match = AgentMatch(agent="obs", confidence=1.0)
        assert Classifier._heuristic_complexity([match], "a" * 61) == "standard"

    def test_multi_agent_is_complex(self):
        matches = [
            AgentMatch(agent="obs", confidence=0.8),
            AgentMatch(agent="k8s", confidence=0.7),
        ]
        assert Classifier._heuristic_complexity(matches, "any query") == "complex"

    def test_empty_query_is_simple(self):
        match = AgentMatch(agent="obs", confidence=1.0)
        assert Classifier._heuristic_complexity([match], "") == "simple"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: End-to-end tier dispatch matrix for forced agent scenarios
# ══════════════════════════════════════════════════════════════════════════════


class TestTierMatrixForceAgent:
    """Full dispatch matrix for force_agent (always confidence=1.0).

    force_agent has confidence=1.0 (by construction). So tier depends only on
    heuristic complexity:
      - simple + 1.0≥0.85 → FAST (Haiku)
      - standard + 1.0≥0.85 → STANDARD (Sonnet)
      - complex → DEEP (Opus)
    """

    def test_simple_high_conf_to_fast(self):
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="obs", confidence=1.0)],
            reasoning="forced",
            complexity="simple",
        )
        assert _resolve_tier_model(cr) == FAST_MODEL

    def test_standard_high_conf_to_standard(self):
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="obs", confidence=1.0)],
            reasoning="forced",
            complexity="standard",
        )
        assert _resolve_tier_model(cr) == STANDARD_MODEL

    def test_complex_to_deep_opus(self):
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="obs", confidence=0.9)],
            reasoning="investigation",
            complexity="complex",
        )
        assert _resolve_tier_model(cr) == DEEP_MODEL

    def test_low_confidence_routes_to_deep(self):
        """confidence < HIGH → deep tier regardless of complexity."""
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(
            agents=[AgentMatch(agent="obs", confidence=0.5)],
            reasoning="uncertain",
            complexity="standard",
        )
        # 0.5 < 0.85 → deep → Opus
        assert _resolve_tier_model(cr) == DEEP_MODEL
