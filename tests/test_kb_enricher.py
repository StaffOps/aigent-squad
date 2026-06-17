"""Tests for KbEnricher (Opus refinement of drafts)."""
import json
import pytest
from unittest.mock import AsyncMock, patch
from src.core.investigation import RCAResult, Evidence
from src.core.kb.enricher import enrich_deltas
from src.core.kb.models import KbDelta


def _make_rca():
    return RCAResult(
        hypothesis="Service X had high latency due to deploy",
        confidence="alta",
        evidence=[Evidence(source_agent="a", signal_type="metric", timestamp="", strength="forte", summary="latency p99 up")],
    )


def _make_drafts():
    return [
        KbDelta(action="create", type="troubleshooting", title="Latency spike", content="Symptom: ...", confidence=0.8),
    ]


@pytest.mark.asyncio
async def test_enrich_deltas_returns_empty_for_empty_drafts():
    """No drafts → no enrichment, returns empty list."""
    rca = _make_rca()
    result = await enrich_deltas([], rca)
    assert result == []


@pytest.mark.asyncio
async def test_enrich_deltas_parses_refined_response():
    """Bedrock returns valid JSON → parsed into refined KbDelta list."""
    refined_response = json.dumps({
        "deltas": [
            {"action": "create", "type": "troubleshooting", "title": "Refined Title", "content": "Refined content", "confidence": 0.92}
        ]
    })

    with patch("src.core.kb.enricher.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value=refined_response)
        result = await enrich_deltas(_make_drafts(), _make_rca())

    assert len(result) == 1
    assert result[0].title == "Refined Title"
    assert result[0].confidence == 0.92


@pytest.mark.asyncio
async def test_enrich_deltas_falls_back_on_invalid_json():
    """Invalid JSON from Bedrock → returns original drafts (graceful)."""
    drafts = _make_drafts()
    with patch("src.core.kb.enricher.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value="not json at all")
        result = await enrich_deltas(drafts, _make_rca())

    assert result == drafts


@pytest.mark.asyncio
async def test_enrich_deltas_falls_back_on_bedrock_failure():
    """Bedrock raises → returns original drafts (best-effort)."""
    drafts = _make_drafts()
    with patch("src.core.kb.enricher.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(side_effect=Exception("rate limit"))
        result = await enrich_deltas(drafts, _make_rca())

    assert result == drafts
