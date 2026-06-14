"""Tests for GenericAgent public contract.

Tests what the agent SHOULD do per spec, not implementation details.
"""
import pytest
from unittest.mock import patch, AsyncMock

from src.core.adapters import DatasourceAdapter
from src.core.agent_config import AgentConfig
from src.core.generic_agent import GenericAgent
from src.core.state_store import ConversationMessage


# --- Helpers ---

def _make_config(name: str = "test-agent") -> AgentConfig:
    return AgentConfig(
        name=name,
        description="Test agent",
        domain="testing",
        capabilities=["test"],
        datasources=[],
    )


class FakeAdapter(DatasourceAdapter):
    """Adapter that returns fixed text."""

    def __init__(self, response: str = "fake data"):
        self._response = response

    async def collect(self, query: str) -> str:
        return self._response


class FailingAdapter(DatasourceAdapter):
    """Adapter that always raises."""

    async def collect(self, query: str) -> str:
        raise RuntimeError("datasource unavailable")


# --- Tests ---

@pytest.mark.asyncio
class TestProcessRequestReturnsConversationMessage:
    @patch("src.core.generic_agent.bedrock")
    async def test_process_request_returns_conversation_message(self, mock_bedrock):
        """Invoke returns a ConversationMessage with role, content, timestamp, agent_id."""
        mock_bedrock.invoke = AsyncMock(return_value="Hello from LLM")

        agent = GenericAgent(
            config=_make_config(),
            prompt="You are a test agent.",
            adapters=[],
        )

        result = await agent.process_request(
            input_text="What is the status?",
            user_id="user-1",
            session_id="sess-1",
            chat_history=[],
        )

        assert isinstance(result, ConversationMessage)
        assert result.role == "assistant"
        assert result.content == "Hello from LLM"
        assert result.timestamp  # non-empty ISO timestamp
        assert result.agent_id == "test-agent"


@pytest.mark.asyncio
class TestEmptyInputRaisesValueError:
    @patch("src.core.generic_agent.bedrock")
    async def test_empty_input_raises_value_error(self, mock_bedrock):
        """Empty string input → ValueError."""
        agent = GenericAgent(config=_make_config(), prompt="p", adapters=[])

        with pytest.raises(ValueError, match="empty"):
            await agent.process_request(
                input_text="",
                user_id="u",
                session_id="s",
                chat_history=[],
            )

        mock_bedrock.invoke.assert_not_called()


@pytest.mark.asyncio
class TestTooLongInputRaisesValueError:
    @patch("src.core.generic_agent.bedrock")
    async def test_too_long_input_raises_value_error(self, mock_bedrock):
        """10001 chars → ValueError."""
        agent = GenericAgent(config=_make_config(), prompt="p", adapters=[])

        with pytest.raises(ValueError, match="too long"):
            await agent.process_request(
                input_text="x" * 10001,
                user_id="u",
                session_id="s",
                chat_history=[],
            )

        mock_bedrock.invoke.assert_not_called()


@pytest.mark.asyncio
class TestAdaptersCalledWithQuery:
    @patch("src.core.generic_agent.bedrock")
    async def test_adapters_called_with_query(self, mock_bedrock):
        """Mock adapters verify collect() was called."""
        mock_bedrock.invoke = AsyncMock(return_value="response")

        adapter = AsyncMock(spec=DatasourceAdapter)
        adapter.collect.return_value = "adapter data"

        agent = GenericAgent(
            config=_make_config(),
            prompt="p",
            adapters=[adapter],
        )

        await agent.process_request(
            input_text="show me pods",
            user_id="u",
            session_id="s",
            chat_history=[],
        )

        adapter.collect.assert_called_once_with("show me pods")


@pytest.mark.asyncio
class TestAdapterFailureDoesNotCrash:
    @patch("src.core.generic_agent.bedrock")
    async def test_adapter_failure_does_not_crash(self, mock_bedrock):
        """One adapter raises, agent still returns (graceful degradation)."""
        mock_bedrock.invoke = AsyncMock(return_value="still works")

        agent = GenericAgent(
            config=_make_config(),
            prompt="p",
            adapters=[FailingAdapter(), FakeAdapter("good data")],
        )

        result = await agent.process_request(
            input_text="query",
            user_id="u",
            session_id="s",
            chat_history=[],
        )

        assert result.content == "still works"
        assert result.role == "assistant"


@pytest.mark.asyncio
class TestHistoryFormattedInContext:
    @patch("src.core.generic_agent.bedrock")
    async def test_history_formatted_in_context(self, mock_bedrock):
        """History messages are included in the context sent to bedrock."""
        mock_bedrock.invoke = AsyncMock(return_value="answer")

        history = [
            ConversationMessage(role="user", content="prior question", timestamp="t1"),
            ConversationMessage(role="assistant", content="prior answer", timestamp="t2"),
        ]

        agent = GenericAgent(config=_make_config(), prompt="p", adapters=[])

        await agent.process_request(
            input_text="follow up",
            user_id="u",
            session_id="s",
            chat_history=history,
        )

        # Verify bedrock.invoke was called with messages containing history
        call_args = mock_bedrock.invoke.call_args
        messages_content = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][0]
        context_text = str(messages_content)

        assert "prior question" in context_text
        assert "prior answer" in context_text
