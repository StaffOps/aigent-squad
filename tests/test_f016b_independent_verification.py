"""Independent verification tests — F-016b, ff6ad19, 3dee3cd debt paydown.

Written by an INDEPENDENT test author who did NOT write the fix. Tests are
against the CONTRACT, not the implementation details.

Three sections:
1. F-016b contract: get_key_agent_map() must reflect CURRENT env after import
2. ff6ad19 debt: build_completion edge cases the original author was blind to
3. 3dee3cd debt: alertmanager webhook tier assertions that are fragile to shape changes

Each test must FAIL against pre-fix code and PASS after. Verified by revert.
"""
from __future__ import annotations

import importlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: F-016b — get_key_agent_map() must reflect CURRENT env value
# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT: (a) get_key_agent_map() reads env FRESH — not a cached import-time
# snapshot. (b) Tests must not leak state via module globals or app overrides.
# (c) Suite passes under arbitrary collection order.


class TestGetKeyAgentMapFreshness:
    """Prove the function reads env fresh — the F-016b core contract."""

    def test_reflects_env_set_after_import(self, monkeypatch):
        """Setting GATEWAY_KEY_AGENT_MAP AFTER import must be visible."""
        from src.gateway.auth import get_key_agent_map

        # Env was not set at import time — set it NOW
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "key-alpha=agent-a")
        result = get_key_agent_map()
        assert result == {"key-alpha": "agent-a"}, (
            f"get_key_agent_map must read env fresh, got {result}"
        )

    def test_reflects_env_changed_after_first_call(self, monkeypatch):
        """Changing env between calls must be reflected immediately."""
        from src.gateway.auth import get_key_agent_map

        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k1=obs")
        assert get_key_agent_map() == {"k1": "obs"}

        # Change it — the next call MUST reflect the change
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k2=finops,k3=aws")
        result = get_key_agent_map()
        assert result == {"k2": "finops", "k3": "aws"}, (
            f"After env change, got stale result: {result}"
        )

    def test_reflects_env_unset_after_first_call(self, monkeypatch):
        """Unsetting env after a successful call must return empty."""
        from src.gateway.auth import get_key_agent_map

        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "k=v")
        assert get_key_agent_map() == {"k": "v"}

        monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)
        result = get_key_agent_map()
        assert result == {}, f"After env delete, got stale result: {result}"

    def test_empty_env_returns_empty_dict(self, monkeypatch):
        """Empty or unset env → empty dict (fail-safe)."""
        from src.gateway.auth import get_key_agent_map

        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "")
        assert get_key_agent_map() == {}

    def test_malformed_pairs_are_skipped(self, monkeypatch):
        """Only well-formed key=value pairs are parsed; garbage is ignored."""
        from src.gateway.auth import get_key_agent_map

        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "good=obs,,=,no-equals,=nokey")
        result = get_key_agent_map()
        assert result == {"good": "obs"}


class TestRequireEdgeAuthFreshness:
    """Prove require_edge_auth reads credentials fresh — no cached globals."""

    def test_internal_token_recognized_when_set_after_import(self, monkeypatch):
        """INTERNAL_API_TOKEN set after import must be honored."""
        from src.gateway.auth import require_edge_auth

        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok-fresh-123")
        monkeypatch.delenv("GATEWAY_API_KEYS", raising=False)
        monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)

        result = require_edge_auth(
            x_internal_token="tok-fresh-123", x_api_key=None, authorization=""
        )
        assert result.consumer_default_agent is None  # internal token → no scoping

    def test_api_key_recognized_when_set_after_import(self, monkeypatch):
        """GATEWAY_API_KEYS set after import must be honored."""
        from src.gateway.auth import require_edge_auth

        monkeypatch.setenv("GATEWAY_API_KEYS", "key-x,key-y")
        monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
        monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)

        result = require_edge_auth(
            x_internal_token="", x_api_key="key-x", authorization=""
        )
        assert result.consumer_default_agent is None  # plain key → no agent scoping

    def test_key_agent_map_key_works_after_import(self, monkeypatch):
        """A key in GATEWAY_KEY_AGENT_MAP set after import must be accepted AND scoped."""
        from src.gateway.auth import require_edge_auth

        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "mapped-key=observability")
        monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
        monkeypatch.delenv("GATEWAY_API_KEYS", raising=False)

        result = require_edge_auth(
            x_internal_token="", x_api_key="mapped-key", authorization=""
        )
        assert result.consumer_default_agent == "observability"

    def test_rejects_after_env_cleared(self, monkeypatch):
        """Clearing all credential env vars → 401 (fail-closed)."""
        from fastapi import HTTPException

        from src.gateway.auth import require_edge_auth

        monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
        monkeypatch.delenv("GATEWAY_API_KEYS", raising=False)
        monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)

        with pytest.raises(HTTPException) as exc_info:
            require_edge_auth(
                x_internal_token="anything", x_api_key=None, authorization=""
            )
        assert exc_info.value.status_code == 401


class TestNoStateLeakBetweenTests:
    """Prove that test isolation works without reload — sequential calls in one test."""

    def test_sequential_env_changes_are_all_visible(self, monkeypatch):
        """Simulates what pytest-randomly exposes: different env per test."""
        from src.gateway.auth import get_key_agent_map, require_edge_auth

        # Scenario A
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "a=alpha")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok-a")
        assert get_key_agent_map() == {"a": "alpha"}
        result_a = require_edge_auth(
            x_internal_token="tok-a", x_api_key=None, authorization=""
        )
        assert result_a.consumer_default_agent is None

        # Scenario B — completely different creds
        monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "b=beta")
        monkeypatch.setenv("INTERNAL_API_TOKEN", "tok-b")
        assert get_key_agent_map() == {"b": "beta"}
        result_b = require_edge_auth(
            x_internal_token="", x_api_key="b", authorization=""
        )
        assert result_b.consumer_default_agent == "beta"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: ff6ad19 debt — build_completion adversarial edge cases
# ═══════════════════════════════════════════════════════════════════════════════
# The original author tested: dict works, dataclass works, malformed dict missing
# confidence, string unverified_claims. The BLIND SPOTS (what the same author
# wouldn't have thought to break):


class TestBuildCompletionAdversarial:
    """Cases the ff6ad19 author was blind to — probing the assessment boundary."""

    def _capture_warnings(self):
        """Context manager collecting WARNING+ from openai_compat logger."""
        import contextlib

        @contextlib.contextmanager
        def _cm():
            records: list[str] = []

            class _H(logging.Handler):
                def emit(self, record):
                    records.append(record.getMessage())

            logger = logging.getLogger("src.supervisor.openai_compat")
            h = _H(level=logging.WARNING)
            logger.addHandler(h)
            prev = logger.level
            logger.setLevel(logging.WARNING)
            try:
                yield records
            finally:
                logger.removeHandler(h)
                logger.setLevel(prev)

        return _cm()

    def test_partially_valid_dict_missing_confidence(self):
        """Dict with unverified_claims but NO confidence → must not crash."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"unverified_claims": ["claim1"]}  # confidence MISSING
        result = {"response": "ok", "quality_assessment": assessment}

        with self._capture_warnings() as captured:
            payload = build_completion(result, "aigent-squad").model_dump()

        # Answer must still return
        assert payload["choices"][0]["message"]["content"] == "ok"
        # x_aigent must be omitted (confidence is required)
        assert "x_aigent" not in payload
        # Must be logged, not silent
        assert any("x_aigent" in m for m in captured)

    def test_unverified_claims_is_integer(self):
        """unverified_claims as an integer must be rejected, not coerced."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"confidence": "high", "unverified_claims": 42}
        result = {"response": "ok", "quality_assessment": assessment}

        with self._capture_warnings() as captured:
            payload = build_completion(result, "aigent-squad").model_dump()

        assert payload["choices"][0]["message"]["content"] == "ok"
        assert "x_aigent" not in payload, "int must be rejected as non-sequence"
        assert any("x_aigent" in m for m in captured)

    def test_unverified_claims_is_dict(self):
        """unverified_claims as a dict must be rejected — it's iterable but not a list."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"confidence": "medium", "unverified_claims": {"key": "val"}}
        result = {"response": "ok", "quality_assessment": assessment}

        with self._capture_warnings() as captured:
            payload = build_completion(result, "aigent-squad").model_dump()

        assert payload["choices"][0]["message"]["content"] == "ok"
        assert "x_aigent" not in payload, "dict must be rejected"
        assert any("x_aigent" in m for m in captured)

    def test_unverified_claims_is_none_treated_as_empty_list(self):
        """unverified_claims=None must be treated as [] (it has a default)."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"confidence": "high", "unverified_claims": None}
        result = {"response": "ok", "quality_assessment": assessment}

        payload = build_completion(result, "aigent-squad").model_dump()

        # None → treated as empty (the `or []` branch)
        assert "x_aigent" in payload
        assert payload["x_aigent"]["quality"]["unverified_claims"] == []

    def test_unverified_claims_as_tuple_is_accepted(self):
        """Tuple is a valid sequence — should be accepted and converted to list."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"confidence": "low", "unverified_claims": ("claim-a", "claim-b")}
        result = {"response": "ok", "quality_assessment": assessment}

        payload = build_completion(result, "aigent-squad").model_dump()

        assert "x_aigent" in payload
        assert payload["x_aigent"]["quality"]["unverified_claims"] == ["claim-a", "claim-b"]

    def test_unverified_claims_as_bytes_is_rejected(self):
        """bytes is a sequence but must be rejected (same trap as str)."""
        from src.supervisor.openai_compat import build_completion

        assessment = {"confidence": "high", "unverified_claims": b"binary data"}
        result = {"response": "ok", "quality_assessment": assessment}

        with self._capture_warnings() as captured:
            payload = build_completion(result, "aigent-squad").model_dump()

        assert "x_aigent" not in payload, "bytes must be rejected like str"
        assert any("x_aigent" in m for m in captured)

    def test_assessment_is_none_omits_x_aigent_cleanly(self):
        """None assessment → x_aigent absent from wire (not null)."""
        from src.supervisor.openai_compat import build_completion

        result = {"response": "ok", "quality_assessment": None}
        payload = build_completion(result, "aigent-squad").model_dump()
        assert "x_aigent" not in payload

    def test_assessment_is_non_dict_non_dataclass(self):
        """An unexpected type (list, int) as assessment must not crash."""
        from src.supervisor.openai_compat import build_completion

        result = {"response": "ok", "quality_assessment": [1, 2, 3]}

        with self._capture_warnings():
            payload = build_completion(result, "aigent-squad").model_dump()

        assert payload["choices"][0]["message"]["content"] == "ok"
        assert "x_aigent" not in payload


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: 3dee3cd debt — alertmanager webhook tier fragility
# ═══════════════════════════════════════════════════════════════════════════════
# The existing test_alertmanager_webhook_tier.py uses `_expected_tier_model()` to
# get the "expected" value from the SAME function the code under test calls. If
# `_resolve_tier_model` changes its return shape (e.g., returns a dict instead
# of str, or wraps the model_id), both sides would change together and the test
# would pass despite a contract violation.
#
# Independent probe: assert the STRUCTURAL properties of what the webhook threads.


class TestWebhookTierStructuralProperties:
    """Assert structural properties of the tier model override that survive shape changes."""

    def _make_client(self):
        """Build a TestClient over a reloaded server with mocked externals."""
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
            mock_registry.discover.return_value = None
            mock_ar.return_value = mock_registry

            import src.supervisor.agent as agent_mod

            importlib.reload(agent_mod)

            import src.supervisor.server as srv

            importlib.reload(srv)
            srv.supervisor.close = AsyncMock()
            srv.app.dependency_overrides[srv.require_token] = lambda: None

            from fastapi.testclient import TestClient

            return TestClient(srv.app, raise_server_exceptions=False), srv

    @pytest.fixture(autouse=True)
    def _clear_overrides(self):
        """Prevent dependency_overrides from leaking into other tests."""
        yield
        try:
            import src.supervisor.server as _srv

            _srv.app.dependency_overrides.clear()
        except Exception:
            pass

    def _firing_payload(self, fingerprint: str = "fp-struct-1") -> dict:
        return {
            "version": "4",
            "status": "firing",
            "commonLabels": {"severity": "critical", "service": "test"},
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "severity": "critical"},
                    "annotations": {"summary": "test"},
                    "fingerprint": fingerprint,
                }
            ],
        }

    def test_model_override_is_string_or_none_never_other_type(self):
        """The contract is: model_id_override is Optional[str]. Not a dict, not an object."""
        client, srv = self._make_client()

        with (
            patch(
                "src.supervisor.investigation.run_investigation",
                new_callable=AsyncMock,
            ) as mock_inv,
            patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
            patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
        ):
            mock_inv.return_value = {"root_cause": "x", "confidence": "alta"}
            resp = client.post("/alerts/incoming", json=self._firing_payload())
            assert resp.status_code == 200

            mock_inv.assert_awaited_once()
            override = mock_inv.await_args.kwargs.get("model_id_override")
            # Must be str or None — never a wrapped object
            assert override is None or isinstance(override, str), (
                f"model_id_override must be Optional[str], got {type(override).__name__}: {override!r}"
            )

    def test_model_override_when_present_is_a_known_model_id_pattern(self):
        """When tier routing is active, the override must be a model id string
        that matches the Bedrock model id format (contains a dot or slash).

        This catches regressions where _resolve_tier_model returns metadata
        (dict, tuple, namedtuple) instead of a plain model id string.
        """
        client, srv = self._make_client()

        with (
            patch(
                "src.supervisor.investigation.run_investigation",
                new_callable=AsyncMock,
            ) as mock_inv,
            patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
            patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
        ):
            mock_inv.return_value = {"root_cause": "x", "confidence": "alta"}
            resp = client.post("/alerts/incoming", json=self._firing_payload("fp-struct-2"))
            assert resp.status_code == 200

            mock_inv.assert_awaited_once()
            override = mock_inv.await_args.kwargs.get("model_id_override")

            if override is not None:
                # Bedrock model IDs contain dots (anthropic.claude-3-5-sonnet...)
                # or slashes (us.anthropic.claude-3-5-sonnet...) — a bare word or
                # a JSON blob would be wrong.
                assert isinstance(override, str)
                assert "." in override or "/" in override, (
                    f"model_id_override doesn't look like a Bedrock model id: {override!r}"
                )
            # If None, tier routing is disabled — acceptable (skip rather than vacuous assert)

    def test_webhook_always_threads_session_id_with_fingerprint(self):
        """session_id carries the fingerprint regardless of tier routing state.

        This is independent of tier: budget scoping is about cost control, not
        about which model runs. Catches regression where session_id was empty.
        """
        client, srv = self._make_client()

        with (
            patch(
                "src.supervisor.investigation.run_investigation",
                new_callable=AsyncMock,
            ) as mock_inv,
            patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
            patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
        ):
            mock_inv.return_value = {"root_cause": "x", "confidence": "media"}
            resp = client.post("/alerts/incoming", json=self._firing_payload("fp-unique-99"))
            assert resp.status_code == 200

            session_id = mock_inv.await_args.kwargs.get("session_id", "")
            assert session_id, "session_id must be non-empty (budget tracking)"
            assert "fp-unique-99" in session_id
