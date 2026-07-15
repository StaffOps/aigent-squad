"""Spec 14 Phase-6 follow-up findings E1 + F (independent author).

F — /alerts/incoming (and any caller reaching run_investigation outside
    process_request) must have the symptom scanned by the entry-stage L2
    InputScanner: fail-closed block, and normalized text propagated downstream.

E1 — synthesizer.synthesize and investigation._synthesize_rca must forward
    agent_id/user_id/session_id to bedrock.invoke so synthesis Bedrock calls are
    attributable (OUTPUT-guardrail audit) and their Sonnet tokens count against
    the session budget.
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.guardrail import GuardrailBlockedError
from src.core.investigation import RCAResult
from src.core.state_store import ConversationMessage


def _make_agent(response_content: str):
    agent = MagicMock()
    agent.process_request = AsyncMock(
        return_value=ConversationMessage(
            role="assistant",
            content=response_content,
            timestamp="2026-07-11T10:00:00Z",
        )
    )
    return agent


_EVIDENCE_JSON = json.dumps([
    {"signal_type": "metric", "timestamp": "2026-07-11T10:00:00Z",
     "strength": "forte", "summary": "CPU 95%"},
])
_RCA_JSON = json.dumps({
    "hypothesis": "Memory leak caused OOM",
    "reasoning": "consistent",
    "contradicting_evidence_indices": [],
    "prevention": ["add memory alert"],
})


# --------------------------------------------------------------------------
# Finding F — symptom is scanned/normalized at the run_investigation entry
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_investigation_blocks_oversized_symptom_before_fanout():
    """An oversized symptom (>10k) is a fail-closed scanner block BEFORE any
    agent is consulted or the RCA synthesizer runs."""
    from src.supervisor.investigation import run_investigation

    agent = _make_agent(_EVIDENCE_JSON)
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_RCA_JSON)
        with pytest.raises(GuardrailBlockedError):
            await run_investigation(
                symptom="x" * 10_001,
                agents={"obs": agent},
                user_id="alertmanager",
                session_id="s-alert",
            )
    # Fail-closed: no agent consulted, no synthesis invoked.
    agent.process_request.assert_not_called()
    mock_bedrock.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_run_investigation_normalizes_homoglyph_symptom():
    """A Cyrillic-homoglyph symptom is folded to Latin at entry; the folded text
    is what reaches the agents (and downstream synthesis)."""
    from src.supervisor.investigation import run_investigation

    # "cpu spike" with Cyrillic 'і' (U+0456) and 'е' (U+0435)
    homoglyph_symptom = "cpu spіkе on svc"
    agent = _make_agent(_EVIDENCE_JSON)
    with patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_RCA_JSON)
        await run_investigation(
            symptom=homoglyph_symptom,
            agents={"obs": agent},
            user_id="alertmanager",
            session_id="s-alert",
        )

    evidence_query = agent.process_request.call_args.kwargs["input_text"]
    assert "cpu spike on svc" in evidence_query          # folded to Latin
    assert "і" not in evidence_query                # no Cyrillic left
    assert "е" not in evidence_query


@pytest.mark.asyncio
async def test_run_investigation_passthrough_when_scanner_disabled():
    """With the scanner disabled, run_investigation proceeds (no size cap)."""
    from src.supervisor.investigation import run_investigation

    agent = _make_agent(_EVIDENCE_JSON)
    with patch("src.supervisor.investigation._scanner.enabled", False), \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_RCA_JSON)
        result = await run_investigation(
            symptom="x" * 10_001,   # would block if scanner were enabled
            agents={"obs": agent},
            user_id="alertmanager",
            session_id="s-alert",
        )
    assert isinstance(result, RCAResult)
    agent.process_request.assert_called_once()


# --------------------------------------------------------------------------
# Finding E1 — synthesis calls are attributed + budgeted
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesizer_forwards_attribution_ids():
    from src.supervisor.synthesizer import Synthesizer

    with patch("src.supervisor.synthesizer.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value="merged")
        await Synthesizer().synthesize(
            "q", [("aws", "r1"), ("k8s", "r2")], [],
            user_id="u-123", session_id="s-456",
        )

    kw = mock_bedrock.invoke.call_args.kwargs
    assert kw["agent_id"] == "synthesizer"
    assert kw["user_id"] == "u-123"
    assert kw["session_id"] == "s-456"


@pytest.mark.asyncio
async def test_synthesize_rca_forwards_attribution_ids():
    from src.supervisor.investigation import _synthesize_rca, Evidence

    evidence = [Evidence(source_agent="obs", signal_type="metric",
                         timestamp="", strength="forte", summary="CPU 95%")]
    with patch("src.supervisor.investigation.inject_similar_cases",
               new_callable=AsyncMock, return_value=""), \
         patch("src.supervisor.investigation.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=_RCA_JSON)
        await _synthesize_rca("high cpu", evidence, evidence,
                              user_id="u-9", session_id="s-9")

    kw = mock_bedrock.invoke.call_args.kwargs
    assert kw["agent_id"] == "rca-synthesizer"
    assert kw["user_id"] == "u-9"
    assert kw["session_id"] == "s-9"


@pytest.mark.asyncio
async def test_run_investigation_threads_session_into_synthesis():
    """End-to-end: the session_id given to run_investigation reaches the
    synthesis Bedrock call (so its tokens hit the right budget bucket)."""
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

    # The only bedrock.invoke here is the RCA synthesis call.
    kw = mock_bedrock.invoke.call_args.kwargs
    assert kw["agent_id"] == "rca-synthesizer"
    assert kw["user_id"] == "u-real"
    assert kw["session_id"] == "s-real"
