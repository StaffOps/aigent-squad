"""Tests for S2 security fix — supervisor 500 handler must never leak exception text.

Spec 31 T23 / BACKLOG F-009: the /internal/process route's generic exception
handler returns a fixed "Internal server error" detail and never exposes the raw
exception message (which may contain ARNs, credentials, or internal state).
"""

import importlib

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _make_client_with_failing_supervisor(side_effect: Exception):
    """Build a TestClient mirroring test_health.py's reload-under-patch pattern.

    The supervisor mock's process_request is configured to raise *side_effect*,
    simulating an unhandled error inside the orchestration layer.
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

        # Set up AgentRegistry mock so discover() works when agent module reloads
        mock_registry = MagicMock()
        mock_registry.agent_names.return_value = ["mock-agent"]
        mock_ar.return_value = mock_registry
        mock_registry.discover.return_value = None

        # Reload the agent module so it creates a supervisor using mocked deps
        import src.supervisor.agent as agent_mod

        importlib.reload(agent_mod)

        # Now reload server so it picks up the freshly-created (mocked) supervisor
        import src.supervisor.server as srv

        importlib.reload(srv)

        # Override the supervisor's process_request with our failing mock
        srv.supervisor.process_request = AsyncMock(side_effect=side_effect)
        srv.supervisor.close = AsyncMock()

        # Bypass auth so the test hits the handler directly
        srv.app.dependency_overrides[srv.require_internal_token] = lambda: None

        client = TestClient(srv.app, raise_server_exceptions=False)
        return client, srv


# ---------------------------------------------------------------------------
# S2 — 500 handler never leaks exception internals
# ---------------------------------------------------------------------------


def test_500_returns_generic_message_and_never_leaks_exception():
    """POST /internal/process 500 must return generic detail, hiding internals."""
    secret_arn = "arn:aws:iam::123456789012:role/secret-role"
    error_msg = f"boom {secret_arn}"

    client, _srv = _make_client_with_failing_supervisor(
        RuntimeError(error_msg),
    )

    resp = client.post(
        "/internal/process",
        json={
            "user_input": "hello",
            "user_id": "u-1",
            "session_id": "s-1",
        },
    )

    # Must be a 500
    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}"

    # Body must contain only the generic message
    body = resp.json()
    assert body["detail"] == "Internal server error"

    # The raw exception text must NOT appear anywhere in the response
    raw_text = resp.text
    assert "boom" not in raw_text, "Exception keyword 'boom' leaked into response"
    assert secret_arn not in raw_text, "Sensitive ARN leaked into response"
    assert "123456789012" not in raw_text, "AWS account ID leaked into response"


def test_500_does_not_leak_arbitrary_traceback_content():
    """Even with a complex nested exception, nothing internal leaks."""
    inner = ValueError("database password=hunter2 at host db.internal:5432")
    outer = RuntimeError("orchestration failed")
    outer.__cause__ = inner

    client, _srv = _make_client_with_failing_supervisor(outer)

    resp = client.post(
        "/internal/process",
        json={
            "user_input": "test",
            "user_id": "u-2",
            "session_id": "s-2",
        },
    )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Internal server error"

    raw_text = resp.text
    assert "hunter2" not in raw_text, "Password leaked into response"
    assert "db.internal" not in raw_text, "Internal hostname leaked into response"
    assert "orchestration failed" not in raw_text, "Exception message leaked"
