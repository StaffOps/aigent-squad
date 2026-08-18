"""Tests for the REAL RISK uncovered paths in server.py and supervisor_client.py.

Rank 1: server.py:116-154 — streaming endpoint (guardrail, None-fallback, success)
Rank 2: server.py:50-55   — lifespan startup validation (HC5 fail-fast gate)
Rank 3: supervisor_client.py:115-131 — streaming transport error handling

Conventions follow test_supervisor_server_errors.py and test_gateway_client.py:
 - reload-under-patch pattern for server.py tests
 - respx for supervisor_client tests
 - autouse fixture clears dependency_overrides (no leak)
"""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════
# Shared teardown — clear dependency overrides to avoid leaking into
# other tests under pytest-randomly (same pattern as test_supervisor_server_errors.py)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clear_supervisor_overrides():
    """Undo auth bypass so it can't leak across tests."""
    yield
    try:
        import src.supervisor.server as _srv
        _srv.app.dependency_overrides.clear()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# HELPER: Build a TestClient with mocked supervisor (same pattern as
# test_supervisor_server_errors.py but with controllable process_request_streaming)
# ═══════════════════════════════════════════════════════════════════════


def _make_streaming_client(
    *,
    streaming_return=None,
    streaming_side_effect=None,
    process_return=None,
    process_side_effect=None,
):
    """Build TestClient with configurable process_request_streaming + process_request.

    Follows the reload-under-patch pattern from the existing test_supervisor_server_errors.
    """
    with (
        patch("src.supervisor.agent.AgentRegistry") as mock_ar,
        patch("src.supervisor.agent.SkillRegistry"),
        patch("src.supervisor.agent.GenericAgent"),
        patch("src.supervisor.agent.create_adapters", return_value=[]),
        patch("src.core.bedrock.boto3"),
        patch("src.core.state_store.boto3"),
        patch("src.core.cache.redis.Redis"),
        patch("src.core.classifier.bedrock"),
        patch("otel_helper.setup_telemetry"),
        patch("otel_helper.get_tracer", return_value=MagicMock()),
        patch("src.supervisor.server.kb_store") as mock_kb,
        patch("src.supervisor.server._health_redis"),
        patch("src.supervisor.server._health_dynamodb_table"),
        patch("src.supervisor.server._checker"),
    ):
        mock_kb.connect = AsyncMock()
        mock_kb.close = AsyncMock()

        mock_registry = MagicMock()
        mock_registry.agent_names.return_value = ["mock-agent"]
        mock_ar.return_value = mock_registry
        mock_registry.discover.return_value = None

        import src.supervisor.agent as agent_mod
        importlib.reload(agent_mod)

        import src.supervisor.server as srv
        importlib.reload(srv)

        # Configure process_request_streaming
        srv.supervisor.process_request_streaming = AsyncMock(
            return_value=streaming_return,
            side_effect=streaming_side_effect,
        )
        # Configure process_request (used in fallback)
        srv.supervisor.process_request = AsyncMock(
            return_value=process_return,
            side_effect=process_side_effect,
        )
        srv.supervisor.close = AsyncMock()

        # Bypass auth
        srv.app.dependency_overrides[srv.require_internal_token] = lambda: None

        client = TestClient(srv.app, raise_server_exceptions=False)
        return client, srv


_STREAM_PAYLOAD = {
    "user_input": "test streaming",
    "user_id": "u-stream",
    "session_id": "s-stream",
}


# ═══════════════════════════════════════════════════════════════════════
# RANK 1: Streaming endpoint behaviour (server.py:116-154)
# ═══════════════════════════════════════════════════════════════════════


class TestStreamingEndpointBehaviours:
    """Pin the /internal/process/stream endpoint's critical logic branches."""

    def test_guardrail_blocked_returns_403_not_500(self):
        """When streaming raises GuardrailBlockedError → HTTP 403 (not 500)."""
        from src.core.guardrail import GuardrailBlockedError

        client, _ = _make_streaming_client(
            streaming_side_effect=GuardrailBlockedError("blocked", "INPUT"),
        )
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 403
        assert "guardrail" in resp.json()["detail"].lower()

    def test_generic_exception_returns_500(self):
        """Unhandled error in streaming → HTTP 500, detail does not leak."""
        client, _ = _make_streaming_client(
            streaming_side_effect=RuntimeError("secret internal error"),
        )
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Internal server error"
        assert "secret" not in resp.text

    def test_none_fallback_calls_process_request_and_returns_sse(self):
        """When streaming returns None → falls back to process_request + sse_stream."""
        client, srv = _make_streaming_client(
            streaming_return=None,
            process_return={"agent": "obs", "response": "fallback answer", "confidence": 0.9},
        )
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # The non-streaming fallback process_request was called
        srv.supervisor.process_request.assert_awaited_once()
        # Body is SSE (contains data: lines)
        assert "data:" in resp.text
        assert "[DONE]" in resp.text

    def test_none_fallback_guardrail_blocked_returns_403(self):
        """When streaming returns None and the fallback process_request raises GuardrailBlockedError → 403."""
        from src.core.guardrail import GuardrailBlockedError

        client, _ = _make_streaming_client(
            streaming_return=None,
            process_side_effect=GuardrailBlockedError("blocked", "INPUT"),
        )
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 403
        assert "guardrail" in resp.json()["detail"].lower()

    def test_none_fallback_generic_error_returns_500(self):
        """When streaming returns None and fallback process_request raises → 500."""
        client, _ = _make_streaming_client(
            streaming_return=None,
            process_side_effect=RuntimeError("fallback crash"),
        )
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Internal server error"

    def test_success_returns_sse_stream_agentic(self):
        """When streaming returns a generator → SSE agentic stream."""

        async def _fake_gen():
            yield {"type": "text_delta", "content": "hello"}

        client, _ = _make_streaming_client(streaming_return=_fake_gen())
        resp = client.post("/internal/process/stream", json=_STREAM_PAYLOAD)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "data:" in resp.text


# ═══════════════════════════════════════════════════════════════════════
# RANK 2: Lifespan startup validation (server.py:50-55)
# ═══════════════════════════════════════════════════════════════════════


class TestLifespanValidation:
    """Lifespan calls validate_tier_models_at_startup and propagates errors."""

    def test_lifespan_calls_validate_tier_models_at_startup(self):
        """Boot calls validate_tier_models_at_startup — confirms HC5 gate is wired."""
        with (
            patch("src.supervisor.agent.AgentRegistry") as mock_ar,
            patch("src.supervisor.agent.SkillRegistry"),
            patch("src.supervisor.agent.GenericAgent"),
            patch("src.supervisor.agent.create_adapters", return_value=[]),
            patch("src.core.bedrock.boto3"),
            patch("src.core.state_store.boto3"),
            patch("src.core.cache.redis.Redis"),
            patch("src.core.classifier.bedrock"),
            patch("otel_helper.setup_telemetry"),
            patch("otel_helper.get_tracer", return_value=MagicMock()),
            patch("src.supervisor.server.kb_store") as mock_kb,
            patch("src.supervisor.server._health_redis"),
            patch("src.supervisor.server._health_dynamodb_table"),
            patch("src.supervisor.server._checker"),
            patch("src.core.model_tier.validate_tier_models_at_startup") as mock_validate,
        ):
            mock_kb.connect = AsyncMock()
            mock_kb.close = AsyncMock()
            mock_registry = MagicMock()
            mock_registry.agent_names.return_value = ["a"]
            mock_ar.return_value = mock_registry
            mock_registry.discover.return_value = None

            import src.supervisor.agent as agent_mod
            importlib.reload(agent_mod)
            import src.supervisor.server as srv
            importlib.reload(srv)

            srv.supervisor.close = AsyncMock()
            srv.app.dependency_overrides[srv.require_internal_token] = lambda: None

            # Exercise lifespan by making any request (TestClient handles lifespan)
            with TestClient(srv.app) as c:
                c.get("/healthz")

            # Validate was called during startup
            mock_validate.assert_called_once()

    def test_lifespan_propagates_validation_error(self):
        """If validate_tier_models_at_startup raises, the app fails to start."""
        with (
            patch("src.supervisor.agent.AgentRegistry") as mock_ar,
            patch("src.supervisor.agent.SkillRegistry"),
            patch("src.supervisor.agent.GenericAgent"),
            patch("src.supervisor.agent.create_adapters", return_value=[]),
            patch("src.core.bedrock.boto3"),
            patch("src.core.state_store.boto3"),
            patch("src.core.cache.redis.Redis"),
            patch("src.core.classifier.bedrock"),
            patch("otel_helper.setup_telemetry"),
            patch("otel_helper.get_tracer", return_value=MagicMock()),
            patch("src.supervisor.server.kb_store") as mock_kb,
            patch("src.supervisor.server._health_redis"),
            patch("src.supervisor.server._health_dynamodb_table"),
            patch("src.supervisor.server._checker"),
            patch(
                "src.core.model_tier.validate_tier_models_at_startup",
                side_effect=RuntimeError("BEDROCK_TIER_FAST_MODEL_ID is empty"),
            ),
        ):
            mock_kb.connect = AsyncMock()
            mock_kb.close = AsyncMock()
            mock_registry = MagicMock()
            mock_registry.agent_names.return_value = ["a"]
            mock_ar.return_value = mock_registry
            mock_registry.discover.return_value = None

            import src.supervisor.agent as agent_mod
            importlib.reload(agent_mod)
            import src.supervisor.server as srv
            importlib.reload(srv)

            srv.supervisor.close = AsyncMock()

            # Lifespan should propagate the error — TestClient wraps it
            with pytest.raises(RuntimeError, match="BEDROCK_TIER_FAST"):
                with TestClient(srv.app):
                    pass  # pragma: no cover — never reached

    # NOTE: test_lifespan_calls_kb_store_connect omitted — kb_store.connect()
    # is a sequential no-branch line immediately after validate_tier_models_at_startup().
    # The test above proves the lifespan body executes; if validate runs, connect
    # runs too (no conditional between them). Patching the singleton's method
    # through the reload-under-patch pattern is fragile (module re-imports the
    # real singleton) and not worth the complexity for a straight-line call.


# ═══════════════════════════════════════════════════════════════════════
# RANK 3: SupervisorClient.process_stream() (supervisor_client.py:115-131)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def stream_client():
    return __import__(
        "src.gateway.supervisor_client", fromlist=["SupervisorClient"]
    ).SupervisorClient(
        base_url="http://supervisor:8001",
        token="test-token",
        max_connections=10,
        timeout=5.0,
    )


class TestProcessStreamTransport:
    """Pin SupervisorClient.process_stream() error and happy path behaviour."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_transport_error_raises_supervisor_unavailable(self, stream_client):
        """httpx.HTTPError during streaming → SupervisorUnavailableError."""
        from src.gateway.supervisor_client import SupervisorUnavailableError

        respx.post("http://supervisor:8001/internal/process/stream").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with pytest.raises(SupervisorUnavailableError):
            await stream_client.process_stream("hi", "u1", "s1")

    @pytest.mark.asyncio
    @respx.mock
    async def test_4xx_raises_http_status_error(self, stream_client):
        """Supervisor returns 403 → raises HTTPStatusError after reading body."""
        respx.post("http://supervisor:8001/internal/process/stream").mock(
            return_value=httpx.Response(403, json={"detail": "blocked"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await stream_client.process_stream("hi", "u1", "s1")
        assert exc_info.value.response.status_code == 403

    @pytest.mark.asyncio
    @respx.mock
    async def test_5xx_raises_http_status_error(self, stream_client):
        """Supervisor returns 500 → raises HTTPStatusError."""
        respx.post("http://supervisor:8001/internal/process/stream").mock(
            return_value=httpx.Response(500, json={"detail": "crash"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await stream_client.process_stream("hi", "u1", "s1")
        assert exc_info.value.response.status_code == 500

    @pytest.mark.asyncio
    @respx.mock
    async def test_success_returns_response_for_iteration(self, stream_client):
        """Successful 200 → returns httpx.Response object for SSE iteration."""
        respx.post("http://supervisor:8001/internal/process/stream").mock(
            return_value=httpx.Response(200, text="data: hello\n\n")
        )
        resp = await stream_client.process_stream("hi", "u1", "s1")
        assert isinstance(resp, httpx.Response)
        assert resp.status_code == 200
