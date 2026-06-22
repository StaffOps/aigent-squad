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
async def test_invoke_records_llm_duration_and_prompt_size(mock_boto3_client):
    """spec 10: invoke records llm.duration + prompt.size_tokens labeled by agent_id."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":321,"output_tokens":50}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter"), \
         patch("src.core.bedrock.estimated_cost"), \
         patch("src.core.bedrock.llm_duration") as mock_llm_dur, \
         patch("src.core.bedrock.prompt_size_tokens") as mock_prompt_size:
        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            agent_id="finops",
        )

    # llm.duration recorded once with a non-negative ms value, labeled agent_id
    assert mock_llm_dur.record.call_count == 1
    dur_value, dur_labels = mock_llm_dur.record.call_args.args
    assert dur_value >= 0
    assert dur_labels == {"agent_id": "finops"}

    # prompt.size_tokens records the Bedrock-reported input tokens
    mock_prompt_size.record.assert_called_once_with(321, {"agent_id": "finops"})


@pytest.mark.asyncio
async def test_prompt_size_reads_snake_case_usage_key(mock_boto3_client):
    """spec 10 contract: prompt.size_tokens is the Bedrock Messages-API
    `input_tokens` (snake_case). A camelCase-only `inputTokens` response (which
    is NOT what bedrock-runtime returns for the Anthropic Messages API) is not
    read, so the metric records 0 rather than the camelCase value.

    This pins the key the impl reads and documents why several legacy mocks in
    this file that use `inputTokens` never actually assert a token *value*.
    """
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"ok"}],"usage":{"inputTokens":999,"outputTokens":7}}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter"), \
         patch("src.core.bedrock.estimated_cost"), \
         patch("src.core.bedrock.llm_duration"), \
         patch("src.core.bedrock.prompt_size_tokens") as mock_prompt_size:
        await client.invoke(
            messages=[{"role": "user", "content": "x"}],
            system_prompt="s",
            agent_id="aws",
        )

    # camelCase key is ignored → snake_case default of 0 recorded, no crash.
    mock_prompt_size.record.assert_called_once_with(0, {"agent_id": "aws"})


@pytest.mark.asyncio
async def test_prompt_size_records_zero_when_usage_absent(mock_boto3_client):
    """spec 10 contract: a response with no `usage` block records 0 input
    tokens (graceful default), never raises KeyError."""
    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"ok"}]}'))
    }
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.token_counter"), \
         patch("src.core.bedrock.estimated_cost"), \
         patch("src.core.bedrock.llm_duration") as mock_llm_dur, \
         patch("src.core.bedrock.prompt_size_tokens") as mock_prompt_size:
        result = await client.invoke(
            messages=[{"role": "user", "content": "x"}],
            system_prompt="s",
            agent_id="devops",
        )

    assert result == "ok"
    mock_prompt_size.record.assert_called_once_with(0, {"agent_id": "devops"})
    # llm.duration is independent of usage and is still recorded once.
    assert mock_llm_dur.record.call_count == 1


@pytest.mark.asyncio
async def test_llm_duration_excludes_retry_backoff(mock_boto3_client):
    """spec 10 contract: llm.duration is per-call round-trip latency and MUST
    exclude retry backoff sleep. With one throttle + a 5s backoff before the
    successful call, the recorded duration must be far below the backoff time
    (the timer is started inside the final attempt, after sleeping)."""
    from botocore.exceptions import ClientError

    throttle_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "InvokeModel",
    )
    success_response = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"ok"}],"usage":{"input_tokens":10,"output_tokens":5}}'))
    }
    mock_boto3_client.invoke_model.side_effect = [throttle_error, success_response]

    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client
    client.base_delay = 5.0  # large backoff; must NOT leak into llm.duration

    # Patch time.sleep so the test doesn't actually wait, but still let the
    # impl's elapsed-time math run against the real clock around invoke_model.
    with patch("src.core.bedrock.time.sleep"), \
         patch("src.core.bedrock.token_counter"), \
         patch("src.core.bedrock.estimated_cost"), \
         patch("src.core.bedrock.prompt_size_tokens"), \
         patch("src.core.bedrock.llm_duration") as mock_llm_dur:
        result = await client.invoke(
            messages=[{"role": "user", "content": "x"}],
            system_prompt="s",
            agent_id="kubernetes",
        )

    assert result == "ok"
    # Only the successful attempt records duration.
    assert mock_llm_dur.record.call_count == 1
    dur_value, dur_labels = mock_llm_dur.record.call_args.args
    assert dur_labels == {"agent_id": "kubernetes"}
    # Backoff was 5000ms; a mocked invoke_model returns in well under 1s.
    # If backoff leaked in, this would be ~5000+.
    assert dur_value < 1000


@pytest.mark.asyncio
async def test_invoke_does_not_record_efficiency_metrics_on_failure(mock_boto3_client):
    """spec 10: llm.duration + prompt.size_tokens only emitted on a successful call."""
    mock_boto3_client.invoke_model.side_effect = Exception("boom")
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = mock_boto3_client
    client.max_retries = 1

    with patch("src.core.bedrock.llm_duration") as mock_llm_dur, \
         patch("src.core.bedrock.prompt_size_tokens") as mock_prompt_size:
        with pytest.raises(Exception, match="boom"):
            await client.invoke(messages=[{"role": "user", "content": "x"}], system_prompt="s")

    mock_llm_dur.record.assert_not_called()
    mock_prompt_size.record.assert_not_called()


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


# ---------------------------------------------------------------------------
# Guardrail integration (spec 14 Phase 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_guardrail_block_skips_invoke(mock_boto3_client):
    """spec 14: INPUT guardrail blocks → invoke_model is NEVER called."""
    from src.core.bedrock import BedrockClient
    from src.core.guardrail import GuardrailBlockedError

    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.guardrail") as mock_gr:
        mock_gr.apply.side_effect = GuardrailBlockedError(
            reason="blocked", source="INPUT", categories=["topic:Attack"]
        )
        with pytest.raises(GuardrailBlockedError) as exc_info:
            await client.invoke(
                messages=[{"role": "user", "content": "evil"}],
                system_prompt="sys",
                agent_id="test",
            )
        assert exc_info.value.reason == "blocked"
        mock_boto3_client.invoke_model.assert_not_called()


@pytest.mark.asyncio
async def test_output_guardrail_block_raises(mock_boto3_client):
    """spec 14: OUTPUT guardrail blocks → raises after invoke_model ran."""
    from src.core.bedrock import BedrockClient
    from src.core.guardrail import GuardrailBlockedError

    mock_boto3_client.invoke_model.return_value = {
        "body": MagicMock(read=MagicMock(
            return_value=b'{"content":[{"text":"leaked PII"}],"usage":{"input_tokens":1,"output_tokens":1}}'))
    }
    client = BedrockClient()
    client.client = mock_boto3_client

    call_count = [0]

    def _side_effect(text, source, **kwargs):
        call_count[0] += 1
        if source == "OUTPUT":
            raise GuardrailBlockedError(reason="blocked", source="OUTPUT", categories=["pii:SSN"])

    with patch("src.core.bedrock.guardrail") as mock_gr:
        mock_gr.apply.side_effect = _side_effect
        with pytest.raises(GuardrailBlockedError) as exc_info:
            await client.invoke(
                messages=[{"role": "user", "content": "show me data"}],
                system_prompt="sys",
            )
        assert exc_info.value.source == "OUTPUT"
        # invoke_model WAS called (input passed)
        mock_boto3_client.invoke_model.assert_called_once()


@pytest.mark.asyncio
async def test_guardrail_blocked_does_not_trip_circuit_breaker(mock_boto3_client):
    """spec 14: GuardrailBlockedError does NOT record a circuit-breaker failure."""
    from src.core.bedrock import BedrockClient
    from src.core.guardrail import GuardrailBlockedError

    client = BedrockClient()
    client.client = mock_boto3_client

    with patch("src.core.bedrock.guardrail") as mock_gr:
        mock_gr.apply.side_effect = GuardrailBlockedError(
            reason="blocked", source="INPUT"
        )
        with patch.object(client.circuit_breaker, "record_failure") as mock_fail:
            with pytest.raises(GuardrailBlockedError):
                await client.invoke(
                    messages=[{"role": "user", "content": "x"}],
                    system_prompt="s",
                )
            mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_user_text_extracts_user_role_only(mock_boto3_client):
    """_user_text only picks user-role messages, ignoring assistant."""
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    messages = [
        {"role": "assistant", "content": "I'm the assistant"},
        {"role": "user", "content": "user message"},
        {"role": "system", "content": "system msg"},
    ]
    result = client._user_text(messages)
    assert "user message" in result
    assert "assistant" not in result
    assert "system" not in result


@pytest.mark.asyncio
async def test_user_text_handles_anthropic_blocks(mock_boto3_client):
    """_user_text extracts text from Anthropic block list format."""
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "block one"},
            {"type": "image", "data": "..."},
            {"type": "text", "text": "block two"},
        ]},
    ]
    result = client._user_text(messages)
    assert "block one" in result
    assert "block two" in result
    assert "..." not in result
