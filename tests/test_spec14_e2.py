"""Spec 14 Phase-6 follow-up finding E2 (independent author).

E2 — RCA evidence-collection fan-out (run_investigation) books its Bedrock
    spend under a derived session_id (f"{session_id}-inv-{state.id[:8]}"),
    a fresh bucket per investigation that budget_tracker.check_budget() never
    reads at the supervisor entrypoint — evidence-collection tokens largely
    escape the session budget cap.

Fix: a distinct `budget_session_id` param threads through
GenericAgent.process_request -> BedrockClient.invoke/_invoke_sync ->
budget_tracker.record_usage, decoupled from the session_id used for
history/audit isolation. run_investigation's fan-out passes the parent's
REAL session_id as budget_session_id so evidence-collection spend counts
against the cap check_budget() enforces, while session_id itself keeps the
"-inv-" derived form for log/audit correlation.
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.agent_config import AgentConfig
from src.core.generic_agent import GenericAgent
from src.core.state_store import ConversationMessage


def _make_config(name: str = "test-agent") -> AgentConfig:
    return AgentConfig(
        name=name,
        description="Test agent",
        domain="testing",
        capabilities=["test"],
        datasources=[],
    )


def _make_agent(response_content: str):
    agent = MagicMock()
    agent.process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant",
            content=response_content,
            timestamp="2026-07-13T10:00:00Z",
        )
    )
    return agent


_EVIDENCE_JSON = json.dumps([
    {"signal_type": "metric", "timestamp": "2026-07-13T10:00:00Z",
     "strength": "forte", "summary": "CPU 95%"},
])
_RCA_JSON = json.dumps({
    "hypothesis": "Memory leak caused OOM",
    "reasoning": "consistent",
    "contradicting_evidence_indices": [],
    "prevention": ["add memory alert"],
})


# --------------------------------------------------------------------------
# GenericAgent forwards budget_session_id to bedrock.invoke
# --------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGenericAgentForwardsBudgetSessionId:
    @patch("src.core.generic_agent.bedrock")
    async def test_budget_session_id_forwarded_when_given(self, mock_bedrock):
        mock_bedrock.invoke = AsyncMock(return_value="ok")
        agent = GenericAgent(config=_make_config(), prompt="sys", adapters=[])

        await agent.process_request(
            input_text="collect evidence",
            user_id="u-1",
            session_id="s-1-inv-abcd1234",
            chat_history=[],
            budget_session_id="s-1",
        )

        kw = mock_bedrock.invoke.call_args.kwargs
        assert kw["session_id"] == "s-1-inv-abcd1234"
        assert kw["budget_session_id"] == "s-1"

    @patch("src.core.generic_agent.bedrock")
    async def test_budget_session_id_defaults_to_none_when_omitted(self, mock_bedrock):
        """Ordinary (non-investigation) calls don't pass budget_session_id —
        bedrock.invoke must fall back to session_id (backward compatible)."""
        mock_bedrock.invoke = AsyncMock(return_value="ok")
        agent = GenericAgent(config=_make_config(), prompt="sys", adapters=[])

        await agent.process_request(
            input_text="normal query",
            user_id="u-1",
            session_id="s-1",
            chat_history=[],
        )

        kw = mock_bedrock.invoke.call_args.kwargs
        assert kw["session_id"] == "s-1"
        assert kw["budget_session_id"] is None


# --------------------------------------------------------------------------
# BedrockClient charges budget_session_id (falling back to session_id)
# --------------------------------------------------------------------------

@pytest.fixture
def mock_boto3_client():
    with patch("src.core.bedrock.boto3.client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_record_usage_charged_to_budget_session_id_when_provided(mock_boto3_client):
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":100,"output_tokens":50}}'
        ))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.budget_tracker") as mock_tracker:
        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            session_id="s-1-inv-abcd1234",
            budget_session_id="s-1",
        )
        mock_tracker.record_usage.assert_called_once_with("s-1", 100, 50)


@pytest.mark.asyncio
async def test_record_usage_falls_back_to_session_id_when_budget_session_id_absent(mock_boto3_client):
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":10,"output_tokens":5}}'
        ))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.budget_tracker") as mock_tracker:
        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            session_id="s-1",
        )
        mock_tracker.record_usage.assert_called_once_with("s-1", 10, 5)


# --------------------------------------------------------------------------
# End-to-end: run_investigation's fan-out charges the PARENT session budget
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_investigation_fanout_charges_parent_session_budget():
    """The evidence-collection fan-out uses a derived session_id (log/audit
    isolation) but must pass budget_session_id=<the real parent session_id>
    so its spend counts against the cap check_budget() enforces."""
    from src.supervisor.investigation import run_investigation

    agent = _make_agent(_EVIDENCE_JSON)
    with patch("src.supervisor.investigation.inject_similar_cases",
               new_callable=AsyncMock, return_value=""), \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_RCA_JSON)
        await run_investigation(
            symptom="latency spike",
            agents={"obs": agent},
            user_id="u-real",
            session_id="s-real",
        )

    kw = agent.process_request.call_args.kwargs
    assert kw["session_id"] != "s-real"          # still isolated for audit/log
    assert kw["session_id"].startswith("s-real-inv-")
    assert kw["budget_session_id"] == "s-real"   # but budget hits the real session
