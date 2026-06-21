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

    async def _collect(self, query: str) -> str:
        return self._response


class FailingAdapter(DatasourceAdapter):
    """Adapter that always raises."""

    async def _collect(self, query: str) -> str:
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
class TestCollectDurationMetric:
    """spec 10: data-collection latency is recorded per agent."""

    @patch("src.core.generic_agent.collect_duration")
    @patch("src.core.generic_agent.bedrock")
    async def test_collect_duration_recorded_with_agent_id(self, mock_bedrock, mock_collect_dur):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        agent = GenericAgent(
            config=_make_config(name="aws"),
            prompt="p",
            adapters=[FakeAdapter("data")],
        )

        await agent.process_request(
            input_text="q", user_id="u", session_id="s", chat_history=[],
        )

        assert mock_collect_dur.record.call_count == 1
        value, labels = mock_collect_dur.record.call_args.args
        assert value >= 0
        assert labels == {"agent_id": "aws"}

    @patch("src.core.generic_agent.collect_duration")
    @patch("src.core.generic_agent.bedrock")
    async def test_collect_duration_recorded_even_with_no_adapters(self, mock_bedrock, mock_collect_dur):
        """Baseline: emitted once even when the agent has zero datasources."""
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        agent = GenericAgent(config=_make_config(name="finops"), prompt="p", adapters=[])

        await agent.process_request(
            input_text="q", user_id="u", session_id="s", chat_history=[],
        )

        mock_collect_dur.record.assert_called_once()
        assert mock_collect_dur.record.call_args.args[1] == {"agent_id": "finops"}


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


@pytest.mark.asyncio
class TestSkillInjection:
    """Lazy skill injection into the system prompt (spec 26)."""

    @patch("src.core.generic_agent.bedrock")
    async def test_matching_skill_injected_into_system_prompt(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        from src.core.skills import Skill

        class FakeRegistry:
            def select(self, allowed, query):
                return [Skill(name="oomkill", body="Check memory limits.")]

            def render(self, skills):
                from src.core.skills import SkillRegistry
                return SkillRegistry.render(skills)

        cfg = _make_config()
        cfg.skills = ["oomkill"]
        agent = GenericAgent(config=cfg, prompt="BASE PROMPT", adapters=[], skill_registry=FakeRegistry())

        await agent.process_request(
            input_text="pod oomkilled?", user_id="u", session_id="s", chat_history=[]
        )

        call = mock_bedrock.invoke.call_args
        system_prompt = call.kwargs.get("system_prompt")
        assert "BASE PROMPT" in system_prompt
        assert "<skills>" in system_prompt
        assert "Check memory limits." in system_prompt

    @patch("src.core.generic_agent.bedrock")
    async def test_no_matching_skill_keeps_plain_prompt(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        class EmptyRegistry:
            def select(self, allowed, query):
                return []

            def render(self, skills):
                return ""

        cfg = _make_config()
        cfg.skills = ["oomkill"]
        agent = GenericAgent(config=cfg, prompt="BASE PROMPT", adapters=[], skill_registry=EmptyRegistry())

        await agent.process_request(
            input_text="unrelated", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs.get("system_prompt")
        assert system_prompt == "BASE PROMPT"
        assert "<skills>" not in system_prompt

    @patch("src.core.generic_agent.bedrock")
    async def test_no_registry_keeps_plain_prompt(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        agent = GenericAgent(config=_make_config(), prompt="BASE PROMPT", adapters=[])
        await agent.process_request(
            input_text="anything", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs.get("system_prompt")
        assert system_prompt == "BASE PROMPT"
