"""Regression proof for spec 35 T1 — the two defect classes that shipped to the
real cluster (F-001, F-002/F-003) are now caught structurally.

Each test mocks Bedrock to return a LITERAL reproduction of the exact text
observed live before the fix (F-001: aws agent hallucinated `<use_mcp_tool>`
XML; F-002: finops surfaced a raw boto3 AccessDeniedException string; F-003:
kubernetes agent surfaced a raw MCP TaskGroup exception). Before
`ResponseQualityGuard` existed, neither `OutputFilter` (PII/secrets — doesn't
pattern-match tool syntax or exception strings) nor `CanaryGuard` (only
matches injected random tokens) would have caught any of these — the
unfiltered text would have reached the user as `ConversationMessage.content`.
This suite proves `GenericAgent.process_request` now raises
`GuardrailBlockedError` for all three instead.

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import pytest
from unittest.mock import patch, AsyncMock

from src.core.adapters import DatasourceAdapter
from src.core.agent_config import AgentConfig
from src.core.generic_agent import GenericAgent
from src.core.guardrail import GuardrailBlockedError


def _make_config(name: str) -> AgentConfig:
    return AgentConfig(
        name=name, description="Test agent", domain="testing",
        capabilities=["test"], datasources=[],
    )


class FakeAdapter(DatasourceAdapter):
    def __init__(self, response: str = "fake infra data"):
        self._response = response

    async def _collect(self, query: str) -> str:
        return self._response


@pytest.mark.asyncio
class TestF001ToolScaffoldingRegression:
    """aws agent (2026-07-03 homologation): responded with raw
    `<use_mcp_tool>` XML instead of a real answer, because the prompt
    referenced an MCP capability that wasn't wired (see specs/BACKLOG.md
    F-001). Reproducing the exact observed shape here."""

    @patch("src.core.generic_agent.bedrock")
    async def test_use_mcp_tool_xml_now_blocked(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(
            return_value=(
                "<use_mcp_tool>\n"
                "<server_name>aws-mcp-server</server_name>\n"
                "<tool_name>describe_instances</tool_name>\n"
                "</use_mcp_tool>"
            )
        )

        agent = GenericAgent(
            config=_make_config("aws"),
            prompt="You are an AWS specialist.",
            adapters=[FakeAdapter("[ec2] 5 instances (3 running)")],
        )

        with pytest.raises(GuardrailBlockedError) as exc_info:
            await agent.process_request(
                input_text="how many EC2 instances are running?",
                user_id="u", session_id="s", chat_history=[],
            )
        assert "quality:tool_scaffolding" in exc_info.value.categories


@pytest.mark.asyncio
class TestF002RawAdapterErrorRegression:
    """finops agent (2026-07-01 homologation): surfaced a raw boto3
    AccessDeniedException in every answer because the athena datasource's
    IRSA lacked permission (see specs/BACKLOG.md F-002). Reproducing the
    exact observed shape here."""

    @patch("src.core.generic_agent.bedrock")
    async def test_boto3_access_denied_string_now_blocked(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(
            return_value=(
                "I attempted to query the cost data but encountered: "
                "An error occurred (AccessDeniedException) when calling the "
                "StartQueryExecution operation: User is not authorized"
            )
        )

        agent = GenericAgent(
            config=_make_config("finops"),
            prompt="You are a FinOps specialist.",
            adapters=[FakeAdapter("[ce] last 30d cost: $191225.98")],
        )

        with pytest.raises(GuardrailBlockedError) as exc_info:
            await agent.process_request(
                input_text="what's my cost trend?",
                user_id="u", session_id="s", chat_history=[],
            )
        assert "quality:raw_boto3_error_string" in exc_info.value.categories


@pytest.mark.asyncio
class TestF003McpTaskGroupRegression:
    """kubernetes agent (2026-07-13, this session): surfaced a raw MCP SSE
    transport exception when the external k8s-mcp hostname 404'd (see
    specs/BACKLOG.md F-003), wrapped in a fabricated-sounding diagnostic
    report. Reproducing the exact observed adapter-error shape here."""

    @patch("src.core.generic_agent.bedrock")
    async def test_mcp_taskgroup_error_now_blocked(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(
            return_value=(
                "Based on [mcp:k8s-mcp] error: unhandled errors in a TaskGroup "
                "(1 sub-exception), here is my diagnostic analysis..."
            )
        )

        agent = GenericAgent(
            config=_make_config("kubernetes"),
            prompt="You are a Kubernetes specialist.",
            adapters=[FakeAdapter("[mcp:k8s-mcp] error: unhandled errors in a TaskGroup (1 sub-exception)")],
        )

        with pytest.raises(GuardrailBlockedError) as exc_info:
            await agent.process_request(
                input_text="what's deployed in the devops namespace?",
                user_id="u", session_id="s", chat_history=[],
            )
        categories = exc_info.value.categories
        assert "quality:raw_adapter_error" in categories
        assert "quality:raw_taskgroup_exception" in categories


@pytest.mark.asyncio
class TestCleanResponsesStillPass:
    """Sanity check: a normal, well-formed answer for each of the three
    agents above is NOT affected by the new guard."""

    @pytest.mark.parametrize("agent_name,answer", [
        ("aws", "You have 5 EC2 instances, 3 currently running."),
        ("finops", "Your last 30 days of AWS spend totals $191,225.98."),
        ("kubernetes", "The devops namespace has 4 deployments and 12 pods, all healthy."),
    ])
    @patch("src.core.generic_agent.bedrock")
    async def test_clean_answer_returns_normally(self, mock_bedrock, agent_name, answer):
        mock_bedrock.invoke = AsyncMock(return_value=answer)

        agent = GenericAgent(
            config=_make_config(agent_name),
            prompt="You are a specialist.",
            adapters=[FakeAdapter("some clean infra data")],
        )

        result = await agent.process_request(
            input_text="a normal question", user_id="u", session_id="s", chat_history=[],
        )
        assert result.content == answer


@pytest.mark.asyncio
class TestPreFixVsPostFixSameCallPath:
    """T11 review fix (2026-07-15): the classes above prove detection fires,
    but "fails on pre-fix code, passes after" up to now was only demonstrated
    by combining this file with TestScanDisabled in test_response_quality.py
    (which drives the guard directly, not through process_request). This
    test proves both halves of the claim through the SAME
    GenericAgent.process_request call path, toggling only the
    response_quality_enabled setting — i.e. literally "disable the T1 fix,
    watch the F-001 defect reach the user; re-enable it, watch it get
    blocked" in one place."""

    @patch("src.core.generic_agent.bedrock")
    async def test_defect_passes_when_disabled_blocked_when_enabled(self, mock_bedrock, monkeypatch):
        defect_text = (
            "<use_mcp_tool>\n"
            "<server_name>aws-mcp-server</server_name>\n"
            "<tool_name>describe_instances</tool_name>\n"
            "</use_mcp_tool>"
        )
        mock_bedrock.invoke = AsyncMock(return_value=defect_text)
        agent = GenericAgent(
            config=_make_config("aws"),
            prompt="You are an AWS specialist.",
            adapters=[FakeAdapter("[ec2] 5 instances (3 running)")],
        )

        # Pre-fix (guard disabled): the defect reaches the user unfiltered —
        # this IS what shipped to the real cluster before T1 existed.
        monkeypatch.setattr("src.core.config.settings.response_quality_enabled", False)
        result = await agent.process_request(
            input_text="how many EC2 instances are running?",
            user_id="u", session_id="s-disabled", chat_history=[],
        )
        assert result.content == defect_text

        # Post-fix (guard enabled, the default): the identical defect is
        # now blocked through the identical call path.
        monkeypatch.setattr("src.core.config.settings.response_quality_enabled", True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            await agent.process_request(
                input_text="how many EC2 instances are running?",
                user_id="u", session_id="s-enabled", chat_history=[],
            )
        assert "quality:tool_scaffolding" in exc_info.value.categories
