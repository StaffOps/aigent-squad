# Alerting & Auto-Investigation

The supervisor exposes a webhook endpoint that accepts **Alertmanager v2 payloads** and triggers RCA investigations automatically.

## Flow

```
Prometheus/VictoriaMetrics fires alert
        │
        ▼
   Alertmanager (groups, routes)
        │
        ├─▶ Slack receiver (team channel)            ← business-as-usual
        └─▶ AIgent webhook (POST /alerts/incoming)   ← NEW
                │
                ▼
       Dedup (Redis fingerprint, TTL=1h)
                │
                ▼
       run_investigation(symptom)
                │
                ▼
       (optional) Slack post-back via SLACK_WEBHOOK_URL
                │
                ▼
       Distillation pipeline → KB (spec 21)
```

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
    # Critical and warning alerts trigger RCA in parallel with team notification
    - matchers:
        - severity =~ "critical|warning"
      receiver: aigent-rca
      continue: true     # ← keep processing other routes (Slack still receives)
```

> **Auth note**: Alertmanager doesn't natively support custom headers in webhook config (it uses HTTP Basic / Bearer). The supervisor accepts the bearer token via the existing `X-Internal-Token` header dependency. If your environment requires the token in a different header, place a thin proxy (Envoy, nginx) in front of the supervisor.

## Endpoint

| Endpoint | Method | Auth | Purpose |
|----------|:------:|:----:|---------|
| `/alerts/incoming` | POST | `X-Internal-Token` | Receive Alertmanager v2 payload |

### Response

```json
{"ok": true, "triggered": 2, "deduplicated": 1, "resolved_skipped": 0}
```

The webhook returns immediately after dispatch decisions. Investigations run as **awaited tasks within the request** (so the response captures the result counts). For asynchronous fire-and-forget, change to `asyncio.create_task` in the handler — current default is sync-await for traceability.

### Dedup

Each alert has a unique `fingerprint`. The supervisor stores it in Redis with TTL `ALERT_DEDUP_TTL` (default `3600` seconds). Duplicate alerts within the window are skipped — no investigation, no Slack post.

If Redis is down, dedup is **skipped (fail-open)** — alerts are still processed but may be investigated more than once.

## Slack post-back

Set `SLACK_WEBHOOK_URL` env var to enable. The supervisor will post the RCA summary to the configured Slack channel.

```yaml
# values.yaml or compose env
SLACK_WEBHOOK_URL: https://hooks.slack.com/services/T.../B.../...
```

Format:
```
🔴 RCA — HighLatency  (service: user-api, severity: critical)
Hypothesis: Deploy of service-x at 14:20 caused pod restart and latency spike
Confidence: alta
Evidence count: 5 (timeline: 3, contradicting: 0)
Prevention:
  • Add canary deployment for service-x
  • Add latency-based rollback in Argo Rollouts
```

If `SLACK_WEBHOOK_URL` is unset → no-op (post-back is silently skipped).

## Resolved alerts

Alertmanager sends `status=resolved` when an alert clears. The supervisor **acknowledges these but does not investigate** (counted in `resolved_skipped`).

If you want auto-postmortem on resolution, that's a future task (Phase 3).

## Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.alerts.received` | Counter | `status` (firing/resolved) | Alerts received via webhook |
| `aigent.alerts.deduplicated` | Counter | — | Skipped due to fingerprint match |
| `aigent.alerts.investigation_triggered` | Counter | — | Investigations triggered from alerts |
| `aigent.alerts.postback` | Counter | `status` (success/failed) | Slack post-back attempts |

## Known limitations / Phase 3 backlog

- **Single-round investigation**: alert symptom triggers 1 investigation round. If the RCA confidence is low, no automatic re-investigation. (multi-round = Phase 3)
- **Multi-hypothesis / fault-tree**: not implemented. Each investigation produces 1 hypothesis. (Phase 3)
- **Alert grouping awareness**: each alert in `payload.alerts[]` triggers its own investigation. Group-level deduplication happens at Alertmanager (via `group_by`), so this is acceptable.
- **No retry on failed Slack post-back**: failures are logged and counted. Implement retry queue if needed.
