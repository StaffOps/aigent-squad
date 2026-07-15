"""Alertmanager webhook handler: parse, dedup, dispatch investigation."""
import os
from typing import Optional
from pydantic import BaseModel, Field
from src.core.cache import cache
from src.core.logger import logger
from src.core.metrics import (
    alerts_received,
    alerts_deduplicated,
    alerts_investigation_triggered,
)

DEDUP_TTL_SECONDS = int(os.getenv("ALERT_DEDUP_TTL", "3600"))  # 1h default


class AlertmanagerAlert(BaseModel):
    status: str  # firing | resolved
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: Optional[str] = None
    endsAt: Optional[str] = None
    fingerprint: str
    generatorURL: Optional[str] = None


class AlertmanagerPayload(BaseModel):
    """Alertmanager webhook v2 schema (https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)."""
    version: str = "4"
    groupKey: Optional[str] = None
    status: str  # firing | resolved
    receiver: Optional[str] = None
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: Optional[str] = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


def alert_to_symptom(alert: AlertmanagerAlert, common_labels: dict[str, str]) -> str:
    """Convert an Alertmanager alert into a natural-language symptom for the RCA workflow."""
    labels = {**common_labels, **alert.labels}
    summary = alert.annotations.get("summary") or alert.annotations.get("description") or labels.get("alertname", "unknown alert")
    service = labels.get("service") or labels.get("job") or labels.get("app") or "unknown service"
    severity = labels.get("severity", "unknown")
    namespace = labels.get("namespace", "")
    started = alert.startsAt or ""
    ns_part = f" in namespace {namespace}" if namespace else ""
    return (
        f"Alert fired: {summary}. "
        f"Service: {service}{ns_part}. Severity: {severity}. Started at: {started}. "
        f"Investigate root cause considering metrics, logs, recent deploys, and infrastructure events."
    )


def is_duplicate(fingerprint: str) -> bool:
    """Returns True if we've seen this fingerprint within the dedup window."""
    key = f"alert:fp:{fingerprint}"
    try:
        if cache.get(key, namespace="alerts") is not None:
            return True
        cache.set(key, "1", ttl=DEDUP_TTL_SECONDS, namespace="alerts")
        return False
    except Exception as e:
        logger.warning("Dedup check failed (treating as new)", extra={"error": str(e)})
        return False


async def handle_alert_payload(payload: AlertmanagerPayload, run_investigation_fn, slack_post_fn) -> dict:
    """Process Alertmanager payload: dedup, trigger investigation per unique alert, post results.

    Returns a summary dict (counts) for the webhook response.
    """
    triggered = 0
    deduped = 0
    skipped_resolved = 0

    for alert in payload.alerts:
        alerts_received.add(1, {"status": alert.status})
        if alert.status == "resolved":
            skipped_resolved += 1
            continue
        if is_duplicate(alert.fingerprint):
            alerts_deduplicated.add(1)
            deduped += 1
            continue

        symptom = alert_to_symptom(alert, payload.commonLabels)
        try:
            rca = await run_investigation_fn(
                symptom=symptom, agents=None, fingerprint=alert.fingerprint,
            )
            alerts_investigation_triggered.add(1)
            triggered += 1
            if slack_post_fn:
                await slack_post_fn(alert, rca, payload)
        except Exception as e:
            logger.warning("Alert investigation failed", extra={"error": str(e), "fingerprint": alert.fingerprint})

    return {"triggered": triggered, "deduplicated": deduped, "resolved_skipped": skipped_resolved}
