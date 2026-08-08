"""Independent verification of G-5: per-consumer key → default agent scoping.

Written by test-author (separate from implementer) against the CONTRACT:
  1. Mapped key + auto-route model → force_agent = mapped agent
  2. Mapped key + explicit model → explicit wins
  3. Unmapped key + auto-route → no forced agent (classifier decides)
  4. Internal token → no default agent (full auto-route)
  5. GATEWAY_KEY_AGENT_MAP parsing: valid, empty, malformed tolerated
  6. Auth still fail-closed + timing-safe; secrets never logged

Does NOT modify implementation. Reports bugs via assertion messages.
"""
import hmac
import importlib
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1: GATEWAY_KEY_AGENT_MAP PARSING (env var → dict)
# ═══════════════════════════════════════════════════════════════════════


class TestG5MapParsing:
    """Contract: env var parsed into dict; malformed entries tolerated, not crash."""

    def _reload_auth(self):
        import src.gateway.auth as auth_mod
        importlib.reload(auth_mod)
        return auth_mod

    def test_single_valid_pair(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k1=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"k1": "observability"}

    def test_multiple_valid_pairs(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "a=obs,b=k8s,c=aws")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"a": "obs", "b": "k8s", "c": "aws"}

    def test_empty_env_var(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {}

    def test_unset_env_var(self, monkeypatch):
        monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {}

    def test_malformed_no_equals(self, monkeypatch):
        """Entry without '=' is silently skipped."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "good=obs,malformed_no_eq")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"good": "obs"}

    def test_malformed_empty_key(self, monkeypatch):
        """Entry with empty key (=value) is skipped."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "=nokey,valid=agent")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"valid": "agent"}

    def test_malformed_empty_value(self, monkeypatch):
        """Entry with empty value (key=) is skipped."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "novalue=,valid=agent")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"valid": "agent"}

    def test_whitespace_trimmed(self, monkeypatch):
        """Whitespace around keys and values is trimmed."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", " k1 = obs , k2 = k8s ")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        assert auth.get_key_agent_map() == {"k1": "obs", "k2": "k8s"}

    def test_value_with_equals_sign(self, monkeypatch):
        """Only first '=' splits; value may contain '='."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k1=obs=extra")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = self._reload_auth()
        # split("=", 1) → key="k1", value="obs=extra"
        assert auth.get_key_agent_map() == {"k1": "obs=extra"}


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2: AUTH RESULT — consumer_default_agent field
# ═══════════════════════════════════════════════════════════════════════


class TestG5AuthResult:
    """Contract: require_edge_auth returns AuthResult with correct consumer_default_agent."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "grafana=observability,jenkins=kubernetes")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "super-secret-token")
        monkeypatch.setenv("GATEWAY_API_KEYS", "plain-key-1,plain-key-2")
        self.auth = importlib.reload(importlib.import_module("src.gateway.auth"))

    def test_internal_token_header_no_default(self):
        """X-Internal-Token → AuthResult(consumer_default_agent=None)."""
        r = self.auth.require_edge_auth(
            x_internal_token="super-secret-token", x_api_key=None, authorization=""
        )
        assert r.consumer_default_agent is None

    def test_mapped_key_via_x_api_key(self):
        """X-API-Key matching map → consumer_default_agent = mapped value."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key="grafana", authorization=""
        )
        assert r.consumer_default_agent == "observability"

    def test_second_mapped_key(self):
        """Second key in map works independently."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key="jenkins", authorization=""
        )
        assert r.consumer_default_agent == "kubernetes"

    def test_mapped_key_via_bearer(self):
        """Authorization: Bearer <mapped-key> → returns mapped default."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key=None, authorization="Bearer grafana"
        )
        assert r.consumer_default_agent == "observability"

    def test_plain_key_no_default(self):
        """Key in GATEWAY_API_KEYS but NOT in map → no default."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key="plain-key-1", authorization=""
        )
        assert r.consumer_default_agent is None

    def test_internal_token_via_bearer_no_default(self):
        """Bearer with internal token value → no default (same as X-Internal-Token)."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key=None, authorization="Bearer super-secret-token"
        )
        assert r.consumer_default_agent is None

    def test_bearer_case_insensitive_prefix(self):
        """'bearer' prefix is case-insensitive per spec."""
        r = self.auth.require_edge_auth(
            x_internal_token="", x_api_key=None, authorization="BEARER grafana"
        )
        assert r.consumer_default_agent == "observability"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3: FAIL-CLOSED AUTH (not an auth bypass)
# ═══════════════════════════════════════════════════════════════════════


class TestG5FailClosed:
    """Contract: G-5 does NOT weaken auth. Invalid creds still rejected."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "valid-key=observability")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        self.auth = importlib.reload(importlib.import_module("src.gateway.auth"))

    def test_no_credentials_rejects(self):
        """No credential at all → 401."""
        with pytest.raises(HTTPException) as exc:
            self.auth.require_edge_auth(x_internal_token="", x_api_key=None, authorization="")
        assert exc.value.status_code == 401

    def test_wrong_key_rejects(self):
        """Invalid key → 401 even with GATEWAY_KEY_AGENT_MAP configured."""
        with pytest.raises(HTTPException) as exc:
            self.auth.require_edge_auth(
                x_internal_token="", x_api_key="attacker-key", authorization=""
            )
        assert exc.value.status_code == 401

    def test_wrong_bearer_rejects(self):
        """Invalid bearer → 401."""
        with pytest.raises(HTTPException) as exc:
            self.auth.require_edge_auth(
                x_internal_token="", x_api_key=None, authorization="Bearer invalid"
            )
        assert exc.value.status_code == 401

    def test_wrong_internal_token_rejects(self):
        """Wrong internal token → falls through to check other creds → 401."""
        with pytest.raises(HTTPException) as exc:
            self.auth.require_edge_auth(
                x_internal_token="wrong-token", x_api_key=None, authorization=""
            )
        assert exc.value.status_code == 401

    def test_empty_token_configured_rejects_all(self, monkeypatch):
        """When INTERNAL_API_TOKEN is empty, token auth path is disabled."""
        monkeypatch.setenv("INTERNAL_API_TOKEN", "")
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = importlib.reload(importlib.import_module("src.gateway.auth"))
        with pytest.raises(HTTPException) as exc:
            auth.require_edge_auth(x_internal_token="anything", x_api_key=None, authorization="")
        assert exc.value.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4: TIMING-SAFE COMPARISON
# ═══════════════════════════════════════════════════════════════════════


class TestG5TimingSafe:
    """Contract: auth uses hmac.compare_digest (timing-safe), not == ."""

    def test_uses_hmac_compare_digest(self, monkeypatch):
        """Patch hmac.compare_digest to verify it's called on the auth path."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k=obs")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = importlib.reload(importlib.import_module("src.gateway.auth"))

        calls = []
        original = hmac.compare_digest

        def tracking_compare(a, b):
            calls.append((a, b))
            return original(a, b)

        with patch("src.gateway.auth.hmac.compare_digest", side_effect=tracking_compare):
            # Reload won't help here since it's module-level; patch at call-site
            auth.require_edge_auth(
                x_internal_token="secret", x_api_key=None, authorization=""
            )

        # hmac.compare_digest MUST have been called at least once
        assert len(calls) >= 1, "hmac.compare_digest not used — timing attack possible"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5: SECRETS NEVER LOGGED
# ═══════════════════════════════════════════════════════════════════════


class TestG5SecretsNotLogged:
    """Contract: credential values never appear in log output."""

    def test_failed_auth_does_not_log_secret(self, monkeypatch, caplog):
        """A failed auth attempt must NOT log the attempted credential."""
        secret_key = "super-secret-do-not-log-me-12345"
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "real-key=obs")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "real-token")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = importlib.reload(importlib.import_module("src.gateway.auth"))

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(HTTPException):
                auth.require_edge_auth(
                    x_internal_token=secret_key,
                    x_api_key=secret_key,
                    authorization=f"Bearer {secret_key}",
                )

        # The secret value must NOT appear anywhere in log output
        full_log = caplog.text
        assert secret_key not in full_log, (
            f"Secret leaked to logs! Found '{secret_key}' in log output"
        )

    def test_valid_auth_does_not_log_key(self, monkeypatch, caplog):
        """Successful auth also must not log the key value."""
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "my-secret-key=obs")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "")
        auth = importlib.reload(importlib.import_module("src.gateway.auth"))

        with caplog.at_level(logging.DEBUG):
            auth.require_edge_auth(
                x_internal_token="", x_api_key="my-secret-key", authorization=""
            )

        assert "my-secret-key" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6: INTEGRATION — /v1/chat/completions consumer scoping
# ═══════════════════════════════════════════════════════════════════════


class TestG5Integration:
    """Contract: gateway applies consumer_default_agent with correct priority."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-tok")
        monkeypatch.setenv("GATEWAY_API_KEYS", "plain-key")
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "scoped-key=observability")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setattr("src.core.config.settings.rate_budget_enabled", False)
        # Reload auth to pick up env
        importlib.reload(importlib.import_module("src.gateway.auth"))

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from src.gateway.main import app
        return TestClient(app)

    def _mock_pool_and_client(self, mock_pool, mock_supervisor):
        mock_pool.has_capacity.return_value = True
        mock_supervisor.is_supervisor_ready = AsyncMock(return_value=True)
        mock_supervisor.process = AsyncMock(return_value={"response": "test"})

        async def _submit(job_id, gen):
            async def _wrap():
                async for item in gen:
                    yield item
            return _wrap()
        mock_pool.submit = AsyncMock(side_effect=_submit)

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_mapped_key_autoroute_applies_default(self, mock_pool, mock_sup, client):
        """Assertion 1: mapped key + model=aigent-squad → force_agent=observability."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"X-API-Key": "scoped-key"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] == "observability"

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_mapped_key_unrecognized_model_applies_default(self, mock_pool, mock_sup, client):
        """Assertion 1b: mapped key + model='base' (unrecognized→auto) → force_agent=observability."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "base", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"X-API-Key": "scoped-key"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] == "observability"

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_explicit_model_overrides_consumer_default(self, mock_pool, mock_sup, client):
        """Assertion 2: mapped key + model=aigent-squad-kubernetes → force_agent=kubernetes."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad-kubernetes", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"X-API-Key": "scoped-key"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] == "kubernetes"

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_unmapped_key_autoroute_no_forced_agent(self, mock_pool, mock_sup, client):
        """Assertion 3: key NOT in map + auto-route model → force_agent=None."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"X-API-Key": "plain-key"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] is None

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_internal_token_autoroute_no_forced_agent(self, mock_pool, mock_sup, client):
        """Assertion 4: X-Internal-Token + auto-route → force_agent=None."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"X-Internal-Token": "internal-tok"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] is None

    @patch("src.gateway.main._agent_names", ["observability", "kubernetes", "aws", "finops"])
    @patch("src.gateway.main.supervisor_client")
    @patch("src.gateway.main.worker_pool")
    def test_mapped_key_via_bearer_applies_default(self, mock_pool, mock_sup, client):
        """Bearer auth with mapped key also applies consumer default."""
        self._mock_pool_and_client(mock_pool, mock_sup)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad", "messages": [{"role": "user", "content": "q"}], "stream": False},
            headers={"Authorization": "Bearer scoped-key"},
        )
        assert resp.status_code == 200
        assert mock_sup.process.call_args.kwargs["force_agent"] == "observability"

    def test_unauthenticated_request_rejected(self, client):
        """No auth header → 401 (fail-closed preserved with G-5)."""
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "aigent-squad", "messages": [{"role": "user", "content": "q"}], "stream": False},
        )
        assert resp.status_code == 401
