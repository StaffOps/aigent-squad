"""Tests for B-14: classifier emits per-agent sub_query, dispatch uses it.

Validates:
  1. AgentMatch carries sub_query field (default empty).
  2. Classifier prompt asks for sub_query; parser extracts it.
  3. Dispatch (single + fan-out + streaming) passes sub_query to agent when
     present; falls back to raw user_input when absent/empty.
  4. Ingress guardrail still sees the RAW user question (not sub_query).
  5. Backward-compatible: missing sub_query in JSON → empty string → raw input used.
"""
import json
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import asdict

import pytest

from src.core.classifier import Classifier, ClassifierResult, AgentMatch
from src.core.agent_config import AgentConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_registry():
    """Create a mock registry with 2 agents."""
    aws_config = AgentConfig(
        name="aws", description="AWS specialist", domain="cloud", capabilities=["ec2"]
    )
    obs_config = AgentConfig(
        name="observability", description="Observability specialist", domain="monitoring",
        capabilities=["metrics", "traces"]
    )
    registry = MagicMock()
    registry.agent_names.return_value = ["aws", "observability"]
    registry.list_agents.return_value = [aws_config, obs_config]
    return registry


# ---------------------------------------------------------------------------
# 1. AgentMatch dataclass
# ---------------------------------------------------------------------------

class TestAgentMatchSubQuery:
    """AgentMatch.sub_query field exists and defaults to empty string."""

    def test_default_empty(self):
        m = AgentMatch(agent="aws", confidence=0.9)
        assert m.sub_query == ""

    def test_explicit_value(self):
        m = AgentMatch(agent="aws", confidence=0.9, sub_query="Show EC2 costs for the last 1h")
        assert m.sub_query == "Show EC2 costs for the last 1h"

    def test_serializable(self):
        m = AgentMatch(agent="obs", confidence=0.8, sub_query="Check error rate")
        d = asdict(m)
        assert d["sub_query"] == "Check error rate"


# ---------------------------------------------------------------------------
# 2. Classifier prompt + parse
# ---------------------------------------------------------------------------

class TestClassifierSubQueryParse:
    """Classifier parses sub_query from LLM JSON response."""

    @pytest.mark.asyncio
    async def test_sub_query_parsed_from_response(self):
        registry = _make_registry()
        classifier = Classifier(registry)

        bedrock_response = json.dumps({
            "agents": [
                {"agent": "observability", "confidence": 0.92,
                 "sub_query": "What is the p99 latency for service X in the last 30min?"}
            ],
            "reasoning": "Time-bound observability query"
        })

        with patch("src.core.classifier.bedrock") as mock_bedrock:
            mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
            result = await classifier.classify("como tá a latência do service X na última meia hora?", [])

        assert result.agents[0].sub_query == "What is the p99 latency for service X in the last 30min?"
        assert result.agents[0].agent == "observability"

    @pytest.mark.asyncio
    async def test_sub_query_missing_defaults_empty(self):
        """Backward compat: old-style response without sub_query → empty string."""
        registry = _make_registry()
        classifier = Classifier(registry)

        bedrock_response = json.dumps({
            "agents": [{"agent": "aws", "confidence": 0.95}],
            "reasoning": "EC2 query"
        })

        with patch("src.core.classifier.bedrock") as mock_bedrock:
            mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
            result = await classifier.classify("list EC2 instances", [])

        assert result.agents[0].sub_query == ""

    @pytest.mark.asyncio
    async def test_multi_agent_each_gets_sub_query(self):
        """Fan-out: each agent gets its own sub_query."""
        registry = _make_registry()
        classifier = Classifier(registry)

        bedrock_response = json.dumps({
            "agents": [
                {"agent": "aws", "confidence": 0.85,
                 "sub_query": "Check if any EC2 instance was terminated in the last 2h"},
                {"agent": "observability", "confidence": 0.80,
                 "sub_query": "Show error rate spike for service Y in the last 2h"},
            ],
            "reasoning": "Cross-domain: infra change + observability impact"
        })

        with patch("src.core.classifier.bedrock") as mock_bedrock:
            mock_bedrock.invoke = AsyncMock(return_value=bedrock_response)
            result = await classifier.classify(
                "por que o custo subiu depois do deploy nas últimas 2h?", []
            )

        assert len(result.agents) == 2
        assert result.agents[0].sub_query == "Check if any EC2 instance was terminated in the last 2h"
        assert result.agents[1].sub_query == "Show error rate spike for service Y in the last 2h"


# ---------------------------------------------------------------------------
# 3. Classifier prompt contains sub_query instruction
# ---------------------------------------------------------------------------

class TestClassifierPromptContainsSubQuery:
    """The SYSTEM_PROMPT now instructs the model to emit sub_query."""

    def test_prompt_mentions_sub_query(self):
        registry = _make_registry()
        classifier = Classifier(registry)
        assert "sub_query" in classifier.SYSTEM_PROMPT
        assert "time window" in classifier.SYSTEM_PROMPT.lower() or "time window" in classifier.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 4. Dispatch uses sub_query (supervisor integration)
# ---------------------------------------------------------------------------

class TestDispatchSubQuery:
    """Supervisor dispatch passes sub_query to agent.process_request."""

    @pytest.mark.asyncio
    async def test_single_agent_uses_sub_query(self):
        """_single_agent_call passes sub_query as input_text."""
        from src.supervisor.agent import SupervisorAgent

        mock_agent = AsyncMock()
        mock_agent.process_request = AsyncMock(return_value=MagicMock(content="answer"))

        with patch("src.supervisor.agent.storage") as mock_storage:
            mock_storage.fetch_chat = AsyncMock(return_value=[])
            mock_storage.save_chat_message = AsyncMock()

            sup = MagicMock(spec=SupervisorAgent)
            sup.agents = {"observability": mock_agent}
            sup._record_metrics = MagicMock()
            sup._save_assistant_message = AsyncMock()

            classification = ClassifierResult(
                agents=[AgentMatch(
                    agent="observability", confidence=0.9,
                    sub_query="Check p99 latency for svc-X in last 1h"
                )],
                reasoning="time-bound obs query"
            )

            await SupervisorAgent._single_agent_call(
                sup, "observability", classification,
                "como tá a latência?", "user1", "sess1", 0.0
            )

        # Verify agent received the sub_query, not the raw input
        call_kwargs = mock_agent.process_request.call_args[1]
        assert call_kwargs["input_text"] == "Check p99 latency for svc-X in last 1h"

    @pytest.mark.asyncio
    async def test_single_agent_fallback_to_raw_when_empty(self):
        """When sub_query is empty, raw user_input is used."""
        from src.supervisor.agent import SupervisorAgent

        mock_agent = AsyncMock()
        mock_agent.process_request = AsyncMock(return_value=MagicMock(content="answer"))

        with patch("src.supervisor.agent.storage") as mock_storage:
            mock_storage.fetch_chat = AsyncMock(return_value=[])
            mock_storage.save_chat_message = AsyncMock()

            sup = MagicMock(spec=SupervisorAgent)
            sup.agents = {"aws": mock_agent}
            sup._record_metrics = MagicMock()
            sup._save_assistant_message = AsyncMock()

            classification = ClassifierResult(
                agents=[AgentMatch(agent="aws", confidence=0.9, sub_query="")],
                reasoning="follow-up"
            )

            await SupervisorAgent._single_agent_call(
                sup, "aws", classification,
                "yes", "user1", "sess1", 0.0
            )

        call_kwargs = mock_agent.process_request.call_args[1]
        assert call_kwargs["input_text"] == "yes"

    @pytest.mark.asyncio
    async def test_fanout_each_agent_gets_own_sub_query(self):
        """Fan-out passes each agent its own sub_query."""
        from src.supervisor.agent import SupervisorAgent

        mock_aws = AsyncMock()
        mock_aws.process_request = AsyncMock(return_value=MagicMock(content="aws answer"))
        mock_obs = AsyncMock()
        mock_obs.process_request = AsyncMock(return_value=MagicMock(content="obs answer"))

        agents_list = [
            AgentMatch(agent="aws", confidence=0.85, sub_query="Check EC2 terminations last 2h"),
            AgentMatch(agent="observability", confidence=0.80, sub_query="Error rate for svc-Y last 2h"),
        ]

        with patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent.synthesizer") as mock_synth:
            mock_storage.save_chat_message = AsyncMock()
            mock_synth.synthesize = AsyncMock(return_value="synthesized")

            sup = MagicMock(spec=SupervisorAgent)
            sup.agents = {"aws": mock_aws, "observability": mock_obs}
            sup._record_metrics = MagicMock()
            sup._save_assistant_message = AsyncMock()

            classification = ClassifierResult(agents=agents_list, reasoning="cross-domain")

            await SupervisorAgent._fan_out(
                sup, agents_list, classification,
                "por que o custo subiu?", "user1", "sess1", 0.0
            )

        # AWS got its own sub_query
        aws_kwargs = mock_aws.process_request.call_args[1]
        assert aws_kwargs["input_text"] == "Check EC2 terminations last 2h"

        # Observability got its own sub_query
        obs_kwargs = mock_obs.process_request.call_args[1]
        assert obs_kwargs["input_text"] == "Error rate for svc-Y last 2h"

    @pytest.mark.asyncio
    async def test_fanout_fallback_when_sub_query_empty(self):
        """Fan-out: agent with empty sub_query gets the raw user_input."""
        from src.supervisor.agent import SupervisorAgent

        mock_aws = AsyncMock()
        mock_aws.process_request = AsyncMock(return_value=MagicMock(content="aws answer"))
        mock_obs = AsyncMock()
        mock_obs.process_request = AsyncMock(return_value=MagicMock(content="obs answer"))

        agents_list = [
            AgentMatch(agent="aws", confidence=0.85, sub_query="Check EC2 costs"),
            AgentMatch(agent="observability", confidence=0.80, sub_query=""),  # no sub_query
        ]

        with patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent.synthesizer") as mock_synth:
            mock_storage.save_chat_message = AsyncMock()
            mock_synth.synthesize = AsyncMock(return_value="synthesized")

            sup = MagicMock(spec=SupervisorAgent)
            sup.agents = {"aws": mock_aws, "observability": mock_obs}
            sup._record_metrics = MagicMock()
            sup._save_assistant_message = AsyncMock()

            classification = ClassifierResult(agents=agents_list, reasoning="cross-domain")

            await SupervisorAgent._fan_out(
                sup, agents_list, classification,
                "raw question here", "user1", "sess1", 0.0
            )

        aws_kwargs = mock_aws.process_request.call_args[1]
        assert aws_kwargs["input_text"] == "Check EC2 costs"

        obs_kwargs = mock_obs.process_request.call_args[1]
        assert obs_kwargs["input_text"] == "raw question here"  # fallback


# ---------------------------------------------------------------------------
# 5. Ingress guardrail still sees RAW user question
# ---------------------------------------------------------------------------

class TestIngressGuardrailOnRawInput:
    """The ingress guardrail at trust boundary must see the original user input,
    NOT the classifier-generated sub_query (trusted transformation)."""

    @pytest.mark.asyncio
    async def test_guardrail_receives_raw_input_not_sub_query(self):
        """Confirms guardrail.apply is called with the raw user_input."""
        from src.supervisor.agent import SupervisorAgent

        # We verify by patching guardrail.apply and checking what it received.
        # The supervisor imports guardrail inside process_request via:
        #   from src.core.guardrail import guardrail
        # So we patch at the definition site.
        with patch("src.core.guardrail.guardrail") as mock_guard, \
             patch("src.supervisor.agent.InputScanner") as mock_scanner, \
             patch("src.supervisor.agent.storage") as mock_storage, \
             patch("src.supervisor.agent.budget_tracker") as mock_budget:

            # InputScanner passes through (no normalization needed for test)
            scanner_instance = MagicMock()
            scanner_instance.scan = MagicMock(side_effect=lambda x, **kw: x)
            mock_scanner.return_value = scanner_instance

            mock_guard.apply = MagicMock()
            mock_budget.check_budget = MagicMock()
            mock_storage.fetch_all_chats = AsyncMock(return_value=[])
            mock_storage.fetch_chat = AsyncMock(return_value=[])
            mock_storage.save_chat_message = AsyncMock()

            # Classifier returns a sub_query different from raw input
            mock_classifier = AsyncMock()
            mock_classifier.classify = AsyncMock(return_value=ClassifierResult(
                agents=[AgentMatch(
                    agent="observability", confidence=0.9,
                    sub_query="Focused question about metrics"
                )],
                reasoning="test"
            ))

            mock_agent = AsyncMock()
            mock_agent.process_request = AsyncMock(return_value=MagicMock(content="resp"))

            sup = SupervisorAgent.__new__(SupervisorAgent)
            sup.classifier = mock_classifier
            sup.max_agents = 3
            sup.agents = {"observability": mock_agent}

            raw_input = "quero saber sobre as métricas"
            await sup.process_request(raw_input, "u1", "s1")

            # Guardrail saw the RAW input
            mock_guard.apply.assert_called_once()
            guarded_text = mock_guard.apply.call_args[0][0]
            assert guarded_text == raw_input
            assert guarded_text != "Focused question about metrics"
