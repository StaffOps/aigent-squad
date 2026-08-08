"""Tests for src/gateway/main.py (spec 31, L1+L2).

Tests the gateway HTTP contract: /query, /v1/chat/completions, /v1/models,
/jobs/{id}/cancel, /healthz, /ready. Uses FastAPI TestClient with mocked
supervisor_client and worker_pool to test the routing/error-mapping layer.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from fastapi.testclient import TestClient

from src.gateway.auth import require_edge_auth
from src.gateway.main import app


@pytest.fixture(autouse=True)
def _bypass_edge_auth():
    """Override the auth dependency so all requests pass through."""
    app.dependency_overrides[require_edge_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


# ─── /healthz ────────────────────────────────────────────────────────


class TestHealthz:
    def test_healthz_returns_200(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_legacy_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ─── /metrics (otel-helper v0.2.0+ Prometheus scrape endpoint) ────────


class TestMetrics:
    def test_metrics_mounted_and_reachable(self, client):
        """Unauthenticated by design (infra-level scrape target, same class
        as /healthz + /ready) — no edge-auth override needed for this one."""
        resp = client.get("/metrics")
        assert resp.status_code == 200


# ─── /ready ──────────────────────────────────────────────────────────


class TestReady:
    def test_ready_returns_200(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_ready_200_even_if_redis_ping_fails(self, client):
        """Ready is fail-open on Redis — reports degraded but stays 200."""
        with patch("src.gateway.main._redis") as mock_redis:
            mock_redis.ping = AsyncMock(side_effect=Exception("redis down"))
            resp = client.get("/ready")
            assert resp.status_code == 200


# ─── /query ──────────────────────────────────────────────────────────


class TestQuery:
    def test_query_503_when_pool_full(self, client):
        with patch("src.gateway.main.worker_pool") as mock_pool:
            mock_pool.has_capacity.return_value = False
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"
            assert "Retry-After" in resp.headers

    def test_query_503_when_supervisor_not_ready(self, client):
        with patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=False)
            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"
            assert "Retry-After" in resp.headers

    def test_query_200_forwards_result(self, client):
        expected = {"agent": "aws", "response": "3 instances", "confidence": 0.9}
        with patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value=expected)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/query", json={
                "user_input": "list ec2", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 200
            assert resp.json() == expected

    def test_query_503_pool_full_during_submit(self, client):
        """PoolFullError raised during submit (race) still returns 503."""
        from src.gateway.worker_pool import PoolFullError as PFE
        with patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_pool.submit = AsyncMock(side_effect=PFE("full"))

            resp = client.post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"

    def test_query_503_supervisor_unavailable_during_stream(self, client):
        """SupervisorUnavailableError mid-stream returns 503."""
        from src.gateway.supervisor_client import SupervisorUnavailableError as SUE
        with patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)
            mock_sc.process = AsyncMock(side_effect=SUE("down"))

            resp = client.post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"

    def test_query_forward_error_non_http(self, client):
        """Non-HTTP exceptions in the stream trigger _forward_error which re-raises → 500."""
        with patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)
            mock_sc.process = AsyncMock(side_effect=RuntimeError("boom"))

            with pytest.raises(RuntimeError):
                client.post("/query", json={
                    "user_input": "x", "user_id": "u", "session_id": "s"
                })


# ─── /v1/chat/completions ────────────────────────────────────────────


class TestChatCompletions:
    def test_unknown_model_is_auto_routed_not_rejected(self, client):
        """G-1: an unrecognized model id must NOT be rejected at the gateway.

        resolve_target() was deliberately made permissive (unknown id ->
        auto-route, never raises) so external clients that cannot set an
        arbitrary model id — the Grafana LLM app sends "base"/"gpt-4o" — work
        without an HTTP error. This asserts the ENDPOINT honors that; the
        resolve_target contract itself is covered by
        tests/test_grafana_plugin_g1_g2.py and tests/test_openai_compat.py.

        The pre-G-1 contract (404 + invalid_request_error) is gone; asserting it
        here is what made this test stale.
        """
        with patch("src.gateway.main._agent_names", ["aws", "k8s"]):
            resp = client.post("/v1/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            })
            # Must not be rejected as an unknown model. (Downstream is not
            # mocked here, so the forwarded call surfaces as 503 — the point is
            # that it got PAST model resolution.)
            assert resp.status_code != 404

    def test_403_guardrail_blocked(self, client):
        """Supervisor 403 (guardrail) propagates as 403 guardrail_blocked."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        mock_request = MagicMock()
        exc = httpx.HTTPStatusError(
            "403", request=mock_request, response=mock_response
        )

        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hack it"}],
            })
            assert resp.status_code == 403
            assert resp.json()["error"]["type"] == "guardrail_blocked"

    def test_non_stream_returns_completion(self, client):
        result = {"agent": "aws", "response": "hello", "confidence": 0.9}
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value=result)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["object"] == "chat.completion"
            assert body["choices"][0]["message"]["content"] == "hello"

    def test_stream_returns_sse(self, client):
        result = {"agent": "aws", "response": "hello", "confidence": 0.9}
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value=result)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            assert "data: " in resp.text
            assert "[DONE]" in resp.text

    def test_503_backend_unavailable(self, client):
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=False)
            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"

    def test_forward_error_non_403_status(self, client):
        """Non-403 HTTPStatusError from supervisor maps to that status code."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)

        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()

            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 500
            assert resp.json()["error"]["type"] == "backend_error"

    def test_503_when_pool_full(self, client):
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool:
            mock_pool.has_capacity.return_value = False
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert "Retry-After" in resp.headers


# ─── /v1/models ──────────────────────────────────────────────────────


class TestModels:
    def test_lists_aigent_squad_plus_per_agent(self, client):
        with patch("src.gateway.main._agent_names", ["aws", "k8s"]):
            resp = client.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "aigent-squad" in ids
            assert "aigent-squad-aws" in ids
            assert "aigent-squad-k8s" in ids


# ─── /jobs/{id}/cancel ───────────────────────────────────────────────


class TestCancelJob:
    def test_cancel_active_returns_202(self, client):
        with patch("src.gateway.main.worker_pool") as mock_pool:
            mock_pool.cancel = AsyncMock(return_value=True)
            resp = client.post("/jobs/abc123/cancel")
            assert resp.status_code == 202
            assert resp.json()["status"] == "cancelling"

    def test_cancel_not_found_returns_404(self, client):
        with patch("src.gateway.main.worker_pool") as mock_pool:
            mock_pool.cancel = AsyncMock(return_value=False)
            resp = client.post("/jobs/nonexist/cancel")
            assert resp.status_code == 404


# ─── Retry-After header on 503 ──────────────────────────────────────


class TestRetryAfter:
    def test_retry_after_present_on_503(self, client):
        with patch("src.gateway.main.worker_pool") as mock_pool:
            mock_pool.has_capacity.return_value = False
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            resp = client.post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 503
            assert "Retry-After" in resp.headers
            assert int(resp.headers["Retry-After"]) >= 1


# ─── Edge auth (src/gateway/auth.py) ────────────────────────────────


class TestEdgeAuth:
    """Test require_edge_auth directly by patching module-level globals."""

    def test_accepts_valid_internal_token(self):
        import src.gateway.auth as auth_mod
        with patch.object(auth_mod, "_EXPECTED_TOKEN", "valid-token"), \
             patch.object(auth_mod, "_API_KEYS", set()):
            # Should not raise
            auth_mod.require_edge_auth(x_internal_token="valid-token", x_api_key=None)

    def test_accepts_allowlisted_api_key(self):
        import src.gateway.auth as auth_mod
        with patch.object(auth_mod, "_EXPECTED_TOKEN", ""), \
             patch.object(auth_mod, "_API_KEYS", {"key-abc"}):
            auth_mod.require_edge_auth(x_internal_token="", x_api_key="key-abc")

    def test_rejects_when_neither_matches(self):
        import src.gateway.auth as auth_mod
        from fastapi import HTTPException
        with patch.object(auth_mod, "_EXPECTED_TOKEN", "tok"), \
             patch.object(auth_mod, "_API_KEYS", {"k1"}):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_edge_auth(x_internal_token="wrong", x_api_key="wrong")
            assert exc_info.value.status_code == 401

    def test_fail_closed_when_no_secret_configured(self):
        """Both _EXPECTED_TOKEN and _API_KEYS empty → deny (fail-closed)."""
        import src.gateway.auth as auth_mod
        from fastapi import HTTPException
        with patch.object(auth_mod, "_EXPECTED_TOKEN", ""), \
             patch.object(auth_mod, "_API_KEYS", set()):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_edge_auth(x_internal_token="", x_api_key=None)
            assert exc_info.value.status_code == 401

    def test_rejects_empty_token_even_if_configured(self):
        """Even with a configured token, empty header is rejected."""
        import src.gateway.auth as auth_mod
        from fastapi import HTTPException
        with patch.object(auth_mod, "_EXPECTED_TOKEN", "secret"), \
             patch.object(auth_mod, "_API_KEYS", set()):
            with pytest.raises(HTTPException) as exc_info:
                auth_mod.require_edge_auth(x_internal_token="", x_api_key=None)
            assert exc_info.value.status_code == 401
