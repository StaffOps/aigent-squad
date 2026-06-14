"""Tests for SupervisorAgent — routes queries to correct agent."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.agent_config import AgentConfig, DatasourceConfig
from src.core.classifier import ClassifierResult
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
    """Classifier returns 'aws' → supervisor calls the aws GenericAgent."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    # Mock classifier to return aws
    supervisor.classifier.classify = AsyncMock(
        return_value=ClassifierResult(selected_agent="aws", confidence=0.95)
    )
    # Mock the agent's process_request
    supervisor.agents["aws"].process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant", content="test response",
            timestamp="2026-01-01T00:00:00", agent_id="aws"
        )
    )
    # Mock storage
    with patch("src.supervisor.agent.storage") as mock_storage:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])
        mock_storage.save_chat_message = AsyncMock()
        mock_storage.fetch_chat = AsyncMock(return_value=[])

        result = await supervisor.process_request("list ec2", "user1", "sess1")

    assert result["agent"] == "aws"
    assert result["response"] == "test response"


@pytest.mark.asyncio
async def test_process_request_unknown_agent_fallback():
    """Classifier returns 'unknown' → supervisor returns rephrase message."""
    mock_registry = _make_mock_registry()

    with patch("src.supervisor.agent.create_adapters", return_value=[]):
        from src.supervisor.agent import SupervisorAgent
        supervisor = SupervisorAgent(mock_registry)

    supervisor.classifier.classify = AsyncMock(
        return_value=ClassifierResult(selected_agent="unknown", confidence=0.2)
    )

    with patch("src.supervisor.agent.storage") as mock_storage:
        mock_storage.fetch_all_chats = AsyncMock(return_value=[])

        result = await supervisor.process_request("gibberish", "user1", "sess1")

    assert result["agent"] == "supervisor"
    assert "rephrase" in result["response"].lower()
