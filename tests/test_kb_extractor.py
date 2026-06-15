"""Tests for src.core.kb.extractor — LLM-based delta extraction."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from src.core.investigation import RCAResult
from src.core.kb.extractor import extract_deltas


@pytest.mark.asyncio
@patch("src.core.kb.extractor.bedrock")
async def test_extract_deltas_returns_parsed_drafts(mock_bedrock):
    mock_bedrock.invoke = AsyncMock(return_value=json.dumps({
        "deltas": [{"action": "create", "type": "troubleshooting",
                    "title": "X", "content": "Y", "confidence": 0.9}]
    }))
    rca = RCAResult(hypothesis="test hypothesis", confidence="alta")
    result = await extract_deltas(rca)
    assert len(result) == 1
    assert result[0].title == "X"


@pytest.mark.asyncio
@patch("src.core.kb.extractor.bedrock")
async def test_extract_deltas_empty_on_invalid_json(mock_bedrock):
    mock_bedrock.invoke = AsyncMock(return_value="invalid response no json")
    rca = RCAResult(hypothesis="test", confidence="alta")
    result = await extract_deltas(rca)
    assert result == []


@pytest.mark.asyncio
@patch("src.core.kb.extractor.bedrock")
async def test_extract_deltas_redacts_pii_before_llm(mock_bedrock):
    mock_bedrock.invoke = AsyncMock(return_value='{"deltas":[]}')
    rca = RCAResult(hypothesis="contact user@example.com for details", confidence="alta")
    await extract_deltas(rca)
    user_content = mock_bedrock.invoke.call_args.kwargs["messages"][0]["content"]
    assert "user@example.com" not in user_content
    assert "<redacted:email>" in user_content
