"""Tests for BedrockClient.invoke()"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture
def mock_boto3_client():
    with patch("src.core.bedrock.boto3.client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.mark.asyncio
async def test_invoke_returns_response_text(mock_boto3_client):
    """Bedrock invoke returns the text content from the model response."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"Hello world"}],"usage":{"inputTokens":10,"outputTokens":5}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    result = await client.invoke(
        messages=[{"role": "user", "content": "test"}],
        system_prompt="You are helpful.",
    )
    assert result == "Hello world"


@pytest.mark.asyncio
async def test_invoke_handles_exception(mock_boto3_client):
    """Bedrock invoke raises when API fails."""
    mock_boto3_client.invoke_model.side_effect = Exception("Throttled")
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with pytest.raises(Exception, match="Throttled"):
        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )


@pytest.mark.asyncio
async def test_invoke_records_metrics(mock_boto3_client):
    """Bedrock invoke records token metrics after successful call."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"inputTokens":100,"outputTokens":50}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter") as mock_counter, \
         patch("src.core.bedrock.estimated_cost") as mock_cost:
        result = await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )
        assert result == "ok"
        assert mock_counter.add.call_count == 2


@pytest.mark.asyncio
async def test_invoke_retries_on_throttling(mock_boto3_client):
    """Bedrock retries on ThrottlingException then succeeds."""
    from botocore.exceptions import ClientError
    from src.core.bedrock import BedrockClient

    throttle_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "InvokeModel"
    )
    success_response = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"success"}],"usage":{"inputTokens":5,"outputTokens":3}}'))
    }

    mock_boto3_client.invoke_model.side_effect = [throttle_error, throttle_error, success_response]

    client = BedrockClient()
    client.client = mock_boto3_client
    client.base_delay = 0  # no wait in tests

    result = await client.invoke(
        messages=[{"role": "user", "content": "test"}],
        system_prompt="sys",
    )
    assert result == "success"
    assert mock_boto3_client.invoke_model.call_count == 3


@pytest.mark.asyncio
async def test_invoke_circuit_breaker_blocks_after_failures(mock_boto3_client):
    """After threshold failures, circuit breaker opens and rejects calls."""
    from src.core.bedrock import BedrockClient
    from src.core.circuit_breaker import CircuitBreaker

    client = BedrockClient()
    client.client = mock_boto3_client
    client.max_retries = 1
    client.base_delay = 0
    client.circuit_breaker = CircuitBreaker("test-cb", failure_threshold=3, recovery_timeout=60.0)

    mock_boto3_client.invoke_model.side_effect = Exception("fail")

    # Trip the breaker
    for _ in range(3):
        with pytest.raises(Exception):
            await client.invoke(messages=[{"role": "user", "content": "x"}], system_prompt="s")

    # Next call should be blocked by circuit breaker
    with pytest.raises(Exception, match="circuit breaker"):
        await client.invoke(messages=[{"role": "user", "content": "x"}], system_prompt="s")
