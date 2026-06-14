"""Tests for supervisor fan-out behavior (spec 17)."""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.agent_config import AgentConfig
from src.core.classifier import ClassifierResult, AgentMatch
from src.core.state_store import ConversationMessage


def _make_registry(*agent_names):
    configs = [
        AgentConfig(name=n, description=f"{n} agent", domain="test", capabilities=[], datasources=[])
        for n in agent_names
    ]
    registry = MagicMock()
    registry.list_agents.return_value = configs
    registry.get_prompt.return_value = "test prompt"
    registry.agent_names.return_value = list(agent_names)
    return registry


def _build_supervisor(agent_names):
    registry = _make_registry(*agent_names)
    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        return SupervisorAgent(registry)


def _mock_response(content):
    return ConversationMessage(
        role="assistant", content=content,
        timestamp="2026-01-01T00:00:00", agent_id="test"
    )


@pytest.mark.asyncio
async def test_fan_out_calls_agents_in_parallel():
    """2+ agents classified → called via asyncio.gather (parallel, not sequential)."""
    supervisor = _build_supervisor(["aws", "k8s"])

    supervisor.classifier.classify = AsyncMock(return_value=ClassifierResult(
        agents=[AgentMatch("aws", 0.9), AgentMatch("k8s", 0.8)]
    ))

    async def slow_agent(*args, **kwargs):
        await asyncio.sleep(0.1)
        return _mock_response("response")

    supervisor.agents["aws"].process_request = AsyncMock(side_effect=slow_agent)
    supervisor.agents["k8s"].process_request = AsyncMock(side_effect=slow_agent)

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.synthesizer") as mock_synth:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_synth.synthesize = AsyncMock(return_value="synthesized")

        start = time.time()
        result = await supervisor.process_request("cross query", "u1", "s1")
        elapsed = time.time() - start

    # Parallel: ~0.1s. Sequential would be ~0.2s.
    assert elapsed < 0.18
    assert result["response"] == "synthesized"


@pytest.mark.asyncio
async def test_fan_out_synthesizes_responses():
    """Fan-out calls synthesizer with (responses, failed) args."""
    supervisor = _build_supervisor(["aws", "k8s"])

    supervisor.classifier.classify = AsyncMock(return_value=ClassifierResult(
        agents=[AgentMatch("aws", 0.9), AgentMatch("k8s", 0.8)]
    ))
    supervisor.agents["aws"].process_request = AsyncMock(return_value=_mock_response("r1"))
    supervisor.agents["k8s"].process_request = AsyncMock(return_value=_mock_response("r2"))

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.synthesizer") as mock_synth:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_synth.synthesize = AsyncMock(return_value="fused")

        result = await supervisor.process_request("q", "u1", "s1")

    mock_synth.synthesize.assert_called_once()
    call_args = mock_synth.synthesize.call_args
    assert call_args[0][0] == "q"  # query
    assert ("aws", "r1") in call_args[0][1]
    assert ("k8s", "r2") in call_args[0][1]
    assert call_args[0][2] == []  # no failures
    assert result["response"] == "fused"


@pytest.mark.asyncio
async def test_fan_out_partial_failure():
    """1 agent fails → synthesizer still called with 1 response + 1 failed."""
    supervisor = _build_supervisor(["aws", "k8s"])

    supervisor.classifier.classify = AsyncMock(return_value=ClassifierResult(
        agents=[AgentMatch("aws", 0.9), AgentMatch("k8s", 0.8)]
    ))
    supervisor.agents["aws"].process_request = AsyncMock(return_value=_mock_response("ok"))
    supervisor.agents["k8s"].process_request = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.synthesizer") as mock_synth:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_synth.synthesize = AsyncMock(return_value="partial")

        result = await supervisor.process_request("q", "u1", "s1")

    call_args = mock_synth.synthesize.call_args[0]
    assert call_args[1] == [("aws", "ok")]
    assert call_args[2] == ["k8s"]
    assert result["agents_failed"] == ["k8s"]
    assert result["response"] == "partial"


@pytest.mark.asyncio
async def test_fan_out_max_agents_cap():
    """Classifier returns 5 agents → supervisor only calls top 3 (max_agents=3)."""
    names = ["a1", "a2", "a3", "a4", "a5"]
    supervisor = _build_supervisor(names)

    supervisor.classifier.classify = AsyncMock(return_value=ClassifierResult(
        agents=[AgentMatch(n, 0.9 - i * 0.1) for i, n in enumerate(names)]
    ))
    for n in names:
        supervisor.agents[n].process_request = AsyncMock(return_value=_mock_response(f"r-{n}"))

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.synthesizer") as mock_synth:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_synth.synthesize = AsyncMock(return_value="capped")

        result = await supervisor.process_request("q", "u1", "s1")

    assert result["agents_consulted"] == ["a1", "a2", "a3"]
    # a4, a5 should NOT have been called
    supervisor.agents["a4"].process_request.assert_not_called()
    supervisor.agents["a5"].process_request.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_n1_skips_synthesizer():
    """Single agent classification → fast-path, synthesizer NOT called."""
    supervisor = _build_supervisor(["aws"])

    supervisor.classifier.classify = AsyncMock(return_value=ClassifierResult(
        agents=[AgentMatch("aws", 0.95)]
    ))
    supervisor.agents["aws"].process_request = AsyncMock(return_value=_mock_response("direct"))

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.synthesizer") as mock_synth:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_storage.fetch_chat = AsyncMock(return_value=[])

        result = await supervisor.process_request("q", "u1", "s1")

    mock_synth.synthesize.assert_not_called()
    assert result["agent"] == "aws"
    assert result["response"] == "direct"
