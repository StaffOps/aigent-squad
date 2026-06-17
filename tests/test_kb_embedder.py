"""Tests for src.core.kb.embedder — Bedrock Titan embedding."""
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_embed_returns_none_for_empty_text():
    from src.core.kb.embedder import embed
    result = await embed("")
    assert result is None


@pytest.mark.asyncio
async def test_embed_returns_none_for_whitespace_only():
    from src.core.kb.embedder import embed
    result = await embed("   ")
    assert result is None


@pytest.mark.asyncio
async def test_embed_returns_none_on_bedrock_error():
    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = Exception("Bedrock error")

    with patch("src.core.kb.embedder._get_client", return_value=mock_client):
        from src.core.kb.embedder import embed
        result = await embed("some text")
    assert result is None


@pytest.mark.asyncio
async def test_embed_returns_vector_on_success():
    embedding = [0.1] * 1024
    body_bytes = json.dumps({"embedding": embedding}).encode()
    mock_resp = {"body": MagicMock(read=MagicMock(return_value=body_bytes))}
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = mock_resp

    with patch("src.core.kb.embedder._get_client", return_value=mock_client):
        from src.core.kb.embedder import embed
        result = await embed("test input text")
    assert result == embedding
    assert len(result) == 1024
