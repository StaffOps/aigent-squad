"""Tests for G-5: per-consumer key → default agent scoping (GATEWAY_KEY_AGENT_MAP).

Verifies:
  - Parsing of GATEWAY_KEY_AGENT_MAP env var into dict.
  - Auth accepts keys from the map (implicitly valid) and returns the default agent.
  - Internal token returns no default agent (full auto-route).
  - Plain API key (not in map) returns no default agent.
  - Explicit forced model (aigent-squad-<agent>) overrides consumer default.
  - Auto-route model + consumer default → applies consumer default.
  - Startup validation warns on unknown agent names.
"""
from unittest.mock import AsyncMock, patch

import pytest


# NOTE (F-016b): The autouse _restore_auth_module_state fixture (F-012) was
# removed. src/gateway/auth.py now reads env vars FRESH on every call — no
# module-level cache, no importlib.reload() needed. monkeypatch.setenv is
# sufficient for test isolation.


# ─── Unit tests: auth module parsing ────────────────────────────────


class TestKeyAgentMapParsing:
    """Test GATEWAY_KEY_AGENT_MAP parsing at module level."""

    def test_parse_valid_map(self, monkeypatch):
        """Multiple key=agent pairs parsed correctly."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "key1=observability,key2=kubernetes")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import get_key_agent_map
        assert get_key_agent_map() == {"key1": "observability", "key2": "kubernetes"}

    def test_parse_empty_map(self, monkeypatch):
        """Empty env var produces empty dict."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "x")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import get_key_agent_map
        assert get_key_agent_map() == {}

    def test_parse_malformed_entries_skipped(self, monkeypatch):
        """Entries without '=' or with empty key/value are skipped."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "good=obs,badentry,=nokey,novalue=")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "x")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import get_key_agent_map
        assert get_key_agent_map() == {"good": "obs"}


# ─── Unit tests: auth dependency return value ───────────────────────

class TestAuthResultConsumerDefault:
    """Test require_edge_auth returns correct AuthResult."""

    def test_internal_token_no_default_agent(self, monkeypatch):
        """Internal token (X-Internal-Token) → no consumer default."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import require_edge_auth
        result = require_edge_auth(
            x_internal_token="internal-secret",
            x_api_key=None,
            authorization="",
        )
        assert result.consumer_default_agent is None

    def test_mapped_api_key_returns_default_agent(self, monkeypatch):
        """Key in GATEWAY_KEY_AGENT_MAP → returns mapped agent as default."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import require_edge_auth
        result = require_edge_auth(
            x_internal_token="",
            x_api_key="grafana-key",
            authorization="",
        )
        assert result.consumer_default_agent == "observability"

    def test_mapped_bearer_returns_default_agent(self, monkeypatch):
        """Bearer token in GATEWAY_KEY_AGENT_MAP → returns mapped agent."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from src.gateway.auth import require_edge_auth
        result = require_edge_auth(
            x_internal_token="",
            x_api_key=None,
            authorization="Bearer grafana-key",
        )
        assert result.consumer_default_agent == "observability"

    def test_plain_api_key_no_default_agent(self, monkeypatch):
        """Plain API key (in GATEWAY_API_KEYS but NOT in map) → no default."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "plain-key")

        from src.gateway.auth import require_edge_auth
        result = require_edge_auth(
            x_internal_token="",
            x_api_key="plain-key",
            authorization="",
        )
        assert result.consumer_default_agent is None

    def test_invalid_key_still_rejected(self, monkeypatch):
        """Invalid key is still rejected (fail-closed)."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")

        from fastapi import HTTPException
        from src.gateway.auth import require_edge_auth
        with pytest.raises(HTTPException) as exc_info:
            require_edge_auth(
                x_internal_token="",
                x_api_key="wrong-key",
                authorization="",
            )
        assert exc_info.value.status_code == 401


# ─── Integration: chat completions with consumer default agent ──────

class TestChatCompletionsConsumerDefault:
    """Test /v1/chat/completions applies consumer default agent correctly."""

    @pytest.fixture(autouse=True)
    def _patch_env(self, monkeypatch):
        """Set up env for gateway tests."""
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "plain-key")
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana-key=observability")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setattr("src.core.config.settings.rate_budget_enabled", False)

    @pytest.fixture
    def client(self):
        """Create test client — no reload needed (auth reads env fresh)."""
        from fastapi.testclient import TestClient
        from src.gateway.main import app
        return TestClient(app)

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_autoroute_model_with_mapped_key_uses_consumer_default(
        self, mock_pool, mock_client, client
    ):
        """model=base + mapped key → force_agent = consumer's default (observability)."""
        mock_pool.has_capacity.return_value = True
        mock_client.is_supervisor_ready = AsyncMock(return_value=True)
        mock_client.process = AsyncMock(return_value={"response": "ok"})

        async def _fake_submit(job_id, gen):
            async def _iter():
                async for item in gen:
                    yield item
            return _iter()
        mock_pool.submit = AsyncMock(side_effect=_fake_submit)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "base",
                "messages": [{"role": "user", "content": "show me metrics"}],
                "stream": False,
            },
            headers={"X-API-Key": "grafana-key"},
        )
        assert resp.status_code == 200
        mock_client.process.assert_called_once()
        call_kwargs = mock_client.process.call_args.kwargs
        assert call_kwargs["force_agent"] == "observability"

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_explicit_model_overrides_consumer_default(
        self, mock_pool, mock_client, client
    ):
        """model=aigent-squad-kubernetes + mapped key → force_agent = kubernetes (not obs)."""
        mock_pool.has_capacity.return_value = True
        mock_client.is_supervisor_ready = AsyncMock(return_value=True)
        mock_client.process = AsyncMock(return_value={"response": "ok"})

        async def _fake_submit(job_id, gen):
            async def _iter():
                async for item in gen:
                    yield item
            return _iter()
        mock_pool.submit = AsyncMock(side_effect=_fake_submit)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "aigent-squad-kubernetes",
                "messages": [{"role": "user", "content": "list pods"}],
                "stream": False,
            },
            headers={"X-API-Key": "grafana-key"},
        )
        assert resp.status_code == 200
        mock_client.process.assert_called_once()
        call_kwargs = mock_client.process.call_args.kwargs
        assert call_kwargs["force_agent"] == "kubernetes"

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_internal_token_no_default_applied(
        self, mock_pool, mock_client, client
    ):
        """Internal token + auto-route model → normal auto-route (no consumer default)."""
        mock_pool.has_capacity.return_value = True
        mock_client.is_supervisor_ready = AsyncMock(return_value=True)
        mock_client.process = AsyncMock(return_value={"response": "ok"})

        async def _fake_submit(job_id, gen):
            async def _iter():
                async for item in gen:
                    yield item
            return _iter()
        mock_pool.submit = AsyncMock(side_effect=_fake_submit)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "help"}],
                "stream": False,
            },
            headers={"X-Internal-Token": "internal-secret"},
        )
        assert resp.status_code == 200
        mock_client.process.assert_called_once()
        call_kwargs = mock_client.process.call_args.kwargs
        assert call_kwargs["force_agent"] is None

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_plain_key_no_default_applied(
        self, mock_pool, mock_client, client
    ):
        """Plain API key (no mapping) + auto-route model → normal auto-route."""
        mock_pool.has_capacity.return_value = True
        mock_client.is_supervisor_ready = AsyncMock(return_value=True)
        mock_client.process = AsyncMock(return_value={"response": "ok"})

        async def _fake_submit(job_id, gen):
            async def _iter():
                async for item in gen:
                    yield item
            return _iter()
        mock_pool.submit = AsyncMock(side_effect=_fake_submit)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "aigent-squad",
                "messages": [{"role": "user", "content": "help"}],
                "stream": False,
            },
            headers={"X-API-Key": "plain-key"},
        )
        assert resp.status_code == 200
        mock_client.process.assert_called_once()
        call_kwargs = mock_client.process.call_args.kwargs
        assert call_kwargs["force_agent"] is None
