"""Tests for src.supervisor.alert_handler — contract-based, independent of implementation."""
import pytest
from unittest.mock import AsyncMock, patch

from src.supervisor.alert_handler import (
    AlertmanagerAlert,
    AlertmanagerPayload,
    alert_to_symptom,
    handle_alert_payload,
    is_duplicate,
)
from src.core.investigation import RCAResult


def make_payload(status="firing", fingerprint="fp-1", labels=None, annotations=None):
    return AlertmanagerPayload(
        status=status,
        alerts=[
            AlertmanagerAlert(
                status=status,
                labels=labels or {"alertname": "TestAlert", "service": "svc-a", "severity": "critical"},
                annotations=annotations or {"summary": "test summary"},
                startsAt="2026-06-14T22:00:00Z",
                fingerprint=fingerprint,
            )
        ],
    )


def test_alertmanager_payload_parses_minimal():
    payload = make_payload()
    assert len(payload.alerts) == 1
    assert payload.alerts[0].fingerprint == "fp-1"


def test_alert_to_symptom_includes_service_severity():
    alert = AlertmanagerAlert(
        status="firing",
        labels={"service": "user-api", "severity": "critical", "namespace": "prod", "alertname": "X"},
        annotations={"summary": "high latency"},
        startsAt="2026-06-14T22:00:00Z",
        fingerprint="fp-x",
    )
    symptom = alert_to_symptom(alert, {})
    assert "user-api" in symptom
    assert "critical" in symptom
    assert "prod" in symptom


def test_alert_to_symptom_falls_back_to_alertname_when_no_summary():
    alert = AlertmanagerAlert(
        status="firing",
        labels={"alertname": "HighMemory", "service": "svc"},
        annotations={},
        startsAt="2026-06-14T22:00:00Z",
        fingerprint="fp-y",
    )
    symptom = alert_to_symptom(alert, {})
    assert "HighMemory" in symptom


@patch("src.supervisor.alert_handler.cache")
def test_is_duplicate_first_call_false_second_call_true(mock_cache):
    mock_cache.get.side_effect = [None, "1"]
    assert is_duplicate("fp1") is False
    assert is_duplicate("fp1") is True


@patch("src.supervisor.alert_handler.cache")
def test_is_duplicate_returns_false_on_cache_error(mock_cache):
    mock_cache.get.side_effect = RuntimeError("connection lost")
    assert is_duplicate("fp1") is False


@pytest.mark.asyncio
async def test_handle_alert_payload_skips_resolved():
    payload = make_payload(status="resolved")
    run_fn = AsyncMock()
    result = await handle_alert_payload(payload, run_fn, None)
    assert result["resolved_skipped"] == 1
    run_fn.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.supervisor.alert_handler.is_duplicate", return_value=True)
async def test_handle_alert_payload_skips_duplicate(_mock_dup):
    payload = make_payload()
    run_fn = AsyncMock()
    result = await handle_alert_payload(payload, run_fn, None)
    assert result["deduplicated"] == 1
    run_fn.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.supervisor.alert_handler.is_duplicate", return_value=False)
async def test_handle_alert_payload_triggers_investigation(_mock_dup):
    rca = RCAResult(hypothesis="X", confidence="alta")
    run_fn = AsyncMock(return_value=rca)
    payload = make_payload()
    result = await handle_alert_payload(payload, run_fn, None)
    assert result["triggered"] == 1
    run_fn.assert_awaited_once()
    # Verify symptom string passed (as kwarg or positional)
    call = run_fn.call_args
    symptom = call.kwargs.get("symptom") or (call.args[0] if call.args else "")
    assert "svc-a" in symptom


@pytest.mark.asyncio
@patch("src.supervisor.alert_handler.is_duplicate", return_value=False)
async def test_handle_alert_payload_forwards_fingerprint_to_run_fn(_mock_dup):
    """Regression (independent review 2026-07-14, spec-14 E2 follow-up):
    server.py's run_investigation_fn needs the alert fingerprint to build a
    non-empty budget_session_id — session_id="" silently skipped budget
    tracking entirely (bedrock.py's `charged_session_id or session_id` falls
    through to a falsy empty string)."""
    rca = RCAResult(hypothesis="X", confidence="alta")
    run_fn = AsyncMock(return_value=rca)
    payload = make_payload(fingerprint="fp-xyz")
    await handle_alert_payload(payload, run_fn, None)
    run_fn.assert_awaited_once()
    assert run_fn.call_args.kwargs.get("fingerprint") == "fp-xyz"


@pytest.mark.asyncio
@patch("src.supervisor.alert_handler.is_duplicate", return_value=False)
async def test_handle_alert_payload_calls_slack_postback_when_provided(_mock_dup):
    rca = RCAResult(hypothesis="X", confidence="alta")
    run_fn = AsyncMock(return_value=rca)
    slack_fn = AsyncMock()
    payload = make_payload()
    await handle_alert_payload(payload, run_fn, slack_fn)
    slack_fn.assert_awaited_once()
    args = slack_fn.call_args.args
    assert args[0] == payload.alerts[0]  # alert
    assert args[1] == rca  # rca result
    assert args[2] == payload  # payload


@pytest.mark.asyncio
@patch("src.supervisor.alert_handler.is_duplicate", return_value=False)
async def test_handle_alert_payload_continues_on_investigation_failure(_mock_dup):
    run_fn = AsyncMock(side_effect=Exception("LLM timeout"))
    payload = make_payload()
    # Must not raise
    result = await handle_alert_payload(payload, run_fn, None)
    # No triggered count since it failed
    assert result["triggered"] == 0
