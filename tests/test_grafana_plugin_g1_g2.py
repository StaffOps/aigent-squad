"""Independent tests for G-1 (resolve_target auto-route) and G-2 (Bearer auth).

These tests verify the CONTRACT specified in BACKLOG G-1 / G-2 — they are written
against the spec and public API, NOT by reading the implementation line-by-line.

G-1: resolve_target() must return None (auto-route) for unrecognized model ids
     instead of raising UnknownModelError. Known agents still force-route.

G-2: require_edge_auth() must accept Authorization: Bearer <token> matching
     INTERNAL_API_TOKEN or GATEWAY_API_KEYS in addition to existing X-Internal-Token
     and X-API-Key paths. Fail-closed when no secret configured.

Run via Docker:
    docker run --rm -v "$(pwd):/app" -w /app \
      -e INTERNAL_API_TOKEN=test-token \
      -e GATEWAY_API_KEYS=key-alpha,key-beta \
      python:3.11-slim sh -c \
      "pip install -q pytest fastapi httpx pydantic &&
       python -m pytest tests/test_grafana_plugin_g1_g2.py -v"
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# G-1: resolve_target tests
# ---------------------------------------------------------------------------

# Known agent names for test fixtures (simulating the registry)
KNOWN_AGENTS = ["kubernetes", "observability", "security", "finops"]


class TestResolveTargetAutoRoute:
    """G-1: unrecognized model ids auto-route (return None), never raise."""

    def _resolve(self, model: str) -> str | None:
        from src.supervisor.openai_compat import resolve_target
        return resolve_target(model, KNOWN_AGENTS)

    # --- (1) External/unknown models auto-route to None ---

    def test_base_model_returns_none(self):
        """'base' is an unrecognized id → auto-route (None)."""
        assert self._resolve("base") is None

    def test_large_model_returns_none(self):
        """'large' is an unrecognized id → auto-route (None)."""
        assert self._resolve("large") is None

    def test_gpt4_returns_none(self):
        """'gpt-4' sent by external clients → auto-route (None)."""
        assert self._resolve("gpt-4") is None

    def test_arbitrary_string_returns_none(self):
        """Completely arbitrary model id → auto-route (None)."""
        assert self._resolve("some-random-model-xyz") is None

    def test_empty_string_returns_none(self):
        """Empty model id → auto-route (None), never raises."""
        assert self._resolve("") is None

    def test_openai_model_name_returns_none(self):
        """'gpt-3.5-turbo' (typical Grafana LLM default) → auto-route."""
        assert self._resolve("gpt-3.5-turbo") is None

    def test_claude_model_returns_none(self):
        """'claude-3-sonnet' → auto-route (None)."""
        assert self._resolve("claude-3-sonnet") is None

    # --- (2) Exact prefix returns None (auto-route to classifier) ---

    def test_exact_prefix_returns_none(self):
        """'aigent-squad' (bare) → classifier auto-route (None)."""
        assert self._resolve("aigent-squad") is None

    # --- (3) Known agent suffix returns the agent name ---

    def test_known_agent_kubernetes(self):
        """'aigent-squad-kubernetes' → force-route to 'kubernetes'."""
        assert self._resolve("aigent-squad-kubernetes") == "kubernetes"

    def test_known_agent_observability(self):
        """'aigent-squad-observability' → force-route to 'observability'."""
        assert self._resolve("aigent-squad-observability") == "observability"

    def test_known_agent_security(self):
        """'aigent-squad-security' → force-route to 'security'."""
        assert self._resolve("aigent-squad-security") == "security"

    def test_known_agent_finops(self):
        """'aigent-squad-finops' → force-route to 'finops'."""
        assert self._resolve("aigent-squad-finops") == "finops"

    # --- (4) Unknown agent suffix → auto-route (None) per spec ---

    def test_unknown_agent_suffix_returns_none(self):
        """'aigent-squad-bogusagent' (unknown) → auto-route (None)."""
        result = self._resolve("aigent-squad-bogusagent")
        assert result is None

    def test_unknown_agent_suffix_nonexistent(self):
        """'aigent-squad-does-not-exist' → auto-route (None)."""
        result = self._resolve("aigent-squad-does-not-exist")
        assert result is None

    # --- (5) No UnknownModelError raised for any input ---

    @pytest.mark.parametrize("model_id", [
        "base", "large", "gpt-4", "gpt-3.5-turbo", "claude-3-opus",
        "", "   ", "aigent-squad-unknownthing", "unknown/model",
        "aigent-squad-", "AIGENT-SQUAD",  # edge: case-sensitive prefix
    ])
    def test_never_raises_unknown_model_error(self, model_id):
        """resolve_target MUST NOT raise for any input (G-1 contract)."""
        from src.supervisor.openai_compat import UnknownModelError
        # Must not raise — returns None or a known agent name.
        try:
            result = self._resolve(model_id)
        except UnknownModelError:
            pytest.fail(f"UnknownModelError raised for model_id='{model_id}'")
        # If it doesn't raise, result is either None or a string.
        assert result is None or isinstance(result, str)

    # --- Edge cases ---

    def test_prefix_with_trailing_dash_no_agent(self):
        """'aigent-squad-' (trailing dash, empty agent) → auto-route."""
        result = self._resolve("aigent-squad-")
        assert result is None

    def test_case_sensitive_prefix(self):
        """'AIGENT-SQUAD' (uppercase) is NOT the prefix → auto-route."""
        result = self._resolve("AIGENT-SQUAD")
        assert result is None

    def test_empty_agent_list(self):
        """If no agents registered, all model ids → auto-route."""
        from src.supervisor.openai_compat import resolve_target
        assert resolve_target("aigent-squad-kubernetes", []) is None
        assert resolve_target("aigent-squad", []) is None
        assert resolve_target("gpt-4", []) is None


# ---------------------------------------------------------------------------
# G-2: require_edge_auth tests — Bearer token support
# ---------------------------------------------------------------------------

# The auth module reads env vars at IMPORT TIME. We need to control them.
# Strategy: reload the module with controlled env in each test class.

@pytest.fixture()
def auth_module_configured():
    """Provide a freshly-imported auth module with known secrets."""
    env = {
        "INTERNAL_API_TOKEN": "secret-internal-42",
        "GATEWAY_API_KEYS": "key-alpha,key-beta,key-gamma",
    }
    with patch.dict(os.environ, env, clear=False):
        # Force reimport to pick up new env vars.
        mod_name = "src.gateway.auth"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import src.gateway.auth as auth_mod
        yield auth_mod
    # Cleanup: remove to avoid leaking state.
    if mod_name in sys.modules:
        del sys.modules[mod_name]


@pytest.fixture()
def auth_module_no_secrets():
    """Auth module with NO secrets configured → must fail-closed."""
    env = {
        "INTERNAL_API_TOKEN": "",
        "GATEWAY_API_KEYS": "",
    }
    with patch.dict(os.environ, env, clear=False):
        mod_name = "src.gateway.auth"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import src.gateway.auth as auth_mod
        yield auth_mod
    if mod_name in sys.modules:
        del sys.modules[mod_name]


class TestExtractBearer:
    """Unit tests for the _extract_bearer helper."""

    def _get_fn(self, auth_mod):
        return auth_mod._extract_bearer

    def test_standard_bearer(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        assert fn("Bearer my-token-123") == "my-token-123"

    def test_lowercase_bearer(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        assert fn("bearer my-token") == "my-token"

    def test_uppercase_bearer(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        assert fn("BEARER my-token") == "my-token"

    def test_mixed_case_bearer(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        assert fn("BeArEr my-token") == "my-token"

    def test_whitespace_tolerance(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        # Leading/trailing whitespace in the full header value.
        assert fn("  Bearer  spaced-token  ") == "spaced-token"

    def test_empty_string(self, auth_module_configured):
        fn = self._get_fn(auth_module_configured)
        assert fn("") == ""

    def test_none_equivalent_empty(self, auth_module_configured):
        """If someone passes empty → empty, no crash."""
        fn = self._get_fn(auth_module_configured)
        assert fn("") == ""

    def test_basic_scheme_ignored(self, auth_module_configured):
        """Non-Bearer schemes (e.g. Basic) → returns empty (not treated as token)."""
        fn = self._get_fn(auth_module_configured)
        assert fn("Basic dXNlcjpwYXNz") == ""

    def test_no_space_after_bearer(self, auth_module_configured):
        """'Bearertoken' (no space) → starts with 'bearer ' (7 chars) is False → empty."""
        fn = self._get_fn(auth_module_configured)
        # 'Bearertoken'.lower() == 'bearertoken' → .startswith('bearer ') is False
        assert fn("Bearertoken") == ""

    def test_just_bearer_no_token(self, auth_module_configured):
        """'Bearer ' with nothing after → empty string (stripped)."""
        fn = self._get_fn(auth_module_configured)
        assert fn("Bearer ") == ""

    def test_bearer_with_only_spaces(self, auth_module_configured):
        """'Bearer    ' → empty (stripped spaces)."""
        fn = self._get_fn(auth_module_configured)
        assert fn("Bearer    ") == ""


class TestRequireEdgeAuthBearer:
    """G-2: Authorization: Bearer <token> path."""

    def _call(self, auth_mod, *, x_internal_token="", x_api_key=None, authorization=""):
        """Call require_edge_auth and return (allowed=True) or raise HTTPException."""
        return auth_mod.require_edge_auth(
            x_internal_token=x_internal_token,
            x_api_key=x_api_key,
            authorization=authorization,
        )

    # --- (6) Bearer + INTERNAL_API_TOKEN → allowed ---

    def test_bearer_internal_token_allowed(self, auth_module_configured):
        """Bearer with the correct INTERNAL_API_TOKEN → passes."""
        # Should not raise.
        self._call(auth_module_configured, authorization="Bearer secret-internal-42")

    def test_bearer_internal_token_case_insensitive_prefix(self, auth_module_configured):
        """'bearer secret-internal-42' (lowercase prefix) → allowed."""
        self._call(auth_module_configured, authorization="bearer secret-internal-42")

    # --- (7) Bearer + API key from GATEWAY_API_KEYS → allowed ---

    def test_bearer_api_key_alpha(self, auth_module_configured):
        """Bearer with 'key-alpha' (in GATEWAY_API_KEYS) → allowed."""
        self._call(auth_module_configured, authorization="Bearer key-alpha")

    def test_bearer_api_key_beta(self, auth_module_configured):
        """Bearer with 'key-beta' → allowed."""
        self._call(auth_module_configured, authorization="Bearer key-beta")

    def test_bearer_api_key_gamma(self, auth_module_configured):
        """Bearer with 'key-gamma' → allowed."""
        self._call(auth_module_configured, authorization="Bearer key-gamma")

    # --- (8) Invalid Bearer → 401 ---

    def test_bearer_wrong_token_denied(self, auth_module_configured):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="Bearer wrong-token-xxx")
        assert exc_info.value.status_code == 401

    def test_bearer_empty_token_denied(self, auth_module_configured):
        """'Bearer ' with empty token → deny."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="Bearer ")
        assert exc_info.value.status_code == 401

    # --- (9) Existing X-Internal-Token and X-API-Key still work ---

    def test_x_internal_token_still_works(self, auth_module_configured):
        """Legacy X-Internal-Token path still accepted."""
        self._call(auth_module_configured, x_internal_token="secret-internal-42")

    def test_x_api_key_still_works(self, auth_module_configured):
        """Legacy X-API-Key path still accepted."""
        self._call(auth_module_configured, x_api_key="key-alpha")

    def test_x_api_key_beta_still_works(self, auth_module_configured):
        self._call(auth_module_configured, x_api_key="key-beta")

    def test_x_internal_token_wrong_denied(self, auth_module_configured):
        """Wrong X-Internal-Token → 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, x_internal_token="wrong-token")
        assert exc_info.value.status_code == 401

    def test_x_api_key_wrong_denied(self, auth_module_configured):
        """Wrong X-API-Key → 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, x_api_key="nonexistent-key")
        assert exc_info.value.status_code == 401

    # --- (10) No secret configured → fail-closed (deny everything) ---

    def test_no_secrets_bearer_denied(self, auth_module_no_secrets):
        """When INTERNAL_API_TOKEN="" and GATEWAY_API_KEYS="" → all denied."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_no_secrets, authorization="Bearer anything")
        assert exc_info.value.status_code == 401

    def test_no_secrets_x_internal_denied(self, auth_module_no_secrets):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_no_secrets, x_internal_token="anything")
        assert exc_info.value.status_code == 401

    def test_no_secrets_no_creds_denied(self, auth_module_no_secrets):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_no_secrets)
        assert exc_info.value.status_code == 401

    # --- (11) Malformed Authorization header → 401, no crash ---

    def test_malformed_no_bearer_prefix(self, auth_module_configured):
        """'Token secret-internal-42' (wrong scheme) → denied."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="Token secret-internal-42")
        assert exc_info.value.status_code == 401

    def test_malformed_blank(self, auth_module_configured):
        """Blank Authorization header → denied, no crash."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="")
        assert exc_info.value.status_code == 401

    def test_malformed_just_spaces(self, auth_module_configured):
        """'   ' → denied, no crash."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="   ")
        assert exc_info.value.status_code == 401

    def test_malformed_basic_auth(self, auth_module_configured):
        """'Basic dXNlcjpwYXNz' → denied (not Bearer scheme)."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(auth_module_configured, authorization="Basic dXNlcjpwYXNz")
        assert exc_info.value.status_code == 401

    def test_malformed_bearer_no_space(self, auth_module_configured):
        """'Bearersecret-internal-42' → no space after Bearer → denied."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(
                auth_module_configured,
                authorization="Bearersecret-internal-42",
            )
        assert exc_info.value.status_code == 401


class TestEdgeAuthTimingSafe:
    """Verify timing-safe comparison is used (hmac.compare_digest)."""

    def test_internal_token_uses_hmac_compare(self, auth_module_configured):
        """The module MUST use hmac.compare_digest, not == for token comparison."""
        import inspect
        source = inspect.getsource(auth_module_configured.require_edge_auth)
        # Verify the implementation uses hmac.compare_digest (timing-safe).
        assert "hmac.compare_digest" in source or "compare_digest" in source, (
            "require_edge_auth MUST use hmac.compare_digest for timing-safe comparison"
        )


class TestEdgeAuthMultiplePathsPriority:
    """Verify that any ONE valid credential suffices — no interference."""

    def _call(self, auth_mod, **kwargs):
        return auth_mod.require_edge_auth(
            x_internal_token=kwargs.get("x_internal_token", ""),
            x_api_key=kwargs.get("x_api_key", None),
            authorization=kwargs.get("authorization", ""),
        )

    def test_bearer_works_even_with_wrong_x_headers(self, auth_module_configured):
        """Valid Bearer + wrong X-Internal-Token → allowed (Bearer suffices)."""
        self._call(
            auth_module_configured,
            x_internal_token="wrong",
            x_api_key="wrong",
            authorization="Bearer secret-internal-42",
        )

    def test_x_internal_works_even_with_wrong_bearer(self, auth_module_configured):
        """Valid X-Internal-Token + wrong Bearer → allowed."""
        self._call(
            auth_module_configured,
            x_internal_token="secret-internal-42",
            authorization="Bearer wrong",
        )

    def test_x_api_key_works_even_with_wrong_others(self, auth_module_configured):
        """Valid X-API-Key + wrong others → allowed."""
        self._call(
            auth_module_configured,
            x_internal_token="wrong",
            x_api_key="key-alpha",
            authorization="Bearer wrong",
        )

    def test_all_wrong_denied(self, auth_module_configured):
        """All credentials wrong → 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            self._call(
                auth_module_configured,
                x_internal_token="wrong",
                x_api_key="wrong",
                authorization="Bearer wrong",
            )
        assert exc_info.value.status_code == 401
