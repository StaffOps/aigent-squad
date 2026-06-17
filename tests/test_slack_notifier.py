"""Tests for src.supervisor.slack_notifier — contract-based."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.supervisor.alert_handler import AlertmanagerAlert, AlertmanagerPayload
from src.supervisor.slack_notifier import post_rca_to_slack
from src.core.investigation import RCAResult


class AsyncCM:
    def __init__(self, mock):
        self._mock = mock

    async def __aenter__(self):
        return self._mock

    async def __aexit__(self, *args):
        return None


def _make_rca():
    return RCAResult(
        hypothesis="Test hypothesis",
        confidence="alta",
        evidence=[],
        timeline=[],
        contradicting=[],
        prevention=["Add alert", "Add test"],
    )


def _make_alert():
    return AlertmanagerAlert(
        status="firing",
        labels={"alertname": "HighLatency", "service": "user-api", "severity": "critical"},
        annotations={"summary": "p99 > 2s"},
        startsAt="2026-06-14T22:00:00Z",
        fingerprint="fp-1",
    )


def _make_payload():
    return AlertmanagerPayload(status="firing", alerts=[_make_alert()])


@pytest.mark.asyncio
@patch("src.supervisor.slack_notifier.SLACK_WEBHOOK_URL", "")
async def test_post_rca_skips_when_webhook_unset():
    with patch("src.supervisor.slack_notifier.httpx") as mock_httpx:
        await post_rca_to_slack(_make_alert(), _make_rca(), _make_payload())
        mock_httpx.AsyncClient.assert_not_called()


@pytest.mark.asyncio
@patch("src.supervisor.slack_notifier.SLACK_WEBHOOK_URL", "https://fake.slack/webhook")
async def test_post_rca_posts_with_correct_format():
    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=MagicMock(status_code=200))

    with patch("src.supervisor.slack_notifier.httpx.AsyncClient", return_value=AsyncCM(client_mock)):
        await post_rca_to_slack(_make_alert(), _make_rca(), _make_payload())

    client_mock.post.assert_awaited_once()
    call_kwargs = client_mock.post.call_args
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("url", "")
    json_body = call_kwargs.kwargs.get("json") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    assert "https://fake.slack/webhook" == url
    assert "Test hypothesis" in json_body["text"]


@pytest.mark.asyncio
@patch("src.supervisor.slack_notifier.SLACK_WEBHOOK_URL", "https://fake.slack/webhook")
async def test_post_rca_swallows_http_errors():
    client_mock = MagicMock()
    client_mock.post = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("src.supervisor.slack_notifier.httpx.AsyncClient", return_value=AsyncCM(client_mock)):
        # Must not raise
        await post_rca_to_slack(_make_alert(), _make_rca(), _make_payload())
