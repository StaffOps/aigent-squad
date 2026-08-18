"""Slack notifier: posts RCA results back to a Slack channel via webhook URL.

Opt-in via SLACK_WEBHOOK_URL env. If unset, no-op (returns without posting).
"""
import os
from typing import Any

import httpx
from src.core.investigation import RCAResult
from src.core.logger import logger
from src.core.metrics import alerts_postback
from src.supervisor.alert_handler import AlertmanagerAlert, AlertmanagerPayload

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()

_CONFIDENCE_EMOJI = {"alta": ":red_circle:", "media": ":large_orange_circle:", "baixa": ":large_yellow_circle:"}


def _format_block(alert: AlertmanagerAlert, rca: RCAResult, payload: AlertmanagerPayload) -> dict[str, Any]:
    emoji = _CONFIDENCE_EMOJI.get(rca.confidence, ":question:")
    alert_name = alert.labels.get("alertname", "alert")
    service = alert.labels.get("service") or alert.labels.get("job") or alert.labels.get("app") or "-"
    severity = alert.labels.get("severity", "-")
    prevention = "\n".join(f"• {p}" for p in (rca.prevention or [])) or "_(none suggested)_"
    text = (
        f"*{emoji} RCA — {alert_name}* (service: `{service}`, severity: `{severity}`)\n\n"
        f"*Hypothesis:* {rca.hypothesis}\n"
        f"*Confidence:* `{rca.confidence}`\n"
        f"*Evidence count:* {len(rca.evidence)} (timeline: {len(rca.timeline)}, contradicting: {len(rca.contradicting)})\n"
        f"*Prevention:*\n{prevention}"
    )
    return {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}


async def post_rca_to_slack(alert: AlertmanagerAlert, rca: RCAResult, payload: AlertmanagerPayload) -> None:
    """Post RCA result to Slack. No-op if SLACK_WEBHOOK_URL not set or call fails."""
    if not SLACK_WEBHOOK_URL:
        return
    try:
        body = _format_block(alert, rca, payload)
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(SLACK_WEBHOOK_URL, json=body)
        alerts_postback.add(1, {"status": "success"})
    except Exception as e:
        logger.warning("Slack post-back failed", extra={"error": str(e)})
        alerts_postback.add(1, {"status": "failed"})
