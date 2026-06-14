"""Tests for src/supervisor/synthesizer.py"""
import pytest
from unittest.mock import AsyncMock, patch

from src.supervisor.synthesizer import Synthesizer


@pytest.mark.asyncio
async def test_synthesize_two_agents():
    """Two agent responses → bedrock called once with both in the message."""
    synth = Synthesizer()

    with patch("src.supervisor.synthesizer.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value="combined answer")

        result = await synth.synthesize(
            "my query",
            [("aws", "resp1"), ("k8s", "resp2")],
            []
        )

    assert result == "combined answer"
    mock_bedrock.invoke.assert_called_once()
    call_kwargs = mock_bedrock.invoke.call_args[1]
    user_msg = call_kwargs["messages"][0]["content"]
    assert "aws" in user_msg
    assert "resp1" in user_msg
    assert "k8s" in user_msg
    assert "resp2" in user_msg
    assert "FAILED" not in user_msg


@pytest.mark.asyncio
async def test_synthesize_with_failures():
    """Failed agents listed → user message mentions FAILED agents."""
    synth = Synthesizer()

    with patch("src.supervisor.synthesizer.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(return_value="partial answer")

        result = await synth.synthesize(
            "query",
            [("aws", "ok response")],
            ["k8s"]
        )

    assert result == "partial answer"
    user_msg = mock_bedrock.invoke.call_args[1]["messages"][0]["content"]
    assert "FAILED" in user_msg
    assert "k8s" in user_msg


@pytest.mark.asyncio
async def test_synthesize_all_failed():
    """Empty responses → returns failure message without calling bedrock."""
    synth = Synthesizer()

    with patch("src.supervisor.synthesizer.bedrock") as mock_bedrock:
        result = await synth.synthesize("query", [], ["aws", "k8s"])

    mock_bedrock.invoke.assert_not_called()
    assert "All agents failed" in result
