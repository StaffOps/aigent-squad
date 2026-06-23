"""Tests for src/core/internal_auth.py and supervisor /internal/* routes (spec 31).

Validates:
- require_internal_token: accepts matching X-Supervisor-Token, rejects otherwise,
  fail-closed when supervisor_internal_token is unset.
- supervisor /internal/process: requires token (401 without), maps
  GuardrailBlockedError→403.
- supervisor /internal/agents: requires token.
- supervisor public /query and /v1/* are GONE (no longer in routes).
"""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


# ─── require_internal_token unit tests ───────────────────────────────


class TestRequireInternalToken:
    def test_accepts_matching_token(self):
        with patch("src.core.internal_auth.settings") as mock_settings:
            mock_settings.supervisor_internal_token = "secret123"
            from src.core.internal_auth import require_internal_token
            # Should not raise
            require_internal_token(x_supervisor_token="secret123")

    def test_rejects_wrong_token(self):
        with patch("src.core.internal_auth.settings") as mock_settings:
            mock_settings.supervisor_internal_token = "secret123"
            from src.core.internal_auth import require_internal_token
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                require_internal_token(x_supervisor_token="wrong")
            assert exc_info.value.status_code == 401

    def test_rejects_empty_token(self):
        with patch("src.core.internal_auth.settings") as mock_settings:
            mock_settings.supervisor_internal_token = "secret123"
            from src.core.internal_auth import require_internal_token
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                require_internal_token(x_supervisor_token="")
            assert exc_info.value.status_code == 401

    def test_fail_closed_when_unset(self):
        """When supervisor_internal_token is unset, deny all (fail-closed)."""
        with patch("src.core.internal_auth.settings") as mock_settings:
            mock_settings.supervisor_internal_token = None
            from src.core.internal_auth import require_internal_token
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                require_internal_token(x_supervisor_token="anything")
            assert exc_info.value.status_code == 401

    def test_fail_closed_when_empty_string(self):
        with patch("src.core.internal_auth.settings") as mock_settings:
            mock_settings.supervisor_internal_token = ""
            from src.core.internal_auth import require_internal_token
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                require_internal_token(x_supervisor_token="")
            assert exc_info.value.status_code == 401


# ─── Supervisor /internal/* routes (integration) ─────────────────────


@pytest.fixture
def sup_client():
    """TestClient for the supervisor app."""
    from src.supervisor.server import app
    return TestClient(app)


class TestSupervisorInternalProcess:
    def test_401_without_token(self, sup_client):
        resp = sup_client.post("/internal/process", json={
            "user_input": "hi", "user_id": "u", "session_id": "s"
        })
        assert resp.status_code == 401

    def test_401_wrong_token(self, sup_client):
        resp = sup_client.post(
            "/internal/process",
            json={"user_input": "hi", "user_id": "u", "session_id": "s"},
            headers={"X-Supervisor-Token": "wrong"},
        )
        assert resp.status_code == 401

    def test_403_guardrail_blocked(self, sup_client):
        """GuardrailBlockedError maps to HTTP 403."""
        from src.core.guardrail import GuardrailBlockedError
        with patch("src.core.internal_auth.settings") as mock_s, \
             patch("src.supervisor.server.supervisor") as mock_sup:
            mock_s.supervisor_internal_token = "sup-token"
            mock_sup.process_request = AsyncMock(
                side_effect=GuardrailBlockedError("blocked", "INPUT")
            )
            resp = sup_client.post(
                "/internal/process",
                json={"user_input": "hi", "user_id": "u", "session_id": "s"},
                headers={"X-Supervisor-Token": "sup-token"},
            )
            assert resp.status_code == 403


class TestSupervisorInternalAgents:
    def test_401_without_token(self, sup_client):
        resp = sup_client.get("/internal/agents")
        assert resp.status_code == 401


class TestSupervisorOldRoutesGone:
    """The public /query and /v1/* routes moved to the gateway — they should NOT exist on the supervisor."""

    def test_no_public_query(self, sup_client):
        resp = sup_client.post("/query", json={
            "user_input": "x", "user_id": "u", "session_id": "s"
        })
        # Route doesn't exist — 404 (path not found) or 405 (method not allowed)
        assert resp.status_code in (404, 405)

    def test_no_public_v1_chat_completions(self, sup_client):
        resp = sup_client.post("/v1/chat/completions", json={
            "model": "aigent-squad",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code in (404, 405)

    def test_no_public_v1_models(self, sup_client):
        resp = sup_client.get("/v1/models")
        assert resp.status_code in (404, 405)
