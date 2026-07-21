"""Tests for BedrockClient.converse() — Converse API engine (spec 37).

Tests the CONTRACT via mocked boto3 bedrock-runtime converse client (no network).
Does NOT modify the implementation; invoke() tests remain in test_bedrock.py.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_boto3_client():
    """Patch boto3.client at module level so BedrockClient.__init__ gets a mock."""
    with patch("src.core.bedrock.boto3.client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def bedrock_client(mock_boto3_client):
    """Fresh BedrockClient instance wired to the mocked boto3 client."""
    from src.core.bedrock import BedrockClient
    bc = BedrockClient()
    bc.client = mock_boto3_client
    return bc


def _converse_response(stop_reason="end_turn", content_blocks=None, input_tokens=10, output_tokens=5):
    """Factory for a valid Converse API response dict."""
    if content_blocks is None:
        content_blocks = [{"text": "Hello from converse"}]
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": content_blocks}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


def _tool_use_block(tool_use_id="tu-123", name="get_weather", input_data=None):
    """Factory for a toolUse content block in Converse response format."""
    return {"toolUse": {"toolUseId": tool_use_id, "name": name, "input": input_data or {"city": "SP"}}}


def _make_client_error(code, message="test error"):
    """Build a botocore ClientError with given code."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "Converse",
    )


# ---------------------------------------------------------------------------
# (1) stop_reason=='tool_use' → parsed toolUse blocks surfaced
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_tool_use_blocks_parsed(bedrock_client, mock_boto3_client):
    """When stop_reason is tool_use, content contains normalized tool_use blocks."""
    mock_boto3_client.converse.return_value = _converse_response(
        stop_reason="tool_use",
        content_blocks=[
            _tool_use_block("tu-001", "search_docs", {"query": "hello"}),
            _tool_use_block("tu-002", "run_sql", {"sql": "SELECT 1"}),
        ],
    )

    result = await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "use tools"}]}],
        system_prompt="You are helpful.",
        tool_config={"tools": [{"toolSpec": {"name": "search_docs"}}]},
    )

    assert result["stop_reason"] == "tool_use"
    assert len(result["content"]) == 2

    block0 = result["content"][0]
    assert block0["type"] == "tool_use"
    assert block0["toolUseId"] == "tu-001"
    assert block0["name"] == "search_docs"
    assert block0["input"] == {"query": "hello"}

    block1 = result["content"][1]
    assert block1["type"] == "tool_use"
    assert block1["toolUseId"] == "tu-002"
    assert block1["name"] == "run_sql"
    assert block1["input"] == {"sql": "SELECT 1"}


# ---------------------------------------------------------------------------
# (2) stop_reason=='end_turn' → text surfaced
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_end_turn_text_surfaced(bedrock_client, mock_boto3_client):
    """When stop_reason is end_turn, text content blocks are surfaced."""
    mock_boto3_client.converse.return_value = _converse_response(
        stop_reason="end_turn",
        content_blocks=[{"text": "The answer is 42."}],
        input_tokens=20,
        output_tokens=8,
    )

    result = await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "What is the answer?"}]}],
        system_prompt="Be precise.",
    )

    assert result["stop_reason"] == "end_turn"
    assert len(result["content"]) == 1
    assert result["content"][0] == {"type": "text", "text": "The answer is 42."}


@pytest.mark.asyncio
async def test_converse_mixed_text_and_tool_use(bedrock_client, mock_boto3_client):
    """Mixed response with both text and toolUse blocks is parsed correctly."""
    mock_boto3_client.converse.return_value = _converse_response(
        stop_reason="tool_use",
        content_blocks=[
            {"text": "Let me check that for you."},
            _tool_use_block("tu-99", "lookup", {"id": 7}),
        ],
    )

    result = await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "check something"}]}],
        system_prompt="sys",
        tool_config={"tools": []},
    )

    assert result["content"][0] == {"type": "text", "text": "Let me check that for you."}
    assert result["content"][1]["type"] == "tool_use"
    assert result["content"][1]["name"] == "lookup"


# ---------------------------------------------------------------------------
# (3) Usage token accounting parsed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_usage_tokens_parsed(bedrock_client, mock_boto3_client):
    """Token usage from Converse response is surfaced in result['usage']."""
    mock_boto3_client.converse.return_value = _converse_response(
        input_tokens=150,
        output_tokens=75,
    )

    result = await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "long question"}]}],
        system_prompt="sys",
    )

    assert result["usage"] == {"input_tokens": 150, "output_tokens": 75}


@pytest.mark.asyncio
async def test_converse_token_metrics_emitted(bedrock_client, mock_boto3_client):
    """token_counter.add is called for both input and output directions."""
    mock_boto3_client.converse.return_value = _converse_response(
        input_tokens=100, output_tokens=50,
    )

    with patch("src.core.bedrock.token_counter") as mock_counter, \
         patch("src.core.bedrock.estimated_cost"), \
         patch("src.core.bedrock.llm_duration"), \
         patch("src.core.bedrock.prompt_size_tokens"):
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
            agent_id="test-agent",
        )

    # 2 calls: input + output
    assert mock_counter.add.call_count == 2
    input_call = mock_counter.add.call_args_list[0]
    output_call = mock_counter.add.call_args_list[1]
    assert input_call[0][0] == 100
    assert input_call[0][1]["direction"] == "input"
    assert output_call[0][0] == 50
    assert output_call[0][1]["direction"] == "output"


@pytest.mark.asyncio
async def test_converse_budget_tracker_called(bedrock_client, mock_boto3_client):
    """budget_tracker.record_usage is called with session_id tokens."""
    mock_boto3_client.converse.return_value = _converse_response(
        input_tokens=80, output_tokens=40,
    )

    with patch("src.core.bedrock.budget_tracker") as mock_budget:
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
            session_id="sess-abc",
        )

    mock_budget.record_usage.assert_called_once_with("sess-abc", 80, 40)


@pytest.mark.asyncio
async def test_converse_budget_session_id_override(bedrock_client, mock_boto3_client):
    """budget_session_id overrides session_id for budget accounting."""
    mock_boto3_client.converse.return_value = _converse_response(
        input_tokens=10, output_tokens=5,
    )

    with patch("src.core.bedrock.budget_tracker") as mock_budget:
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
            session_id="child-sess",
            budget_session_id="parent-sess",
        )

    mock_budget.record_usage.assert_called_once_with("parent-sess", 10, 5)


# ---------------------------------------------------------------------------
# (4) Retry/backoff on throttling exception then success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_retries_on_throttling_then_succeeds(bedrock_client, mock_boto3_client):
    """ThrottlingException on attempt 1 triggers retry; attempt 2 succeeds."""
    throttle_err = _make_client_error("ThrottlingException")
    success_resp = _converse_response(content_blocks=[{"text": "retried ok"}])

    mock_boto3_client.converse.side_effect = [throttle_err, success_resp]

    with patch("src.core.bedrock.time.sleep") as mock_sleep:
        result = await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "retry me"}]}],
            system_prompt="sys",
        )

    assert result["content"][0]["text"] == "retried ok"
    # sleep called once (backoff between attempt 1 fail and attempt 2)
    assert mock_sleep.call_count == 1
    # converse called twice
    assert mock_boto3_client.converse.call_count == 2


@pytest.mark.asyncio
async def test_converse_retries_on_service_unavailable(bedrock_client, mock_boto3_client):
    """ServiceUnavailableException triggers retry."""
    err = _make_client_error("ServiceUnavailableException")
    success_resp = _converse_response()
    mock_boto3_client.converse.side_effect = [err, success_resp]

    with patch("src.core.bedrock.time.sleep"):
        result = await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
        )

    assert result["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_converse_exhausts_retries_on_throttling(bedrock_client, mock_boto3_client):
    """After max_retries throttling errors, the ClientError is raised."""
    throttle_err = _make_client_error("ThrottlingException")
    mock_boto3_client.converse.side_effect = [throttle_err, throttle_err, throttle_err]

    with patch("src.core.bedrock.time.sleep"):
        with pytest.raises(ClientError) as exc_info:
            await bedrock_client.converse(
                messages=[{"role": "user", "content": [{"text": "fail"}]}],
                system_prompt="sys",
            )

    assert exc_info.value.response["Error"]["Code"] == "ThrottlingException"


@pytest.mark.asyncio
async def test_converse_backoff_delay_increases(bedrock_client, mock_boto3_client):
    """Backoff delay doubles on consecutive retries (exponential)."""
    throttle_err = _make_client_error("ThrottlingException")
    success_resp = _converse_response()
    mock_boto3_client.converse.side_effect = [throttle_err, throttle_err, success_resp]

    with patch("src.core.bedrock.time.sleep") as mock_sleep, \
         patch("src.core.bedrock.random.uniform", return_value=0.5):
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
        )

    # attempt 0 fail → delay = 1.0 * 2^0 + 0.5 = 1.5
    # attempt 1 fail → delay = 1.0 * 2^1 + 0.5 = 2.5
    delays = [c[0][0] for c in mock_sleep.call_args_list]
    assert len(delays) == 2
    assert abs(delays[0] - 1.5) < 0.01
    assert abs(delays[1] - 2.5) < 0.01


# ---------------------------------------------------------------------------
# (5) guardrailConfig passed with configured id/version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_passes_guardrail_config(bedrock_client, mock_boto3_client):
    """When guardrail is enabled with id, guardrailConfig is in the API call."""
    mock_boto3_client.converse.return_value = _converse_response()

    with patch("src.core.bedrock.settings") as mock_settings:
        mock_settings.guardrail_enabled = True
        mock_settings.guardrail_id = "gr-abc123"
        mock_settings.guardrail_version = "3"

        # Need to re-call _converse_sync directly to use patched settings
        from src.core.bedrock import BedrockClient
        bc = BedrockClient()
        bc.client = mock_boto3_client

        with patch("src.core.bedrock.resolve_model", return_value="us.anthropic.claude-sonnet-4-5-20250929-v1:0"), \
             patch("src.core.bedrock.guardrail"), \
             patch("src.core.bedrock.budget_tracker"), \
             patch("src.core.bedrock.token_counter"), \
             patch("src.core.bedrock.estimated_cost"), \
             patch("src.core.bedrock.llm_duration"), \
             patch("src.core.bedrock.prompt_size_tokens"), \
             patch("src.core.bedrock.compute_cost", return_value=0.001):
            bc._converse_sync(
                messages=[{"role": "user", "content": [{"text": "test"}]}],
                system_prompt="sys",
            )

    call_kwargs = mock_boto3_client.converse.call_args[1]
    assert "guardrailConfig" in call_kwargs
    assert call_kwargs["guardrailConfig"]["guardrailIdentifier"] == "gr-abc123"
    assert call_kwargs["guardrailConfig"]["guardrailVersion"] == "3"


@pytest.mark.asyncio
async def test_converse_no_guardrail_config_when_disabled(bedrock_client, mock_boto3_client):
    """When guardrail is disabled, guardrailConfig is NOT in the API call."""
    mock_boto3_client.converse.return_value = _converse_response()

    with patch("src.core.bedrock.settings") as mock_settings:
        mock_settings.guardrail_enabled = False
        mock_settings.guardrail_id = "gr-abc123"
        mock_settings.guardrail_version = "3"

        from src.core.bedrock import BedrockClient
        bc = BedrockClient()
        bc.client = mock_boto3_client

        with patch("src.core.bedrock.resolve_model", return_value="model-id"), \
             patch("src.core.bedrock.guardrail"), \
             patch("src.core.bedrock.budget_tracker"), \
             patch("src.core.bedrock.token_counter"), \
             patch("src.core.bedrock.estimated_cost"), \
             patch("src.core.bedrock.llm_duration"), \
             patch("src.core.bedrock.prompt_size_tokens"), \
             patch("src.core.bedrock.compute_cost", return_value=0.0):
            bc._converse_sync(
                messages=[{"role": "user", "content": [{"text": "t"}]}],
                system_prompt="sys",
            )

    call_kwargs = mock_boto3_client.converse.call_args[1]
    assert "guardrailConfig" not in call_kwargs


@pytest.mark.asyncio
async def test_converse_no_guardrail_config_when_id_none(bedrock_client, mock_boto3_client):
    """When guardrail_id is None, guardrailConfig is NOT passed even if enabled."""
    mock_boto3_client.converse.return_value = _converse_response()

    with patch("src.core.bedrock.settings") as mock_settings:
        mock_settings.guardrail_enabled = True
        mock_settings.guardrail_id = None
        mock_settings.guardrail_version = "DRAFT"

        from src.core.bedrock import BedrockClient
        bc = BedrockClient()
        bc.client = mock_boto3_client

        with patch("src.core.bedrock.resolve_model", return_value="model-id"), \
             patch("src.core.bedrock.guardrail"), \
             patch("src.core.bedrock.budget_tracker"), \
             patch("src.core.bedrock.token_counter"), \
             patch("src.core.bedrock.estimated_cost"), \
             patch("src.core.bedrock.llm_duration"), \
             patch("src.core.bedrock.prompt_size_tokens"), \
             patch("src.core.bedrock.compute_cost", return_value=0.0):
            bc._converse_sync(
                messages=[{"role": "user", "content": [{"text": "t"}]}],
                system_prompt="sys",
            )

    call_kwargs = mock_boto3_client.converse.call_args[1]
    assert "guardrailConfig" not in call_kwargs


# ---------------------------------------------------------------------------
# (6) tool_config passed through when provided, absent when None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_tool_config_passed_when_provided(bedrock_client, mock_boto3_client):
    """tool_config dict is forwarded as toolConfig in the API call."""
    mock_boto3_client.converse.return_value = _converse_response()

    tool_cfg = {
        "tools": [{"toolSpec": {"name": "my_tool", "description": "does stuff",
                                "inputSchema": {"json": {"type": "object"}}}}]
    }

    await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "use tool"}]}],
        system_prompt="sys",
        tool_config=tool_cfg,
    )

    call_kwargs = mock_boto3_client.converse.call_args[1]
    assert "toolConfig" in call_kwargs
    assert call_kwargs["toolConfig"] == tool_cfg


@pytest.mark.asyncio
async def test_converse_tool_config_absent_when_none(bedrock_client, mock_boto3_client):
    """When tool_config is None, toolConfig key is NOT in the API call."""
    mock_boto3_client.converse.return_value = _converse_response()

    await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "no tools"}]}],
        system_prompt="sys",
        tool_config=None,
    )

    call_kwargs = mock_boto3_client.converse.call_args[1]
    assert "toolConfig" not in call_kwargs


# ---------------------------------------------------------------------------
# (7) Error mapping on a non-retryable exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_non_retryable_error_raises_immediately(bedrock_client, mock_boto3_client):
    """Non-retryable ClientError (e.g. ValidationException) raises without retry."""
    validation_err = _make_client_error("ValidationException", "Invalid request")
    mock_boto3_client.converse.side_effect = validation_err

    with patch("src.core.bedrock.time.sleep") as mock_sleep:
        with pytest.raises(ClientError) as exc_info:
            await bedrock_client.converse(
                messages=[{"role": "user", "content": [{"text": "bad"}]}],
                system_prompt="sys",
            )

    assert exc_info.value.response["Error"]["Code"] == "ValidationException"
    # No sleep = no retry
    mock_sleep.assert_not_called()
    # Only 1 call to converse (no retries)
    assert mock_boto3_client.converse.call_count == 1


@pytest.mark.asyncio
async def test_converse_access_denied_raises_immediately(bedrock_client, mock_boto3_client):
    """AccessDeniedException raises without retry."""
    err = _make_client_error("AccessDeniedException", "Not authorized")
    mock_boto3_client.converse.side_effect = err

    with patch("src.core.bedrock.time.sleep") as mock_sleep:
        with pytest.raises(ClientError) as exc_info:
            await bedrock_client.converse(
                messages=[{"role": "user", "content": [{"text": "denied"}]}],
                system_prompt="sys",
            )

    assert exc_info.value.response["Error"]["Code"] == "AccessDeniedException"
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_converse_unexpected_exception_raises(bedrock_client, mock_boto3_client):
    """Non-ClientError exceptions propagate immediately."""
    mock_boto3_client.converse.side_effect = RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
        )


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_converse_circuit_breaker_open_raises(bedrock_client, mock_boto3_client):
    """When circuit breaker is open, converse raises immediately."""
    bedrock_client.circuit_breaker.can_execute = MagicMock(return_value=False)

    with pytest.raises(Exception, match="circuit breaker is OPEN"):
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
        )

    # converse API never called
    mock_boto3_client.converse.assert_not_called()


@pytest.mark.asyncio
async def test_converse_records_success_on_breaker(bedrock_client, mock_boto3_client):
    """Successful converse records success on circuit breaker."""
    mock_boto3_client.converse.return_value = _converse_response()
    bedrock_client.circuit_breaker.record_success = MagicMock()

    await bedrock_client.converse(
        messages=[{"role": "user", "content": [{"text": "q"}]}],
        system_prompt="sys",
    )

    bedrock_client.circuit_breaker.record_success.assert_called_once()


@pytest.mark.asyncio
async def test_converse_records_failure_on_breaker(bedrock_client, mock_boto3_client):
    """Failed converse records failure on circuit breaker."""
    mock_boto3_client.converse.side_effect = _make_client_error("ValidationException")
    bedrock_client.circuit_breaker.record_failure = MagicMock()

    with pytest.raises(ClientError):
        await bedrock_client.converse(
            messages=[{"role": "user", "content": [{"text": "q"}]}],
            system_prompt="sys",
        )

    bedrock_client.circuit_breaker.record_failure.assert_called_once()


@pytest.mark.asyncio
async def test_converse_guardrail_blocked_does_not_trip_breaker(bedrock_client, mock_boto3_client):
    """GuardrailBlockedError does not record a failure on the circuit breaker."""
    from src.core.guardrail import GuardrailBlockedError

    bedrock_client.circuit_breaker.record_failure = MagicMock()

    # GuardrailBlockedError can come from the pre-call guardrail.apply inside _converse_sync
    with patch("src.core.bedrock.guardrail") as mock_gr:
        mock_gr.apply.side_effect = GuardrailBlockedError("policy refusal", "INPUT")
        with pytest.raises(GuardrailBlockedError):
            await bedrock_client.converse(
                messages=[{"role": "user", "content": [{"text": "bad input"}]}],
                system_prompt="sys",
            )

    bedrock_client.circuit_breaker.record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# Static helper coverage
# ---------------------------------------------------------------------------

class TestExtractConverseUserText:
    """Unit tests for _extract_converse_user_text static method."""

    def test_extracts_text_from_user_messages(self):
        from src.core.bedrock import BedrockClient
        messages = [
            {"role": "user", "content": [{"text": "hello"}, {"text": "world"}]},
            {"role": "assistant", "content": [{"text": "ignored"}]},
            {"role": "user", "content": [{"text": "second turn"}]},
        ]
        result = BedrockClient._extract_converse_user_text(messages)
        assert "hello" in result
        assert "world" in result
        assert "second turn" in result
        assert "ignored" not in result

    def test_ignores_non_text_blocks(self):
        from src.core.bedrock import BedrockClient
        messages = [
            {"role": "user", "content": [
                {"text": "real text"},
                {"toolResult": {"toolUseId": "x", "content": [{"text": "tool out"}]}},
            ]},
        ]
        result = BedrockClient._extract_converse_user_text(messages)
        assert "real text" in result
        # toolResult blocks don't have a top-level "text" key → not extracted
        assert "tool out" not in result

    def test_empty_messages_returns_empty(self):
        from src.core.bedrock import BedrockClient
        assert BedrockClient._extract_converse_user_text([]) == ""

    def test_no_user_messages_returns_empty(self):
        from src.core.bedrock import BedrockClient
        messages = [{"role": "assistant", "content": [{"text": "only assistant"}]}]
        assert BedrockClient._extract_converse_user_text(messages) == ""


class TestParseConverseContent:
    """Unit tests for _parse_converse_content static method."""

    def test_text_block_normalized(self):
        from src.core.bedrock import BedrockClient
        raw = [{"text": "hello"}]
        result = BedrockClient._parse_converse_content(raw)
        assert result == [{"type": "text", "text": "hello"}]

    def test_tool_use_block_normalized(self):
        from src.core.bedrock import BedrockClient
        raw = [{"toolUse": {"toolUseId": "id-1", "name": "fn", "input": {"a": 1}}}]
        result = BedrockClient._parse_converse_content(raw)
        assert result == [{"type": "tool_use", "toolUseId": "id-1", "name": "fn", "input": {"a": 1}}]

    def test_unknown_block_type(self):
        from src.core.bedrock import BedrockClient
        raw = [{"image": {"format": "png", "source": "base64data"}}]
        result = BedrockClient._parse_converse_content(raw)
        assert result == [{"type": "unknown", "raw": {"image": {"format": "png", "source": "base64data"}}}]

    def test_empty_input_defaults(self):
        from src.core.bedrock import BedrockClient
        raw = [{"toolUse": {"toolUseId": "", "name": "", "input": {}}}]
        result = BedrockClient._parse_converse_content(raw)
        assert result[0]["toolUseId"] == ""
        assert result[0]["name"] == ""
        assert result[0]["input"] == {}

    def test_mixed_blocks(self):
        from src.core.bedrock import BedrockClient
        raw = [
            {"text": "thinking..."},
            {"toolUse": {"toolUseId": "t1", "name": "calc", "input": {"x": 2}}},
            {"unknownType": "data"},
        ]
        result = BedrockClient._parse_converse_content(raw)
        assert len(result) == 3
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "tool_use"
        assert result[2]["type"] == "unknown"
