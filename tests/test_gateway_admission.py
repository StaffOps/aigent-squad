"""Tests for admission wiring in src/gateway/main.py (spec 31 L3).

Validates the HTTP contract when admission guards deny: 429 rate_limited,
503 budget_exhausted, correct headers, and ordering (admission runs BEFORE
pool capacity / supervisor preflight).

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from src.gateway.auth import require_edge_auth
from src.gateway.main import app


@pytest.fixture(autouse=True)
def _bypass_edge_auth():
    app.dependency_overrides[require_edge_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


# ─── /query admission ────────────────────────────────────────────────


class TestQueryAdmission:
    def test_429_rate_limited(self, client):
        with patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(False, 0))
            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 429
            assert resp.json()["error"]["type"] == "rate_limited"
            assert resp.headers["X-RateLimit-Remaining"] == "0"
            # Supervisor must NOT be called
            mock_sc.process.assert_not_called()

    def test_503_budget_exhausted(self, client):
        with patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(True, 59))
            mock_adm.check_budget = AsyncMock(return_value=(False, 1.2345))
            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "budget_exhausted"
            assert "X-Budget-Remaining-USD" in resp.headers
            assert resp.headers["X-Budget-Remaining-USD"] == "1.2345"
            mock_sc.process.assert_not_called()

    def test_admission_skipped_when_disabled(self, client):
        """When settings.rate_budget_enabled=False, admission is bypassed."""
        with patch("src.gateway.main.settings") as mock_settings, \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc, \
             patch("src.gateway.main.admission") as mock_adm:
            mock_settings.rate_budget_enabled = False
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value={"agent": "a", "response": "ok", "confidence": 0.9})

            async def fake_submit(job_id, stream):
                async def wrapper():
                    async for item in stream:
                        yield item
                return wrapper()
            mock_pool.submit = AsyncMock(side_effect=fake_submit)

            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 200
            # Admission guards must not have been called
            mock_adm.check_rate.assert_not_called()

    def test_admission_before_pool_capacity(self, client):
        """Rate-denied request must NOT reach pool or supervisor."""
        with patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(False, 0))
            resp = client.post("/query", json={
                "user_input": "hi", "user_id": "u1", "session_id": "s1"
            })
            assert resp.status_code == 429
            mock_pool.has_capacity.assert_not_called()
            mock_sc.is_supervisor_ready.assert_not_called()
            mock_sc.process.assert_not_called()


# ─── /v1/chat/completions admission ─────────────────────────────────


class TestChatCompletionsAdmission:
    def test_429_rate_limited(self, client):
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(False, 0))
            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 429
            assert resp.json()["error"]["type"] == "rate_limited"
            assert resp.headers["X-RateLimit-Remaining"] == "0"
            mock_sc.process.assert_not_called()

    def test_503_budget_exhausted(self, client):
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(True, 50))
            mock_adm.check_budget = AsyncMock(return_value=(False, 0.5))
            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            assert resp.json()["error"]["type"] == "budget_exhausted"
            assert resp.headers["X-Budget-Remaining-USD"] == "0.5000"
            mock_sc.process.assert_not_called()

    def test_admission_skipped_when_disabled(self, client):
        with patch("src.gateway.main.settings") as mock_settings, \
             patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc, \
             patch("src.gateway.main.admission") as mock_adm:
            mock_settings.rate_budget_enabled = False
            mock_pool.has_capacity.return_value = True
            mock_sc.is_supervisor_ready = AsyncMock(return_value=True)
            mock_sc.process = AsyncMock(return_value={"agent": "a", "response": "ok", "confidence": 0.9})

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
            mock_adm.check_rate.assert_not_called()

    def test_admission_before_supervisor_preflight(self, client):
        """Budget-denied request must NOT reach supervisor."""
        with patch("src.gateway.main._agent_names", ["aws"]), \
             patch("src.gateway.main.admission") as mock_adm, \
             patch("src.gateway.main.worker_pool") as mock_pool, \
             patch("src.gateway.main.supervisor_client") as mock_sc:
            mock_adm.check_rate = AsyncMock(return_value=(True, 50))
            mock_adm.check_budget = AsyncMock(return_value=(False, 0.0))
            resp = client.post("/v1/chat/completions", json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 503
            mock_pool.has_capacity.assert_not_called()
            mock_sc.is_supervisor_ready.assert_not_called()
            mock_sc.process.assert_not_called()
