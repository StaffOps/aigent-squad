"""Tests for src/core/agent_tools.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.agent_tools import AgentTools, AgentToolError, _call_depth
from src.core.state_store import ConversationMessage


def _mock_supervisor(agent_names):
    supervisor = MagicMock()
    supervisor.agents = {}
    for name in agent_names:
        agent = MagicMock()
        agent.process_request = AsyncMock(return_value=ConversationMessage(
            role="assistant", content=f"{name} response",
            timestamp="2026-01-01T00:00:00", agent_id=name
        ))
        supervisor.agents[name] = agent
    return supervisor


@pytest.mark.asyncio
async def test_agent_tools_ask_calls_target():
    """ask('aws', 'query') calls target's process_request, returns content."""
    supervisor = _mock_supervisor(["aws"])
    tools = AgentTools(supervisor)

    result = await tools.ask("aws", "what instances?")

    assert result == "aws response"
    supervisor.agents["aws"].process_request.assert_called_once()


@pytest.mark.asyncio
async def test_agent_tools_unknown_agent_raises():
    """ask('nonexistent') → AgentToolError."""
    supervisor = _mock_supervisor(["aws"])
    tools = AgentTools(supervisor)

    with pytest.raises(AgentToolError, match="Unknown agent"):
        await tools.ask("nonexistent", "query")


@pytest.mark.asyncio
async def test_agent_tools_depth_guard():
    """Already at depth=1 → ask() raises AgentToolError about max depth."""
    supervisor = _mock_supervisor(["aws"])
    tools = AgentTools(supervisor)

    token = _call_depth.set(1)
    try:
        with pytest.raises(AgentToolError, match="Max agent-as-tools depth"):
            await tools.ask("aws", "query")
    finally:
        _call_depth.reset(token)
