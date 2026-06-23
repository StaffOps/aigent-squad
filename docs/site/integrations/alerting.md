# Alertmanager integration

The supervisor exposes a webhook endpoint that accepts **Alertmanager v2 payloads**
and triggers automatic RCA investigations. The endpoint is a drop-in receiver that
plugs into any existing Alertmanager routing tree.

---

## How it works

```
Prometheus / VictoriaMetrics fires alert
              │
              ▼
        Alertmanager (group, route)
              │
              ├──▶ Slack receiver (team channel)          ← unchanged
              └──▶ AIgent webhook  POST /alerts/incoming  ← new path
                          │
                          ▼
                 Dedup check (Redis fingerprint, TTL 1 h)
                          │
                    ┌─────┴──────┐
                  skip        proceed
                          │
                          ▼
                   run_investigation(symptom)
                          │
                          ▼
                 Slack post-back  (optional, SLACK_WEBHOOK_URL)
                          │
                          ▼
                 Distillation pipeline → Knowledge Base
```

The `continue: true` flag in the route keeps Alertmanager delivering to existing
Slack receivers in parallel — AIgent-squad is additive, not a replacement.

---

## Alertmanager configuration

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

receivers:
  - name: slack-team
    slack_configs:
      - api_url: <slack-webhook>

  - name: aigent-rca
    webhook_configs:
      - url: http://aigent-supervisor:8000/alerts/incoming
        send_resolved: true
        http_config:
          authorization:
            type: Bearer
            credentials: <INTERNAL_API_TOKEN>

route:
  receiver: slack-team
  group_by: ['alertname', 'service']
  routes:
    - matchers:
        - severity =~ "critical|warning"
      receiver: aigent-rca
      continue: true     # Slack still receives
```

!!! warning "Token header note"
    Alertmanager's `authorization` block sends the token as a standard HTTP
    `Authorization: Bearer <token>` header. The supervisor accepts this via its
    existing token middleware. If your network requires the token in a different
    header (e.g. behind a proxy), place a thin Envoy or nginx sidecar in front of
    the supervisor to rewrite the header.

---

## Endpoint reference

| Endpoint | Method | Auth | Purpose |
|----------|:------:|------|---------|
| `POST /alerts/incoming` | POST | `X-Internal-Token` (Bearer) | Receive Alertmanager v2 payload |

### Response body

```json
{
  "ok": true,
  "triggered": 2,
  "deduplicated": 1,
  "resolved_skipped": 0
}
```

| Field | Description |
|-------|-------------|
| `triggered` | Investigations dispatched in this batch |
| `deduplicated` | Alerts skipped — fingerprint seen within the TTL window |
| `resolved_skipped` | `status=resolved` alerts acknowledged but not investigated |

The endpoint responds after all dispatch decisions are made. Investigations run as
awaited tasks within the same request so the counts are accurate in the response.

---

## Deduplication

Each alert carries a unique `fingerprint` from Alertmanager. The supervisor stores
it in Redis with TTL controlled by `ALERT_DEDUP_TTL` (default: `3600` seconds).
Duplicate alerts within the window are skipped — no investigation, no Slack post.

!!! info "Redis failure behaviour"
    If Redis is unavailable, deduplication is **skipped (fail-open)**. Alerts are
    still processed but may trigger repeated investigations for the same event.
    The `aigent.alerts.deduplicated` counter will read zero during an outage.

---

## Resolved alerts

When an alert clears, Alertmanager sends `status: resolved`. The supervisor
acknowledges the payload and counts it in `resolved_skipped` but **does not
trigger an investigation**. Automatic post-mortems on resolution are planned for
Phase 3.

---

## Slack post-back

Set the `SLACK_WEBHOOK_URL` environment variable to enable RCA summaries in Slack.

```yaml
# compose/.env or Helm values
SLACK_WEBHOOK_URL: https://hooks.slack.com/services/T.../B.../...
```

When set, the supervisor posts a structured summary after each completed
investigation:

```
RCA — HighLatency  (service: user-api, severity: critical)
Hypothesis: Deploy of service-x at 14:20 caused pod restart and latency spike
Confidence: alta
Evidence count: 5 (timeline: 3, contradicting: 0)
Prevention:
  • Add canary deployment for service-x
  • Add latency-based rollback in Argo Rollouts
```

If `SLACK_WEBHOOK_URL` is unset, post-back is silently skipped. Failed post-backs
are logged and counted but not retried.

---

## Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.alerts.received` | Counter | `status` (`firing` / `resolved`) | Alerts received via webhook |
| `aigent.alerts.deduplicated` | Counter | — | Skipped due to fingerprint match |
| `aigent.alerts.investigation_triggered` | Counter | — | Investigations dispatched from alerts |
| `aigent.alerts.postback` | Counter | `status` (`success` / `failed`) | Slack post-back attempts |

See [Metrics reference](../reference/metrics.md) for the full label cardinality table.

---

## Known limitations

!!! info "Phase 3 backlog"
    The following capabilities are not yet implemented and are planned for Phase 3:

    - **Multi-round investigation** — low-confidence RCAs do not automatically re-investigate.
    - **Multi-hypothesis / fault-tree** — each investigation produces a single hypothesis.
    - **Retry on failed Slack post-back** — failures are counted and logged only.
    - **Auto-postmortem on resolution** — resolved alerts are not investigated.
