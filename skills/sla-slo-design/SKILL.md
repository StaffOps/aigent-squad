---
name: sla-slo-design
description: SLI/SLO/SLA design framework. Use when defining reliability targets for services, creating error budgets, designing burn rate alerts, or establishing service tiers. Covers indicator selection, objective setting, budget policies, multi-window alerting, and <org>-specific patterns.
keywords: [sla-slo-design, sla, slo, design, "sla slo", "slo design", sli]
---
# SLI/SLO/SLA Design Framework

Reliability engineering framework for <org> services. Based on Google SRE book patterns, adapted for VictoriaMetrics + VMAlert + Alertmanager stack.

## Concepts

| Term | Definition | Owner |
|------|-----------|-------|
| **SLI** | Service Level Indicator — measurable metric reflecting user experience | Engineering |
| **SLO** | Service Level Objective — target value for an SLI | Engineering + Product |
| **SLA** | Service Level Agreement — contractual commitment (SLO - margin) | Business |
| **Error Budget** | Allowed unreliability = 1 - SLO | Engineering |

## SLI Types

### Availability (request-based services)

```
SLI = successful_requests / total_requests
```

Exclude from total: health checks (`/healthz`, `/ready`), synthetic probes.

VictoriaMetrics query:
```promql
sum(rate(http_server_request_duration_seconds_count{http_status_code!~"5.."}[5m]))
/
sum(rate(http_server_request_duration_seconds_count[5m]))
```

### Latency (request-based services)

```
SLI = requests_below_threshold / total_requests
```

Multiple thresholds (p50, p95, p99):
```promql
# p99 < 500ms
histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le)) < 0.5
```

### Freshness (batch/async — BTC services)

```
SLI = data_age < threshold
```

For BTC workloads:
```promql
time() - max(batch_last_successful_run_timestamp) < 3600  # data < 1h old
```

### Correctness

```
SLI = valid_responses / total_responses
```

Application-specific validation (schema, business rules).

### Job success (batch — BTC)

```
SLI = successful_jobs / total_jobs
```

```promql
sum(argo_workflows_count{status="Succeeded"}) / sum(argo_workflows_count)
```

## SLO Design

### Format

> "**X%** of **<SLI>** over **<window>**"

Example: "99.9% of requests return successfully within 500ms over a 30-day rolling window"

### Service tiers

| Tier | Availability SLO | Latency p99 | Error Budget (30d) | Examples |
|------|-----------------|-------------|-------------------|----------|
| **Tier 1** | 99.95% | < 200ms | 21.6 min | Payment APIs, auth |
| **Tier 2** | 99.9% | < 500ms | 43.2 min | Core APIs (People, KYC) |
| **Tier 3** | 99.5% | < 2s | 3.6 hours | Internal tools, batch |

### BTC (batch) SLOs

| SLI | Target | Window |
|-----|--------|--------|
| Job success rate | 99.5% | 7 days |
| Processing time | < 2x expected | Per job |
| Data freshness | < SLA threshold | Continuous |

### Rules

- Multiple SLOs per service (minimum: availability + latency)
- Rolling window (30 days) — NOT calendar month
- SLA = SLO - margin (e.g., SLO 99.95% → SLA 99.9%)
- Review SLOs quarterly with service owners

## Error Budget

### Calculation

```
Budget = 1 - SLO_target

Example (99.9% SLO, 30-day window):
  Budget = 0.1% = 0.001
  Minutes = 30 * 24 * 60 * 0.001 = 43.2 minutes
```

### Burn rate

How fast the budget is being consumed:

```
burn_rate = error_rate_observed / error_rate_allowed
```

- burn_rate = 1 → consuming budget at exactly the allowed rate
- burn_rate = 10 → consuming 10x faster (budget exhausted in 3 days)
- burn_rate = 0 → no errors (ideal)

### Multi-window burn rate alerting

Google SRE pattern — alerts at different speeds:

| Alert | Burn Rate | Short Window | Long Window | Action |
|-------|-----------|-------------|-------------|--------|
| **Critical** | 14.4x | 1h | 5m | Page (SEV1) |
| **High** | 6x | 6h | 30m | Page (SEV2) |
| **Medium** | 3x | 1d | 2h | Ticket (SEV3) |
| **Low** | 1x | 3d | 6h | Review |

Why two windows: short window detects the issue, long window confirms it's sustained (reduces false positives).

### VMAlert recording rules

```yaml
# Recording rules for SLI calculation
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: sli-recording-rules
  namespace: monitoring
spec:
  groups:
    - name: sli.availability
      interval: 1m
      rules:
        - record: sli:availability:ratio_rate5m
          expr: |
            sum(rate(http_server_request_duration_seconds_count{http_status_code!~"5.."}[5m])) by (service_name)
            /
            sum(rate(http_server_request_duration_seconds_count[5m])) by (service_name)

    - name: sli.error_budget
      interval: 1m
      rules:
        - record: sli:error_budget:remaining_ratio
          expr: |
            1 - (
              (1 - sli:availability:ratio_rate5m)
              /
              (1 - 0.999)  # SLO target
            )
```

### VMAlert burn rate alerts

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: slo-burn-rate-alerts
  namespace: monitoring
spec:
  groups:
    - name: slo.burn_rate
      rules:
        - alert: SLOBurnRateCritical
          expr: |
            (
              (1 - sli:availability:ratio_rate5m) / (1 - 0.999)
            ) > 14.4
            and
            (
              (1 - avg_over_time(sli:availability:ratio_rate5m[1h])) / (1 - 0.999)
            ) > 14.4
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "SLO burn rate critical for {{ $labels.service_name }}"
            description: "Error budget being consumed at 14.4x rate. Budget exhausted in ~2h."
            runbook_url: "https://docs.<old-internal-domain>/runbooks/slo-burn-rate"

        - alert: SLOBurnRateHigh
          expr: |
            (
              (1 - sli:availability:ratio_rate5m) / (1 - 0.999)
            ) > 6
            and
            (
              (1 - avg_over_time(sli:availability:ratio_rate5m[6h])) / (1 - 0.999)
            ) > 6
          for: 30m
          labels:
            severity: warning
          annotations:
            summary: "SLO burn rate high for {{ $labels.service_name }}"
            description: "Error budget being consumed at 6x rate. Budget exhausted in ~5d."
```

## Error Budget Policies

| Budget Consumed | Action |
|----------------|--------|
| < 50% | Normal operations, deploy freely |
| 50-80% | Review recent changes, increase testing |
| 80-100% | Freeze non-critical deploys, prioritize reliability |
| 100% (exhausted) | All engineering on reliability until budget recovers |

## Grafana Dashboard Pattern

SLO overview dashboard (datasource UID: `victoriametrics`):

- **Panel 1**: Budget remaining (%) — gauge, thresholds at 50/20/0
- **Panel 2**: Burn rate (current) — stat, thresholds at 1/3/6/14.4
- **Panel 3**: Availability over time — time series, SLO target line
- **Panel 4**: Error budget consumption trend — time series, 30d projection
- **Panel 5**: Top error contributors — table (by endpoint, status code)

## <org> Specifics

- Stack: VictoriaMetrics (metrics) + VMAlert (rules) + Alertmanager (routing)
- Alert channels: `#eks-notifications-workload-prd` (SEV1-2), `#eks-notifications-teams` (SEV3-4)
- Datasource UID: `victoriametrics`
- Recording rules namespace: `monitoring`
- SLO review cadence: quarterly (with service owners)

## Anti-patterns

- ❌ 100% availability target (impossible, blocks all innovation)
- ❌ SLOs without measurement infrastructure (aspirational, not real)
- ❌ Too many SLOs per service (>5 creates confusion — pick 2-3 critical ones)
- ❌ SLOs imposed without team agreement (must be collaborative)
- ❌ Alerting on raw metrics instead of burn rate (noisy, not actionable)
- ❌ Calendar month windows (creates end-of-month anxiety, not representative)
- ❌ Same SLO for all services (tiers exist for a reason)
- ❌ Error budget without policy (budget means nothing without consequences)
- ❌ SLIs that don't reflect user experience (CPU usage is not an SLI)
- ❌ Ignoring batch services (BTC needs freshness/success SLIs, not just availability)

---

## Related skills

- `error-budget-framework` — burn rate alerting, error budget policies, multi-window alerting (companion to SLO design)
- `incident-response-runbook` — what to do when SLO is at risk
- `alerting-strategy` — symptom-based alerting (SLOs are the canonical symptom)
- `runbook-authoring` — runbooks for SLO violations and burn rate alerts
- `post-mortem-templates` — when SLO is missed, post-mortem framework
