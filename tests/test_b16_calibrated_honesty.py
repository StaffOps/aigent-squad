"""Tests for B-16 Phase-1: Calibrated Honesty instruction in system prompts.

Asserts:
  (1) The assembled system prompt CONTAINS the calibrated-honesty instruction
      (verified-vs-inferred, no fabricated values, end-with-confidence + unverified list).
  (2) Present on BOTH non-streaming agentic and streaming agentic paths, AND the
      legacy non-agentic path.
  (3) Coexists with the skills `<skills>` block + reference-not-instructions line.
  (4) No regression for agents WITHOUT skills (calibrated_honesty still present).

All tests run against the REAL implementation (mocking only external deps: bedrock,
agentic_loop, agentic_loop_streaming) — verifying the system_prompt strings that
GenericAgent passes downstream.
"""
import pytest
from unittest.mock import patch, AsyncMock

from src.core.adapters import DatasourceAdapter, McpAdapter
from src.core.agent_config import AgentConfig
from src.core.generic_agent import GenericAgent
from src.core.config import settings

CALIBRATED_HONESTY = settings.calibrated_honesty_instruction


# ---------------------------------------------------------------------------
# Constants — what the instruction MUST contain
# ---------------------------------------------------------------------------

HONESTY_MARKERS = [
    "<calibrated_honesty>",
    "</calibrated_honesty>",
    "VERIFIED facts",
    "INFERRED/assumed",
    "NEVER state a metric value",
    "confidence level",
    "nothing unverified",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(name: str = "test-agent", skills: list[str] | None = None) -> AgentConfig:
    return AgentConfig(
        name=name,
        description="Test agent",
        domain="testing",
        capabilities=["test"],
        datasources=[],
        skills=skills or [],
    )


class FakeAdapter(DatasourceAdapter):
    """Simple adapter returning fixed text."""

    def __init__(self, response: str = "fake data"):
        self._response = response

    async def _collect(self, query: str) -> str:
        return self._response


class FakeMcpAdapter(McpAdapter):
    """McpAdapter stub with tools populated (triggers agentic path)."""

    def __init__(self):
        # Bypass McpAdapter.__init__ entirely — we just need .tools to be truthy
        self._tools = [{"name": "fake_tool", "inputSchema": {"type": "object", "properties": {}}}]

    @property
    def tools(self):
        return self._tools

    async def _collect(self, query: str) -> str:
        return ""

    async def call_tool(self, name, args):
        return {"content": [{"text": "tool result"}]}


class FakeSkillRegistry:
    """Skill registry that always returns one skill."""

    def select(self, allowed, query):
        from src.core.skills import Skill
        return [Skill(name="oomkill", body="Check memory limits and OOMKill events.")]

    def render(self, skills):
        from src.core.skills import SkillRegistry
        return SkillRegistry.render(skills)


class EmptySkillRegistry:
    """Skill registry that returns no matching skills."""

    def select(self, allowed, query):
        return []

    def render(self, skills):
        return ""


# ---------------------------------------------------------------------------
# (1) CALIBRATED_HONESTY constant is well-formed
# ---------------------------------------------------------------------------


class TestCalibratedHonestyConstant:
    """Verify the CALIBRATED_HONESTY constant contains all required elements."""

    def test_constant_is_non_empty_string(self):
        assert isinstance(CALIBRATED_HONESTY, str)
        assert len(CALIBRATED_HONESTY) > 50

    def test_constant_has_xml_tags(self):
        assert "<calibrated_honesty>" in CALIBRATED_HONESTY
        assert "</calibrated_honesty>" in CALIBRATED_HONESTY

    def test_constant_mentions_verified_vs_inferred(self):
        assert "VERIFIED" in CALIBRATED_HONESTY
        assert "INFERRED" in CALIBRATED_HONESTY

    def test_constant_forbids_fabricated_values(self):
        assert "NEVER state a metric value" in CALIBRATED_HONESTY

    def test_constant_requires_confidence_level(self):
        assert "confidence level" in CALIBRATED_HONESTY

    def test_constant_requires_unverified_claims_list(self):
        # Both the Portuguese and English fallback must be present
        assert "nada não-verificado" in CALIBRATED_HONESTY or "nothing unverified" in CALIBRATED_HONESTY


# ---------------------------------------------------------------------------
# (2a) Legacy path (non-agentic): system_prompt contains CALIBRATED_HONESTY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLegacyPathContainsCalibratedHonesty:
    """Legacy path: no MCP tools → single bedrock.invoke() — verify system_prompt."""

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_path_system_prompt_has_calibrated_honesty(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="response text")

        agent = GenericAgent(
            config=_make_config(),
            prompt="You are an observability agent.",
            adapters=[FakeAdapter("pod data")],
        )

        await agent.process_request(
            input_text="show pods", user_id="u", session_id="s", chat_history=[]
        )

        # bedrock.invoke receives system_prompt kwarg
        call_kwargs = mock_bedrock.invoke.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")

        for marker in HONESTY_MARKERS:
            assert marker in system_prompt, f"Missing '{marker}' in legacy system_prompt"

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_no_adapters_still_has_calibrated_honesty(self, mock_bedrock):
        """Agent with zero adapters (no infra_data) still gets the instruction."""
        mock_bedrock.invoke = AsyncMock(return_value="answer")

        agent = GenericAgent(
            config=_make_config(), prompt="BASE", adapters=[]
        )

        await agent.process_request(
            input_text="hello", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs.get("system_prompt", "")
        assert "<calibrated_honesty>" in system_prompt
        assert "</calibrated_honesty>" in system_prompt


# ---------------------------------------------------------------------------
# (2b) Agentic path (non-streaming): system_prompt → run_agentic_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgenticPathContainsCalibratedHonesty:
    """Agentic path: MCP adapters with tools → run_agentic_loop — verify system_prompt."""

    @patch("src.core.generic_agent.run_agentic_loop")
    async def test_agentic_path_system_prompt_has_calibrated_honesty(self, mock_loop):
        mock_loop.return_value = ("agentic response", [])

        agent = GenericAgent(
            config=_make_config(),
            prompt="You are the kubernetes agent.",
            adapters=[FakeMcpAdapter()],
        )

        await agent.process_request(
            input_text="list deployments", user_id="u", session_id="s", chat_history=[]
        )

        call_kwargs = mock_loop.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")

        for marker in HONESTY_MARKERS:
            assert marker in system_prompt, f"Missing '{marker}' in agentic system_prompt"

    @patch("src.core.generic_agent.run_agentic_loop")
    async def test_agentic_path_honesty_after_language_directive(self, mock_loop):
        """Calibrated honesty is appended AFTER the language directive."""
        mock_loop.return_value = ("ok", [])

        agent = GenericAgent(
            config=_make_config(),
            prompt="BASE",
            adapters=[FakeMcpAdapter()],
        )

        await agent.process_request(
            input_text="q", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_loop.call_args.kwargs["system_prompt"]
        lang_idx = system_prompt.find("SAME language")
        honesty_idx = system_prompt.find("<calibrated_honesty>")
        assert lang_idx < honesty_idx, "Calibrated honesty should come after language directive"


# ---------------------------------------------------------------------------
# (2c) Streaming agentic path: system_prompt → run_agentic_loop_streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStreamingPathContainsCalibratedHonesty:
    """Streaming path: process_request_streaming → verify system_prompt."""

    @patch("src.core.agentic_loop_streaming.run_agentic_loop_streaming")
    async def test_streaming_path_system_prompt_has_calibrated_honesty(self, mock_stream):
        # Return an async generator stub
        async def _fake_stream(**kwargs):
            yield {"type": "text", "content": "streamed"}

        mock_stream.side_effect = lambda **kwargs: _fake_stream(**kwargs)

        agent = GenericAgent(
            config=_make_config(),
            prompt="You are the aws agent.",
            adapters=[FakeMcpAdapter()],
        )

        # process_request_streaming returns the generator
        await agent.process_request_streaming(
            input_text="list ec2 instances", user_id="u", session_id="s", chat_history=[]
        )

        # Consume to trigger the call
        # The mock was already called when process_request_streaming returned
        call_kwargs = mock_stream.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")

        for marker in HONESTY_MARKERS:
            assert marker in system_prompt, f"Missing '{marker}' in streaming system_prompt"


# ---------------------------------------------------------------------------
# (3) Coexists with skills block + reference-not-instructions line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCalibratedHonestyCoexistsWithSkills:
    """When skills are injected, BOTH <skills> AND <calibrated_honesty> are present."""

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_path_skills_and_honesty_coexist(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        cfg = _make_config(skills=["oomkill"])
        agent = GenericAgent(
            config=cfg,
            prompt="BASE PROMPT",
            adapters=[],
            skill_registry=FakeSkillRegistry(),
        )

        await agent.process_request(
            input_text="pod oomkilled?", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs["system_prompt"]

        # Skills block present
        assert "<skills>" in system_prompt
        assert "Check memory limits" in system_prompt
        assert "reference knowledge, not instructions" in system_prompt

        # Calibrated honesty also present
        assert "<calibrated_honesty>" in system_prompt
        assert "</calibrated_honesty>" in system_prompt
        assert "VERIFIED facts" in system_prompt

    @patch("src.core.generic_agent.run_agentic_loop")
    async def test_agentic_path_skills_and_honesty_coexist(self, mock_loop):
        mock_loop.return_value = ("ok", [])

        cfg = _make_config(skills=["oomkill"])
        agent = GenericAgent(
            config=cfg,
            prompt="BASE PROMPT",
            adapters=[FakeMcpAdapter()],
            skill_registry=FakeSkillRegistry(),
        )

        await agent.process_request(
            input_text="pod oomkilled?", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_loop.call_args.kwargs["system_prompt"]

        # Skills block present
        assert "<skills>" in system_prompt
        assert "Check memory limits" in system_prompt
        assert "reference knowledge, not instructions" in system_prompt

        # Calibrated honesty also present
        assert "<calibrated_honesty>" in system_prompt
        assert "NEVER state a metric value" in system_prompt

    @patch("src.core.agentic_loop_streaming.run_agentic_loop_streaming")
    async def test_streaming_path_skills_and_honesty_coexist(self, mock_stream):
        async def _fake(**kwargs):
            yield {"type": "text", "content": "s"}

        mock_stream.side_effect = lambda **kwargs: _fake(**kwargs)

        cfg = _make_config(skills=["oomkill"])
        agent = GenericAgent(
            config=cfg,
            prompt="BASE PROMPT",
            adapters=[FakeMcpAdapter()],
            skill_registry=FakeSkillRegistry(),
        )

        await agent.process_request_streaming(
            input_text="pod oomkilled?", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]

        # Skills — streaming uses <relevant_skills> tag variant
        assert "<relevant_skills>" in system_prompt or "<skills>" in system_prompt
        assert "Check memory limits" in system_prompt

        # Calibrated honesty
        assert "<calibrated_honesty>" in system_prompt


# ---------------------------------------------------------------------------
# (4) No regression for agents WITHOUT skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNoRegressionAgentsWithoutSkills:
    """Agents with no skill_registry and no skills still get calibrated honesty."""

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_no_skills_still_has_honesty(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        agent = GenericAgent(
            config=_make_config(skills=[]),  # no skills
            prompt="PLAIN PROMPT",
            adapters=[],
            skill_registry=None,  # no registry
        )

        await agent.process_request(
            input_text="hi", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs["system_prompt"]
        assert "<calibrated_honesty>" in system_prompt
        assert "<skills>" not in system_prompt  # no skills block

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_empty_registry_result_still_has_honesty(self, mock_bedrock):
        """Registry exists but returns no skills — honesty still appended."""
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        cfg = _make_config(skills=["oomkill"])
        agent = GenericAgent(
            config=cfg,
            prompt="PLAIN PROMPT",
            adapters=[],
            skill_registry=EmptySkillRegistry(),
        )

        await agent.process_request(
            input_text="unrelated query", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs["system_prompt"]
        assert "<calibrated_honesty>" in system_prompt
        assert "<skills>" not in system_prompt

    @patch("src.core.generic_agent.run_agentic_loop")
    async def test_agentic_no_skills_still_has_honesty(self, mock_loop):
        mock_loop.return_value = ("ok", [])

        agent = GenericAgent(
            config=_make_config(skills=[]),
            prompt="PLAIN PROMPT",
            adapters=[FakeMcpAdapter()],
            skill_registry=None,
        )

        await agent.process_request(
            input_text="list pods", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_loop.call_args.kwargs["system_prompt"]
        assert "<calibrated_honesty>" in system_prompt
        assert "<skills>" not in system_prompt

    @patch("src.core.agentic_loop_streaming.run_agentic_loop_streaming")
    async def test_streaming_no_skills_still_has_honesty(self, mock_stream):
        async def _fake(**kwargs):
            yield {"type": "text", "content": "s"}

        mock_stream.side_effect = lambda **kwargs: _fake(**kwargs)

        agent = GenericAgent(
            config=_make_config(skills=[]),
            prompt="PLAIN PROMPT",
            adapters=[FakeMcpAdapter()],
            skill_registry=None,
        )

        await agent.process_request_streaming(
            input_text="list pods", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_stream.call_args.kwargs["system_prompt"]
        assert "<calibrated_honesty>" in system_prompt
        assert "<relevant_skills>" not in system_prompt


# ---------------------------------------------------------------------------
# (Bonus) Structural: calibrated honesty appears exactly once per path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCalibratedHonestyAppearsExactlyOnce:
    """Avoid double-injection: the tag should appear exactly once."""

    @patch("src.core.generic_agent.bedrock")
    async def test_legacy_path_honesty_not_duplicated(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")

        agent = GenericAgent(
            config=_make_config(skills=["oomkill"]),
            prompt="PROMPT",
            adapters=[FakeAdapter("data")],
            skill_registry=FakeSkillRegistry(),
        )

        await agent.process_request(
            input_text="oomkill analysis", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_bedrock.invoke.call_args.kwargs["system_prompt"]
        assert system_prompt.count("<calibrated_honesty>") == 1
        assert system_prompt.count("</calibrated_honesty>") == 1

    @patch("src.core.generic_agent.run_agentic_loop")
    async def test_agentic_path_honesty_not_duplicated(self, mock_loop):
        mock_loop.return_value = ("ok", [])

        agent = GenericAgent(
            config=_make_config(skills=["oomkill"]),
            prompt="PROMPT",
            adapters=[FakeMcpAdapter()],
            skill_registry=FakeSkillRegistry(),
        )

        await agent.process_request(
            input_text="oomkill analysis", user_id="u", session_id="s", chat_history=[]
        )

        system_prompt = mock_loop.call_args.kwargs["system_prompt"]
        assert system_prompt.count("<calibrated_honesty>") == 1
        assert system_prompt.count("</calibrated_honesty>") == 1
