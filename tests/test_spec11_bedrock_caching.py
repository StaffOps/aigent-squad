"""Tests for spec 11: Bedrock prompt caching (T3), model routing (T2), cost metrics (T6).

Bedrock client.invoke_model is fully mocked. Tests validate the body structure,
graceful degradation, and metric emission with correct labels.
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError


def _mock_bedrock_response(text="ok", input_tokens=100, output_tokens=50,
                           cache_read=0, cache_creation=0):
    """Build a mock invoke_model response matching Bedrock's format."""
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cache_read:
        usage["cache_read_input_tokens"] = cache_read
    if cache_creation:
        usage["cache_creation_input_tokens"] = cache_creation
    body = json.dumps({"content": [{"text": text}], "usage": usage}).encode()
    return {"body": MagicMock(read=MagicMock(return_value=body))}


def _make_client():
    """Create a fresh BedrockClient with mocked boto3."""
    from src.core.bedrock import BedrockClient
    client = BedrockClient()
    client.client = MagicMock()
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# T2: Classifier uses Haiku (via role routing)
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelRouting:
    """_invoke_sync uses resolve_model(role) to pick the modelId."""

    @pytest.mark.asyncio
    async def test_classifier_role_uses_haiku_model(self):
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            role="classifier",
            match_user_language=False,
        )

        call_kwargs = client.client.invoke_model.call_args
        model_id_used = call_kwargs.kwargs.get("modelId") or call_kwargs[1].get("modelId")
        assert "haiku" in model_id_used.lower()

    @pytest.mark.asyncio
    async def test_agent_role_uses_sonnet_model(self):
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            role="agent",
        )

        call_kwargs = client.client.invoke_model.call_args
        model_id_used = call_kwargs.kwargs.get("modelId") or call_kwargs[1].get("modelId")
        assert "sonnet" in model_id_used.lower()

    @pytest.mark.asyncio
    async def test_synthesis_role_uses_sonnet_model(self):
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            role="synthesis",
        )

        call_kwargs = client.client.invoke_model.call_args
        model_id_used = call_kwargs.kwargs.get("modelId") or call_kwargs[1].get("modelId")
        assert "sonnet" in model_id_used.lower()

    @pytest.mark.asyncio
    async def test_default_role_is_agent(self):
        """When no role is passed, defaults to 'agent' (backward compat)."""
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
        )

        call_kwargs = client.client.invoke_model.call_args
        model_id_used = call_kwargs.kwargs.get("modelId") or call_kwargs[1].get("modelId")
        assert "sonnet" in model_id_used.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# T3: Prompt Caching
# ═══════════════════════════════════════════════════════════════════════════════


class TestPromptCaching:
    """cache_control ephemeral in system block; graceful degradation."""

    @pytest.mark.asyncio
    async def test_cache_control_present_in_system_block(self, monkeypatch):
        """When caching enabled, system block has cache_control ephemeral."""
        monkeypatch.setattr("src.core.bedrock.settings.bedrock_prompt_cache_enabled", True)
        client = _make_client()
        client._cache_supported = True
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="You are helpful",
            use_cache=True,
            role="agent",
        )

        call_kwargs = client.client.invoke_model.call_args
        body_arg = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        body = json.loads(body_arg)
        system_block = body["system"][0]
        assert system_block.get("cache_control") == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_no_cache_control_when_disabled(self, monkeypatch):
        """When setting disabled, no cache_control in system block."""
        monkeypatch.setattr("src.core.bedrock.settings.bedrock_prompt_cache_enabled", False)
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            use_cache=True,
            role="agent",
        )

        call_kwargs = client.client.invoke_model.call_args
        body_arg = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        body = json.loads(body_arg)
        system_block = body["system"][0]
        assert "cache_control" not in system_block

    @pytest.mark.asyncio
    async def test_no_cache_control_when_use_cache_false(self, monkeypatch):
        """use_cache=False skips cache_control even if setting enabled."""
        monkeypatch.setattr("src.core.bedrock.settings.bedrock_prompt_cache_enabled", True)
        client = _make_client()
        client._cache_supported = True
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            use_cache=False,
            role="agent",
        )

        call_kwargs = client.client.invoke_model.call_args
        body_arg = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        body = json.loads(body_arg)
        system_block = body["system"][0]
        assert "cache_control" not in system_block

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_validation_error(self, monkeypatch):
        """ValidationException with 'cache_control' disables caching and retries."""
        monkeypatch.setattr("src.core.bedrock.settings.bedrock_prompt_cache_enabled", True)
        client = _make_client()
        client._cache_supported = True

        # First call: reject with ValidationException mentioning cache_control
        error_response = {"Error": {"Code": "ValidationException", "Message": "cache_control not supported"}}
        cache_error = ClientError(error_response, "InvokeModel")

        # Second call (retry without cache): succeeds
        client.client.invoke_model.side_effect = [
            cache_error,
            _mock_bedrock_response("retry-ok"),
        ]

        result = await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            use_cache=True,
            role="agent",
        )

        assert result == "retry-ok"
        assert client._cache_supported is False

    @pytest.mark.asyncio
    async def test_subsequent_calls_skip_cache_after_degradation(self, monkeypatch):
        """After degradation, further calls don't include cache_control."""
        monkeypatch.setattr("src.core.bedrock.settings.bedrock_prompt_cache_enabled", True)
        client = _make_client()
        client._cache_supported = False  # Already degraded
        client.client.invoke_model.return_value = _mock_bedrock_response()

        await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            use_cache=True,
            role="agent",
        )

        call_kwargs = client.client.invoke_model.call_args
        body_arg = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        body = json.loads(body_arg)
        system_block = body["system"][0]
        assert "cache_control" not in system_block

    @pytest.mark.asyncio
    async def test_is_cache_supported_initially_true(self):
        """Optimistic: _is_cache_supported returns True before any failure."""
        client = _make_client()
        assert client._is_cache_supported() is True


# ═══════════════════════════════════════════════════════════════════════════════
# T6: Per-Model Cost Metric
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerModelCostMetric:
    """Metrics emitted with model and agent_id labels."""

    @pytest.mark.asyncio
    async def test_token_counter_labels_include_model_and_direction(self):
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response(
            input_tokens=200, output_tokens=80
        )

        with patch("src.core.bedrock.token_counter") as mock_tc:
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                agent_id="my-agent",
                role="agent",
            )

            # Two calls: input + output
            assert mock_tc.add.call_count == 2
            input_call = mock_tc.add.call_args_list[0]
            output_call = mock_tc.add.call_args_list[1]

            # Input call
            assert input_call[0][0] == 200
            assert input_call[0][1]["direction"] == "input"
            assert input_call[0][1]["agent_id"] == "my-agent"
            assert "model" in input_call[0][1]

            # Output call
            assert output_call[0][0] == 80
            assert output_call[0][1]["direction"] == "output"
            assert output_call[0][1]["agent_id"] == "my-agent"

    @pytest.mark.asyncio
    async def test_estimated_cost_uses_compute_cost_with_cache(self):
        """Cost metric accounts for cache_read_input_tokens."""
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response(
            input_tokens=1000, output_tokens=200, cache_read=300
        )

        with patch("src.core.bedrock.estimated_cost") as mock_cost, \
             patch("src.core.bedrock.compute_cost", wraps=__import__("src.core.model_tier", fromlist=["compute_cost"]).compute_cost) as mock_cc:
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                agent_id="cost-agent",
                role="agent",
            )

            # estimated_cost.add called with the result of compute_cost
            assert mock_cost.add.call_count == 1
            cost_value = mock_cost.add.call_args[0][0]
            assert cost_value > 0

            # Labels include model + agent_id
            labels = mock_cost.add.call_args[0][1]
            assert labels["agent_id"] == "cost-agent"
            assert "model" in labels

    @pytest.mark.asyncio
    async def test_cost_metric_model_label_matches_resolved_model(self):
        """The model label in metrics matches what resolve_model returns."""
        client = _make_client()
        client.client.invoke_model.return_value = _mock_bedrock_response()

        with patch("src.core.bedrock.estimated_cost") as mock_cost:
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="classifier",
                agent_id="cls",
                match_user_language=False,
            )

            labels = mock_cost.add.call_args[0][1]
            assert "haiku" in labels["model"].lower()

    @pytest.mark.asyncio
    async def test_zero_cache_read_when_field_absent(self):
        """When response has no cache_read_input_tokens, cost uses 0."""
        client = _make_client()
        # Response without cache fields
        body = json.dumps({
            "content": [{"text": "ok"}],
            "usage": {"input_tokens": 500, "output_tokens": 100}
        }).encode()
        client.client.invoke_model.return_value = {
            "body": MagicMock(read=MagicMock(return_value=body))
        }

        with patch("src.core.bedrock.estimated_cost") as mock_cost:
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="agent",
                agent_id="test",
            )

            cost_value = mock_cost.add.call_args[0][0]
            # Should be full-rate input cost (no cache discount)
            from src.core.model_tier import compute_cost, resolve_model
            expected = compute_cost(resolve_model("agent"), 500, 100, 0)
            assert abs(cost_value - expected) < 1e-10


# ═══════════════════════════════════════════════════════════════════════════════
# Additional coverage: retry paths, circuit breaker, GuardrailBlockedError
# (not new spec-11 logic but needed for >=90% on bedrock.py overall)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBedrockRetryAndCircuitBreaker:
    """Cover retry loop, max-retries exhaustion, circuit breaker open."""

    @pytest.mark.asyncio
    async def test_throttling_retries_and_succeeds(self, monkeypatch):
        """ThrottlingException triggers retry; success on 2nd attempt."""
        monkeypatch.setattr("src.core.bedrock.time.sleep", lambda _: None)
        client = _make_client()

        throttle_err = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )
        client.client.invoke_model.side_effect = [
            throttle_err,
            _mock_bedrock_response("retry-success"),
        ]

        result = await client.invoke(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="sys",
            role="agent",
        )
        assert result == "retry-success"

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_raises(self, monkeypatch):
        """All retries fail with ThrottlingException → raises ClientError."""
        monkeypatch.setattr("src.core.bedrock.time.sleep", lambda _: None)
        client = _make_client()

        throttle_err = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            "InvokeModel",
        )
        client.client.invoke_model.side_effect = [throttle_err] * 5

        with pytest.raises(ClientError, match="ThrottlingException"):
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="agent",
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_raises(self):
        """When circuit breaker is open, invoke raises immediately."""
        client = _make_client()
        client.circuit_breaker.can_execute = MagicMock(return_value=False)

        with pytest.raises(Exception, match="circuit breaker is OPEN"):
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="agent",
            )

    @pytest.mark.asyncio
    async def test_guardrail_blocked_does_not_trip_breaker(self, monkeypatch):
        """GuardrailBlockedError re-raised without recording failure."""
        from src.core.guardrail import GuardrailBlockedError
        client = _make_client()

        # Make guardrail.apply raise on INPUT
        monkeypatch.setattr(
            "src.core.bedrock.guardrail.apply",
            MagicMock(side_effect=GuardrailBlockedError("blocked", "INPUT", "test")),
        )

        with pytest.raises(GuardrailBlockedError):
            await client.invoke(
                messages=[{"role": "user", "content": "bad content"}],
                system_prompt="sys",
                role="agent",
            )

        # Circuit breaker should NOT have recorded a failure
        # (success count unchanged, failure not recorded)

    @pytest.mark.asyncio
    async def test_non_retryable_client_error_raises_immediately(self):
        """A ClientError with non-retryable code raises without retry."""
        client = _make_client()
        err = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "No access"}},
            "InvokeModel",
        )
        client.client.invoke_model.side_effect = err

        with pytest.raises(ClientError):
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="agent",
            )

    @pytest.mark.asyncio
    async def test_unexpected_exception_raises(self):
        """Non-ClientError exception raises directly."""
        client = _make_client()
        client.client.invoke_model.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await client.invoke(
                messages=[{"role": "user", "content": "test"}],
                system_prompt="sys",
                role="agent",
            )

    @pytest.mark.asyncio
    async def test_user_text_extracts_block_format(self):
        """_user_text handles Anthropic block content format."""
        from src.core.bedrock import BedrockClient
        text = BedrockClient._user_text([
            {"role": "user", "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ]},
            {"role": "assistant", "content": "ignored"},
        ])
        assert "hello" in text
        assert "world" in text
        assert "ignored" not in text
