"""Tests for BedrockClient.invoke()"""
import json
import pytest
from unittest.mock import patch, MagicMock


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
         patch("src.core.bedrock.estimated_cost"):
        result = await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )
        assert result == "ok"
        assert mock_counter.add.call_count == 2


@pytest.mark.asyncio
async def test_invoke_labels_metrics_with_agent_id(mock_boto3_client):
    """agent_id is propagated into token + cost metric labels (spec 27)."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":100,"output_tokens":50}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter") as mock_counter, \
         patch("src.core.bedrock.estimated_cost") as mock_cost:
        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            agent_id="kubernetes",
        )

    # both token_counter.add calls carry agent_id
    for call in mock_counter.add.call_args_list:
        labels = call.args[1]
        assert labels["agent_id"] == "kubernetes"
    # cost metric also carries agent_id
    cost_labels = mock_cost.add.call_args.args[1]
    assert cost_labels["agent_id"] == "kubernetes"


@pytest.mark.asyncio
async def test_invoke_defaults_agent_id_to_unknown(mock_boto3_client):
    """When no agent_id is passed, metrics are labeled 'unknown' (no crash)."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter") as mock_counter, \
         patch("src.core.bedrock.estimated_cost"):
        await client.invoke(messages=[{"role": "user", "content": "t"}], system_prompt="s")

    assert mock_counter.add.call_args_list[0].args[1]["agent_id"] == "unknown"


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


@pytest.mark.asyncio
async def test_invoke_injects_language_directive_by_default(mock_boto3_client):
    """By default, the system prompt carries the language-matching directive."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    await client.invoke(messages=[{"role": "user", "content": "oi"}], system_prompt="BASE")

    body = json.loads(mock_boto3_client.invoke_model.call_args.kwargs["body"])
    sys_text = body["system"][0]["text"]
    assert "BASE" in sys_text
    assert "SAME language" in sys_text


@pytest.mark.asyncio
async def test_invoke_can_opt_out_of_language_directive(mock_boto3_client):
    """match_user_language=False keeps the system prompt clean (e.g. classifier JSON)."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":1,"output_tokens":1}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    await client.invoke(
        messages=[{"role": "user", "content": "x"}],
        system_prompt="BASE",
        match_user_language=False,
    )

    body = json.loads(mock_boto3_client.invoke_model.call_args.kwargs["body"])
    assert body["system"][0]["text"] == "BASE"
