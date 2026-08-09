"""Alertmanager webhook (`POST /alerts/incoming`) — tier routing must be threaded.

Why this file exists
--------------------
Spec 38 (agentic28) fixed tier routing on the paths that were bypassing
``_resolve_tier_model``: auto-route, fan-out, forced-agent streaming,
investigation, and the **alertmanager webhook**. Named tests existed for all of
them EXCEPT the webhook, and the gap was structural, not an oversight:

1. The webhook's tier resolution lives in the ``_run_inv`` closure inside
   ``src/supervisor/server.py`` — a file that ``.coveragerc`` omitted with the
   justification *"Entry-point thin wrappers (no logic to test — just FastAPI
   app instantiation)"*. That justification aged badly: measured at 62%, with
   the whole closure uncovered.
2. ``tests/test_alert_handler.py`` exercises ``handle_alert_payload`` with an
   INJECTED ``run_investigation_fn``. The injection point is exactly where the
   tier logic lives, so the mock replaced the code under test.

Consequence while untested: this is the only RCA path with **no human in the
loop** — an alert fires, the investigation runs on its own and spends Bedrock
tokens. A silent regression to the wrong tier (the agentic28 failure mode was
17/17 invocations on Sonnet) would go unnoticed.

These tests drive the real HTTP route so the real closure runs.
"""
from __future__ import annotations

import importlib

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _firing_payload(fingerprint: str = "fp-abc123") -> dict:
    """Minimal Alertmanager webhook v2 body with one firing alert."""
    return {
        "version": "4",
        "status": "firing",
        "commonLabels": {"severity": "critical", "service": "vminsert"},
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "SLOBurnRateP1", "severity": "critical"},
                "annotations": {"summary": "error budget burning fast"},
                "fingerprint": fingerprint,
            }
        ],
    }


def _make_client():
    """Build a TestClient over a reloaded server with mocked externals.

    Mirrors the reload-under-patch pattern of test_supervisor_server_errors.py /
    test_health.py. Auth is bypassed via dependency_overrides so the request
    reaches the handler.
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
        mock_registry.discover.return_value = None
        mock_ar.return_value = mock_registry

        import src.supervisor.agent as agent_mod

        importlib.reload(agent_mod)

        import src.supervisor.server as srv

        importlib.reload(srv)
        srv.supervisor.close = AsyncMock()

        # The webhook is token-protected; bypass so we reach the handler.
        srv.app.dependency_overrides[srv.require_token] = lambda: None

        return TestClient(srv.app, raise_server_exceptions=False), srv


def _expected_tier_model():
    """The tier the webhook SHOULD resolve, from the same source of truth.

    Deliberately NOT a hard-coded model id: the assertion is about the value
    being THREADED, not about which model is configured. Hard-coding would make
    this test fail every time the deep-tier model or its feature flag changes,
    which is noise, not signal.
    """
    from src.core.classifier import AgentMatch, ClassifierResult
    from src.supervisor.agent import _resolve_tier_model

    return _resolve_tier_model(
        ClassifierResult(
            agents=[AgentMatch(agent="investigation", confidence=0.9)],
            reasoning="alertmanager-triggered investigation",
            complexity="complex",
        )
    )


def test_webhook_threads_tier_model_override_to_run_investigation():
    """The real closure must pass model_id_override into run_investigation."""
    client, srv = _make_client()

    with (
        patch("src.supervisor.investigation.run_investigation", new_callable=AsyncMock) as mock_inv,
        patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
        patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
    ):
        mock_inv.return_value = {"root_cause": "vminsert saturated", "confidence": "alta"}

        resp = client.post("/alerts/incoming", json=_firing_payload())

        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        mock_inv.assert_awaited_once()
        kwargs = mock_inv.await_args.kwargs
        assert "model_id_override" in kwargs, (
            "the webhook must thread a tier model override — this is the spec-38 "
            "regression that made 17/17 invocations run on the default model"
        )
        assert kwargs["model_id_override"] == _expected_tier_model()


def test_webhook_uses_complex_complexity_so_rca_is_not_downgraded():
    """Alert-triggered RCA is multi-signal work: it must not resolve to the fast tier.

    Asserts the classification the closure builds drives a deep/standard tier,
    never the same tier a trivial one-liner query would get.
    """
    from src.core.classifier import AgentMatch, ClassifierResult
    from src.supervisor.agent import _resolve_tier_model

    fast = _resolve_tier_model(
        ClassifierResult(
            agents=[AgentMatch(agent="observability", confidence=0.95)],
            reasoning="short simple query",
            complexity="simple",
        )
    )
    alert_tier = _expected_tier_model()

    # A test that can silently assert nothing is worse than no test — and this
    # guards the only RCA path with no human in the loop. So when the invariant
    # cannot be evaluated (tier routing switched off in this environment), SKIP
    # loudly instead of passing quietly. Flagged by independent review of
    # 3dee3cd: the previous `if ... is not None` guard made this vacuous whenever
    # AIGENT_TIER_ROUTING_ENABLED=false leaked in from a shell or CI override.
    if fast is None and alert_tier is None:
        pytest.skip(
            "tier routing disabled in this environment (_resolve_tier_model "
            "returns None for every classification) — the downgrade invariant "
            "cannot be evaluated here"
        )

    assert alert_tier != fast, (
        "alert-triggered RCA resolved to the same tier as a trivial query — "
        f"alert={alert_tier!r} simple={fast!r}"
    )


def test_webhook_scopes_budget_per_alert_fingerprint():
    """session_id must carry the fingerprint (budget bucket per unique alert).

    Regression guard for the E2 follow-up recorded in server.py: session_id=""
    made bedrock.py's `charged_session_id = budget_session_id or session_id`
    fall through to a falsy value, so record_usage() was never called and
    alert-triggered investigations spent Bedrock tokens with NO budget cap.
    """
    client, srv = _make_client()

    with (
        patch("src.supervisor.investigation.run_investigation", new_callable=AsyncMock) as mock_inv,
        patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
        patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
    ):
        mock_inv.return_value = {"root_cause": "x", "confidence": "media"}

        resp = client.post("/alerts/incoming", json=_firing_payload("fp-budget-1"))
        assert resp.status_code == 200, resp.text

        session_id = mock_inv.await_args.kwargs["session_id"]
        assert "fp-budget-1" in session_id, (
            f"budget bucket must be per-fingerprint, got {session_id!r}"
        )
        assert session_id != "", "empty session_id disables the budget cap entirely"


def test_webhook_resolved_alert_does_not_trigger_investigation():
    """A resolved alert must not spend tokens."""
    client, srv = _make_client()
    payload = _firing_payload("fp-resolved")
    payload["alerts"][0]["status"] = "resolved"

    with (
        patch("src.supervisor.investigation.run_investigation", new_callable=AsyncMock) as mock_inv,
        patch("src.supervisor.alert_handler.is_duplicate", return_value=False),
        patch.object(srv, "post_rca_to_slack", new_callable=AsyncMock),
    ):
        resp = client.post("/alerts/incoming", json=payload)

        assert resp.status_code == 200, resp.text
        mock_inv.assert_not_awaited()
