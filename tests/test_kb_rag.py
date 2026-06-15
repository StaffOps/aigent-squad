"""Tests for src.core.kb.rag — RAG injection of similar cases."""
import pytest
from unittest.mock import patch, AsyncMock

from src.core.kb.models import KbItem
from src.core.kb.rag import inject_similar_cases


@pytest.mark.asyncio
@patch("src.core.kb.rag.embed", new_callable=AsyncMock)
async def test_rag_returns_empty_when_embedding_fails(mock_embed):
    mock_embed.return_value = None
    result = await inject_similar_cases("some symptom")
    assert result == ""


@pytest.mark.asyncio
@patch("src.core.kb.rag.kb_store")
@patch("src.core.kb.rag.embed", new_callable=AsyncMock)
async def test_rag_returns_empty_when_no_matches(mock_embed, mock_store):
    mock_embed.return_value = [0.1] * 1024
    mock_store.search_similar = AsyncMock(return_value=[])
    result = await inject_similar_cases("some symptom")
    assert result == ""


@pytest.mark.asyncio
@patch("src.core.kb.rag.kb_store")
@patch("src.core.kb.rag.embed", new_callable=AsyncMock)
async def test_rag_returns_xml_block_with_matches(mock_embed, mock_store):
    mock_embed.return_value = [0.1] * 1024
    item = KbItem(title="OOM on redis", content="Increase memory limit", type="troubleshooting")
    mock_store.search_similar = AsyncMock(return_value=[item])
    result = await inject_similar_cases("redis crash")
    assert "<similar_cases>" in result
    assert "OOM on redis" in result
