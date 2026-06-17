"""Tests for src.supervisor.investigation — run_investigation orchestrator."""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.investigation import RCAResult
from src.core.state_store import ConversationMessage


def _make_agent(response_content: str):
    agent = MagicMock()
    agent.process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant",
            content=response_content,
            timestamp="2026-06-14T10:00:00Z",
        )
    )
    return agent


def _valid_evidence_json():
    return json.dumps([
        {"signal_type": "metric", "timestamp": "2026-06-14T10:00:00Z", "strength": "forte", "summary": "CPU 95%"},
        {"signal_type": "log", "timestamp": "2026-06-14T10:01:00Z", "strength": "media", "summary": "OOM event"},
    ])


def _rca_json():
    return json.dumps({
        "hypothesis": "Memory leak caused OOM",
        "reasoning": "evidence consistent",
        "contradicting_evidence_indices": [],
        "prevention": ["add memory alert"],
    })


@pytest.mark.asyncio
async def test_run_investigation_returns_rca_result():
    agents = {"obs": _make_agent(_valid_evidence_json())}
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_rca_json())
        result = await _run(agents)
    assert isinstance(result, RCAResult)
    assert result.hypothesis == "Memory leak caused OOM"
    assert result.confidence in ("alta", "media", "baixa")


@pytest.mark.asyncio
async def test_run_investigation_handles_agent_failure():
    good_agent = _make_agent(_valid_evidence_json())
    bad_agent = MagicMock()
    bad_agent.process_request = AsyncMock(side_effect=RuntimeError("timeout"))

    agents = {"good": good_agent, "bad": bad_agent}
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_rca_json())
        result = await _run(agents)
    # Should complete despite failure
    assert isinstance(result, RCAResult)


@pytest.mark.asyncio
async def test_run_investigation_respects_max_agents():
    agents = {f"agent{i}": _make_agent(_valid_evidence_json()) for i in range(5)}
    with (
        patch("src.supervisor.investigation.MAX_AGENTS_PER_INVESTIGATION", 2),
        patch("src.supervisor.investigation.bedrock") as mock_bedrock,
    ):
        mock_bedrock.invoke = AsyncMock(return_value=_rca_json())
        await _run(agents)
    called = sum(1 for a in agents.values() if a.process_request.called)
    assert called == 2


@pytest.mark.asyncio
async def test_run_investigation_parses_evidence_json():
    evidence_json = json.dumps([
        {"signal_type": "trace", "timestamp": "2026-06-14T09:00:00Z", "strength": "forte", "summary": "slow span"},
    ])
    agents = {"obs": _make_agent(evidence_json)}
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_rca_json())
        result = await _run(agents)
    assert len(result.evidence) == 1
    assert result.evidence[0].signal_type == "trace"
    assert result.evidence[0].strength == "forte"


@pytest.mark.asyncio
async def test_run_investigation_falls_back_on_invalid_json():
    agents = {"obs": _make_agent("I found something wrong but no JSON here")}
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_rca_json())
        result = await _run(agents)
    assert len(result.evidence) == 1
    assert result.evidence[0].strength == "fraca"
    assert result.evidence[0].signal_type == "unknown"


async def _run(agents):
    from src.supervisor.investigation import run_investigation
    return await run_investigation(
        symptom="latency spike on service X",
        agents=agents,
        user_id="test",
        session_id="test-session",
    )


# --- _synthesize_rca direct tests ---


@pytest.mark.asyncio
async def test_synthesize_rca_includes_rag_block_when_provided():
    from src.supervisor.investigation import _synthesize_rca, Evidence

    evidence = [Evidence(source_agent="obs", signal_type="metric", timestamp="2026-01-01T00:00:00Z", strength="forte", summary="CPU 95%")]
    timeline = evidence[:]

    with patch("src.supervisor.investigation.inject_similar_cases", new_callable=AsyncMock) as mock_rag, \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_rag.return_value = "<similar_cases>\n<case>OOM pattern</case>\n</similar_cases>\n\nNOTE: priors."
        mock_bedrock.invoke = AsyncMock(return_value=json.dumps({
            "hypothesis": "CPU saturation", "prevention": []
        }))

        await _synthesize_rca("high cpu", evidence, timeline)

        user_msg = mock_bedrock.invoke.call_args[1]["messages"][0]["content"]
        assert "similar_cases" in user_msg


@pytest.mark.asyncio
async def test_synthesize_rca_handles_invalid_json_response():
    from src.supervisor.investigation import _synthesize_rca, Evidence

    evidence = [Evidence(source_agent="obs", signal_type="log", timestamp="", strength="media", summary="error")]
    timeline = []

    with patch("src.supervisor.investigation.inject_similar_cases", new_callable=AsyncMock, return_value=""), \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value="not valid json at all")

        result = await _synthesize_rca("something broke", evidence, timeline)

    assert isinstance(result, RCAResult)
    # Production behavior: invalid JSON → falls back to default hypothesis
    # (not echoing raw text). See _synthesize_rca exception handler.
    assert result.hypothesis  # non-empty
    assert result.confidence in ("alta", "media", "baixa")


@pytest.mark.asyncio
async def test_synthesize_rca_handles_no_evidence():
    from src.supervisor.investigation import _synthesize_rca

    with patch("src.supervisor.investigation.inject_similar_cases", new_callable=AsyncMock, return_value=""), \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=json.dumps({
            "hypothesis": "unknown", "prevention": []
        }))

        await _synthesize_rca("mystery", [], [])

    user_msg = mock_bedrock.invoke.call_args[1]["messages"][0]["content"]
    assert "(no evidence collected)" in user_msg
    assert "(no temporal evidence)" in user_msg
