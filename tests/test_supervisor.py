"""Tests for SupervisorAgent — routes queries to correct agent."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.agent_config import AgentConfig
from src.core.classifier import ClassifierResult, AgentMatch
from src.core.state_store import ConversationMessage


def _make_mock_registry():
    """Create a mock registry with 1 agent."""
    config = AgentConfig(
        name="aws",
        description="AWS test agent",
        domain="cloud",
        capabilities=["test"],
        datasources=[],
    )
    registry = MagicMock()
    registry.list_agents.return_value = [config]
    registry.get_prompt.return_value = "You are a test agent."
    registry.agent_names.return_value = ["aws"]
    registry.agents = {"aws": config}
    return registry


@pytest.mark.asyncio
async def test_process_request_routes_to_agent():
    """Classifier returns 'aws' → supervisor calls the aws GenericAgent (single-agent fast path)."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    supervisor.classifier.classify = AsyncMock(
        return_value=ClassifierResult(
            agents=[AgentMatch(agent="aws", confidence=0.95)],
            reasoning="EC2 query"
        )
    )
    supervisor.agents["aws"].process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant", content="test response",
            timestamp="2026-01-01T00:00:00", agent_id="aws"
        )
    )

    with patch("src.supervisor.agent.storage") as mock_storage:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_storage.fetch_chat = AsyncMock(return_value=[])

        result = await supervisor.process_request("list ec2", "user1", "sess1")

    assert result["agent"] == "aws"
    assert result["response"] == "test response"
    assert result["confidence"] == 0.95


@pytest.mark.asyncio
async def test_process_request_unknown_agent_fallback():
    """Classifier returns empty agents → supervisor returns rephrase message."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    supervisor.classifier.classify = AsyncMock(
        return_value=ClassifierResult(agents=[], reasoning="No match")
    )

    with patch("src.supervisor.agent.storage") as mock_storage:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])

        result = await supervisor.process_request("gibberish", "user1", "sess1")

    assert result["agent"] == "supervisor"
    assert "rephrase" in result["response"].lower()


@pytest.mark.asyncio
async def test_process_request_triggers_investigation_on_keyword():
    """When should_investigate returns True, supervisor runs investigation."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    rca_dict = {
        "hypothesis": "Memory leak",
        "confidence": "alta",
        "evidence": [],
        "timeline": [],
        "contradicting": [],
        "prevention": ["add alert"],
    }
    mock_rca = MagicMock()
    mock_rca.hypothesis = "Memory leak"
    mock_rca.confidence = "alta"
    mock_rca.to_dict.return_value = rca_dict

    with patch("src.supervisor.agent.should_investigate", return_value=True), \
         patch("src.supervisor.agent.run_investigation", new_callable=AsyncMock, return_value=mock_rca), \
         patch("src.supervisor.agent.distill_rca", new_callable=AsyncMock):
        result = await supervisor.process_request(
            "why is service X failing with OOM after deploy", "user1", "sess1"
        )

    assert result["agent"] == "investigation"
    assert "rca" in result
    assert result["rca"]["hypothesis"] == "Memory leak"


@pytest.mark.asyncio
async def test_process_request_handles_classifier_failure_gracefully():
    """When classifier raises, supervisor returns error response."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    supervisor.classifier.classify = AsyncMock(side_effect=RuntimeError("LLM down"))

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.should_investigate", return_value=False):
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])

        result = await supervisor.process_request("some query", "user1", "sess1")

    assert result["agent"] == "supervisor"
    assert "error" in result
    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_process_request_force_agent_bypasses_classifier():
    """force_agent (OpenAI bridge per-agent model) routes directly, no classify."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    # Classifier must NOT be called on the forced path.
    supervisor.classifier.classify = AsyncMock(side_effect=AssertionError("classifier should be bypassed"))
    supervisor.agents["aws"].process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant", content="forced response",
            timestamp="2026-01-01T00:00:00", agent_id="aws"
        )
    )

    with patch("src.supervisor.agent.storage") as mock_storage:
        mock_storage.save_chat_message = AsyncMock()
        mock_storage.fetch_chat = AsyncMock(return_value=[])

        result = await supervisor.process_request(
            "list ec2", "user1", "sess1", force_agent="aws"
        )

    assert result["agent"] == "aws"
    assert result["response"] == "forced response"


@pytest.mark.asyncio
async def test_process_request_force_unknown_agent_falls_through_to_classifier():
    """An unknown force_agent is ignored → normal classifier path runs."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    supervisor.classifier.classify = AsyncMock(
        return_value=ClassifierResult(
            agents=[AgentMatch(agent="aws", confidence=0.9)],
            reasoning="ec2"
        )
    )
    supervisor.agents["aws"].process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant", content="classified response",
            timestamp="2026-01-01T00:00:00", agent_id="aws"
        )
    )

    with patch("src.supervisor.agent.storage") as mock_storage, \
         patch("src.supervisor.agent.should_investigate", return_value=False):
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_storage.fetch_chat = AsyncMock(return_value=[])

        result = await supervisor.process_request(
            "list ec2", "user1", "sess1", force_agent="ghost"
        )

    assert result["agent"] == "aws"
    assert result["response"] == "classified response"
