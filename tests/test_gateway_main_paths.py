"""Additional coverage tests for src/gateway/main.py.

Covers lines missed due to async-coverage tracking issues when admission is not
mocked. All tests explicitly patch admission + settings so that the downstream
branches (pool capacity, supervisor ready, exception handlers, streaming) are
properly traced by coverage.py.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from fastapi.testclient import TestClient

from src.gateway.auth import require_edge_auth
import src.gateway.main as gw


@pytest.fixture(autouse=True)
def _bypass_edge_auth():
    gw.app.dependency_overrides[require_edge_auth] = lambda: None
    yield
    gw.app.dependency_overrides.clear()


def _admit():
    """Patch admission to allow (returns context managers for use in `with`)."""
    return (
        patch.object(gw, "settings", **{"rate_budget_enabled": True}),
        patch.object(gw, "admission", **{
            "check_rate": AsyncMock(return_value=(True, 55)),
            "check_budget": AsyncMock(return_value=(True, 40.0)),
        }),
    )


def _fake_submit_factory():
    """Create a fake_submit that iterates the generator through the pool."""
    async def fake_submit(job_id, stream):
        async def wrapper():
            async for item in stream:
                yield item
        return wrapper()
    return AsyncMock(side_effect=fake_submit)


# ─── Lifespan (lines 71-72) ─────────────────────────────────────────


class TestLifespan:
    def test_lifespan_aclose(self):
        """Lifespan shutdown awaits supervisor_client.aclose().

        The whole client is an AsyncMock, not a MagicMock with one async
        attribute: startup ALSO awaits `list_agents()` whenever
        GATEWAY_KEY_AGENT_MAP is non-empty, and that map is a module-level
        global in gateway/auth.py populated at import time. The G-5 tests
        reload that module under monkeypatch.setenv — monkeypatch restores the
        env var but cannot undo a reload, so the global stays populated and
        leaks into whatever runs next. With a bare MagicMock this test then
        died on `await list_agents()` in the full suite while passing alone.
        AsyncMock makes it order-independent. (Leak tracked as F-012.)
        """
        with patch.object(gw, "supervisor_client", new_callable=AsyncMock) as mock_sc:
            with TestClient(gw.app) as c:
                resp = c.get("/healthz")
                assert resp.status_code == 200
            mock_sc.aclose.assert_awaited_once()


# ─── /query: pool full (line 141) ───────────────────────────────────


class TestQueryPoolFull:
    def test_503_service_overloaded(self):
        s_patch, a_patch = _admit()
        with s_patch, a_patch, patch.object(gw, "worker_pool") as mock_pool:
            mock_pool.has_capacity.return_value = False
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"


# ─── /query: supervisor not ready (line 143) ────────────────────────


class TestQuerySupervisorNotReady:
    def test_503_backend_unavailable(self):
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=False)
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"


# ─── /query: exception branches (lines 162-167) ─────────────────────


class TestQueryExceptions:
    def test_pool_full_error_during_submit(self):
        from src.gateway.worker_pool import PoolFullError
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_pool.submit = AsyncMock(side_effect=PoolFullError("full"))
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"

    def test_supervisor_unavailable_during_stream(self):
        from src.gateway.supervisor_client import SupervisorUnavailableError
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=SupervisorUnavailableError("down"))
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"

    def test_forward_error_403_guardrail(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        exc = httpx.HTTPStatusError("403", request=MagicMock(), response=mock_response)
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 403
            assert resp.json()["error"]["type"] == "guardrail_blocked"

    def test_forward_error_non_403(self):
        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        exc = httpx.HTTPStatusError("502", request=MagicMock(), response=mock_response)
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "x", "user_id": "u", "session_id": "s"
            })
            assert resp.status_code == 502
            assert resp.json()["error"]["type"] == "backend_error"


# ─── /v1/chat/completions: pool full (line 197) ─────────────────────


class TestChatPoolFull:
    def test_503_service_overloaded(self):
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool:
            mock_pool.has_capacity.return_value = False
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"


# ─── /v1/chat/completions: supervisor not ready (line 199) ──────────


class TestChatSupervisorNotReady:
    def test_503_backend_unavailable(self):
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=False)
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"


# ─── /v1/chat/completions: exception branches (lines 218-223) ───────


class TestChatExceptions:
    def test_pool_full_error_during_submit(self):
        from src.gateway.worker_pool import PoolFullError
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 20
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_pool.submit = AsyncMock(side_effect=PoolFullError("full"))
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "service_overloaded"

    def test_supervisor_unavailable_during_stream(self):
        from src.gateway.supervisor_client import SupervisorUnavailableError
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=SupervisorUnavailableError("down"))
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "backend_unavailable"

    def test_forward_error_403(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        exc = httpx.HTTPStatusError("403", request=MagicMock(), response=mock_response)
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 403
            assert resp.json()["error"]["type"] == "guardrail_blocked"

    def test_forward_error_non_403(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = httpx.Headers()
        mock_response.stream = MagicMock(is_closed=True)
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 5
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(side_effect=exc)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 500
            assert resp.json()["error"]["type"] == "backend_error"


# ─── /v1/chat/completions: streaming (line 226) ─────────────────────


class TestChatStreaming:
    def test_stream_true_returns_sse(self):
        result = {"agent": "aws", "response": "hello", "confidence": 0.9}
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "_agent_names", ["aws"]), \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 2
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value=result)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]


# ─── /ready: redis ping success (line 266) ──────────────────────────


class TestReadyRedis:
    def test_redis_ping_ok(self):
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        with patch.object(gw, "_redis", mock_redis):
            resp = TestClient(gw.app).get("/ready")
            assert resp.status_code == 200
            assert resp.json()["checks"]["redis"]["ok"] is True

    def test_redis_ping_fails_still_200(self):
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("refused"))
        with patch.object(gw, "_redis", mock_redis):
            resp = TestClient(gw.app).get("/ready")
            assert resp.status_code == 200
            assert resp.json()["checks"]["redis"]["ok"] is False


# ─── _refresh_agents (lines 284-287) ────────────────────────────────


class TestRefreshAgents:
    def test_models_calls_list_agents_when_cache_empty(self):
        with patch.object(gw, "_agent_names", []), \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_sc.list_agents = AsyncMock(return_value=["aws", "finops"])
            resp = TestClient(gw.app).get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "aigent-squad-aws" in ids
            assert "aigent-squad-finops" in ids

    def test_models_empty_list_returns_base(self):
        with patch.object(gw, "_agent_names", []), \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_sc.list_agents = AsyncMock(return_value=[])
            resp = TestClient(gw.app).get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "aigent-squad" in ids


# ─── _check_admission allow-through (line 131) ──────────────────────


class TestAdmissionAllow:
    def test_query_passes_when_admitted(self):
        """Full allow path with rate_budget_enabled=True covers line 131 (return None)."""
        result = {"agent": "aws", "response": "ok", "confidence": 0.9}
        s_patch, a_patch = _admit()
        with s_patch, a_patch, \
             patch.object(gw, "worker_pool") as mock_pool, \
             patch.object(gw, "supervisor_client") as mock_sc:
            mock_pool.has_capacity.return_value = True
            mock_pool.active_count = 2
            mock_pool.max_capacity = 20
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value=result)
            mock_pool.submit = _fake_submit_factory()
            resp = TestClient(gw.app).post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 200
            assert resp.json() == result
