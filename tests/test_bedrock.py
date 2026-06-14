"""Tests for BedrockClient.invoke()"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_boto3_client():
    with patch("src.core.bedrock.boto3.client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_invoke_returns_response_text(mock_boto3_client):
    """Bedrock invoke returns the text content from the model response."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"Hello world"}],"usage":{"inputTokens":10,"outputTokens":5}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    result = client.invoke(
        messages=[{"role": "user", "content": "test"}],
        system_prompt="You are helpful.",
    )
    assert result == "Hello world"


def test_invoke_handles_exception(mock_boto3_client):
    """Bedrock invoke raises when API fails."""
    mock_boto3_client.invoke_model.side_effect = Exception("Throttled")
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with pytest.raises(Exception, match="Throttled"):
        client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )


def test_invoke_records_metrics(mock_boto3_client):
    """Bedrock invoke records token metrics after successful call."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"inputTokens":100,"outputTokens":50}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter") as mock_counter, \
         patch("src.core.bedrock.estimated_cost") as mock_cost:
        result = client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )
        assert result == "ok"
        # Verify metrics were recorded
        assert mock_counter.add.call_count == 2  # input + output
