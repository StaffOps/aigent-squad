---
name: error-budget-framework
description: "Use when implementing error budget tracking, burn rate alerting, or defining budget exhaustion policies. Covers budget calculation, multi-window burn rate alerts (Google SRE workbook), VMAlert recording rules, budget policies, and <org>-specific VictoriaMetrics patterns."
keywords: [error-budget, error, budget, "error budget", victoriametrics, vmalert, sre]
---
# Error Budget Framework

Operational framework for managing error budgets at <org>. Translates SLO targets into actionable budgets, burn rate alerts, and escalation policies using VictoriaMetrics + VMAlert.

## Concepts

### Error Budget Calculation

```
Error Budget = 1 - SLO_target
```

| SLO Target | Error Budget | Allowed Downtime (30d) | Allowed Errors (1M requests) |
|-----------|-------------|----------------------|------------------------------|
| 99.99% | 0.01% | 4.32 min | 100 |
| 99.95% | 0.05% | 21.6 min | 500 |
| 99.9% | 0.1% | 43.2 min | 1,000 |
| 99.5% | 0.5% | 3.6 hours | 5,000 |
| 99.0% | 1.0% | 7.2 hours | 10,000 |

The budget is the **maximum tolerable unreliability** within the SLO window. It's a currency — teams spend it on deployments, experiments, and migrations.

### Burn Rate

Rate at which the error budget is being consumed relative to the window:

```
burn_rate = observed_error_rate / allowed_error_rate
```

Where:
```
allowed_error_rate = 1 - SLO_target
```

| Burn Rate | Meaning | Budget Exhaustion |
|-----------|---------|-------------------|
| 0 | No errors | Never |
| 1.0 | Consuming at exactly SLO pace | End of window (30d) |
| 2.0 | 2x consumption | 15 days |
| 6.0 | 6x consumption | 5 days |
| 14.4 | 14.4x consumption | ~2 hours |
| 36.0 | 36x consumption | ~20 hours (entire budget in <1d) |

Formula for time-to-exhaustion:
```
time_to_exhaust = window_duration / burn_rate
  Example: 30d / 14.4 = 2.08 days ≈ 50 hours
```

## Multi-Window Burn Rate Alerting

### Why multi-window?

A single-window burn rate alert has two failure modes:
- **Too sensitive** (short window): fires on transient spikes → false positives
- **Too slow** (long window): detects issues too late → budget already gone

Solution: **two windows per alert** — a long window confirms sustained impact, a short window ensures recent activity.

### Google SRE Workbook Pattern

| Alert Level | Burn Rate | Long Window | Short Window | Action | % Budget Consumed |
|-------------|-----------|-------------|--------------|--------|-------------------|
| **Page (SEV1)** | 14.4x | 1h | 5m | Immediate response | 2% in 1h |
| **Page (SEV2)** | 6x | 6h | 30m | Urgent response | 5% in 6h |
| **Ticket (SEV3)** | 3x | 1d | 2h | Fix within SLA | 10% in 1d |
| **Review** | 1x | 3d | 6h | Investigate trend | 10% in 3d |

Both windows must exceed the threshold simultaneously:
```
ALERT IF burn_rate(long_window) > threshold AND burn_rate(short_window) > threshold
```

### Why these specific numbers?

- **14.4x**: at this rate, 100% of a 30-day budget is consumed in ~50 hours. The 1h window catches it after ~2% is gone.
- **6x**: budget gone in 5 days. The 6h window catches it after ~5% consumed.
- **3x**: budget gone in 10 days. The 1d window catches it after ~10%.
- **1x**: on track to exhaust exactly at window end. Low urgency but worth tracking.

## <org> Implementation — VictoriaMetrics

### Recording Rules (SLI base)

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: sli-error-budget-recording
  namespace: monitoring
spec:
  groups:
    - name: sli.error_rate
      interval: 30s
      rules:
        # Error rate (5m smoothed)
        - record: sli:http_error_rate:ratio_rate5m
          expr: |
            sum(rate(http_server_request_duration_seconds_count{http_status_code=~"5.."}[5m])) by (service_name, cluster)
            /
            sum(rate(http_server_request_duration_seconds_count[5m])) by (service_name, cluster)

        # Availability (inverse of error rate)
        - record: sli:http_availability:ratio_rate5m
          expr: |
            1 - sli:http_error_rate:ratio_rate5m

        # Latency SLI (% requests under threshold)
        - record: sli:http_latency_good:ratio_rate5m
          expr: |
            sum(rate(http_server_request_duration_seconds_bucket{le="0.5"}[5m])) by (service_name, cluster)
            /
            sum(rate(http_server_request_duration_seconds_count[5m])) by (service_name, cluster)

    - name: sli.burn_rate
      interval: 30s
      rules:
        # Burn rate over multiple windows (availability)
        - record: sli:burn_rate_5m:availability
          expr: |
            sli:http_error_rate:ratio_rate5m / (1 - 0.999)

        - record: sli:burn_rate_30m:availability
          expr: |
            (
              sum(increase(http_server_request_duration_seconds_count{http_status_code=~"5.."}[30m])) by (service_name, cluster)
              /
              sum(increase(http_server_request_duration_seconds_count[30m])) by (service_name, cluster)
            ) / (1 - 0.999)

        - record: sli:burn_rate_1h:availability
          expr: |
            (
              sum(increase(http_server_request_duration_seconds_count{http_status_code=~"5.."}[1h])) by (service_name, cluster)
              /
              sum(increase(http_server_request_duration_seconds_count[1h])) by (service_name, cluster)
            ) / (1 - 0.999)

        - record: sli:burn_rate_6h:availability
          expr: |
            (
              sum(increase(http_server_request_duration_seconds_count{http_status_code=~"5.."}[6h])) by (service_name, cluster)
              /
              sum(increase(http_server_request_duration_seconds_count[6h])) by (service_name, cluster)
            ) / (1 - 0.999)

        - record: sli:burn_rate_1d:availability
          expr: |
            (
              sum(increase(http_server_request_duration_seconds_count{http_status_code=~"5.."}[1d])) by (service_name, cluster)
              /
              sum(increase(http_server_request_duration_seconds_count[1d])) by (service_name, cluster)
            ) / (1 - 0.999)

        - record: sli:burn_rate_3d:availability
          expr: |
            (
              sum(increase(http_server_request_duration_seconds_count{http_status_code=~"5.."}[3d])) by (service_name, cluster)
              /
              sum(increase(http_server_request_duration_seconds_count[3d])) by (service_name, cluster)
            ) / (1 - 0.999)
```

### Alerting Rules (Multi-Window)

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMRule
metadata:
  name: slo-burn-rate-multiwindow
  namespace: monitoring
spec:
  groups:
    - name: slo.burn_rate.page
      rules:
        # SEV1: 14.4x burn rate (1h long + 5m short)
        - alert: SLOBudgetBurnCritical
          expr: |
            sli:burn_rate_1h:availability > 14.4
            and
            sli:burn_rate_5m:availability > 14.4
          for: 1m
          labels:
            severity: critical
            tier: slo
          annotations:
            summary: "🔥 SLO budget burning critically for {{ $labels.service_name }}"
            description: "Burn rate 14.4x — error budget exhausted in ~2h. Immediate action required."
            runbook_url: "https://gitlab.<org-domain>/devops/runbooks/-/blob/main/slo-burn-rate-critical.md"
            grafana_url: "https://grafana.<org-domain>/d/slo-overview?var-service={{ $labels.service_name }}&var-cluster={{ $labels.cluster }}"

        # SEV2: 6x burn rate (6h long + 30m short)
        - alert: SLOBudgetBurnHigh
          expr: |
            sli:burn_rate_6h:availability > 6
            and
            sli:burn_rate_30m:availability > 6
          for: 5m
          labels:
            severity: warning
            tier: slo
          annotations:
            summary: "⚠️ SLO budget burning fast for {{ $labels.service_name }}"
            description: "Burn rate 6x — error budget exhausted in ~5 days."
            runbook_url: "https://gitlab.<org-domain>/devops/runbooks/-/blob/main/slo-burn-rate-high.md"
            grafana_url: "https://grafana.<org-domain>/d/slo-overview?var-service={{ $labels.service_name }}&var-cluster={{ $labels.cluster }}"

    - name: slo.burn_rate.ticket
      rules:
        # SEV3: 3x burn rate (1d long + 2h short)
        - alert: SLOBudgetBurnElevated
          expr: |
            sli:burn_rate_1d:availability > 3
            and
            sli:burn_rate_1h:availability > 3
          for: 15m
          labels:
            severity: info
            tier: slo
          annotations:
            summary: "📋 SLO budget consumption elevated for {{ $labels.service_name }}"
            description: "Burn rate 3x — budget exhausted in ~10 days. Create ticket."
            runbook_url: "https://gitlab.<org-domain>/devops/runbooks/-/blob/main/slo-burn-rate-elevated.md"

        # Review: 1x burn rate (3d long + 6h short)
        - alert: SLOBudgetBurnSteady
          expr: |
            sli:burn_rate_3d:availability > 1
            and
            sli:burn_rate_6h:availability > 1
          for: 30m
          labels:
            severity: info
            tier: slo
          annotations:
            summary: "📊 SLO budget on track to exhaust for {{ $labels.service_name }}"
            description: "Burn rate 1x — budget will exhaust by end of window. Review."
```

### Budget Remaining Calculation

```yaml
        - record: sli:error_budget:remaining_ratio_30d
          expr: |
            1 - (
              sum_over_time(sli:http_error_rate:ratio_rate5m[30d])
              /
              (30 * 24 * 60 / 5)  # number of 5m intervals in 30d
            ) / (1 - 0.999)
```

## Error Budget Policy

### Policy Tiers

| Budget Remaining | Status | Actions |
|-----------------|--------|---------|
| **> 50%** | 🟢 Healthy | Normal operations. Deploy freely. Experiment. |
| **25–50%** | 🟡 Caution | Review recent deploys. Increase test coverage. No risky experiments. |
| **5–25%** | 🟠 At Risk | Freeze non-critical deploys. Prioritize reliability work. IC reviews all changes. |
| **0–5%** | 🔴 Critical | Feature freeze. All engineering on reliability. SRE approval for ANY change. |
| **Exhausted (< 0%)** | ⛔ Exhausted | Complete deploy freeze until budget recovers. Postmortem required. |

### Policy Enforcement

1. **Automated**: Grafana dashboard shows budget status per service
2. **Process**: Weekly SLO review meeting checks budget consumption
3. **Escalation**: Budget < 25% triggers Slack notification to team lead
4. **Freeze**: Budget exhausted → ArgoCD sync disabled (manual override by IC only)

### Budget Recovery

Budget recovers naturally as the rolling window advances:
- Old errors "fall off" the 30-day window
- Recovery rate depends on current error rate
- If error rate drops to 0, full recovery in 30 days

Accelerated recovery:
- Fix root cause (reduce error rate to near-zero)
- Budget recovers proportionally as bad data ages out

## Alertmanager Routing (<org>)

```yaml
routes:
  - matchers:
      - tier = slo
      - severity = critical
    receiver: slack-prd-workload
    # → #eks-notifications-workload-prd
    repeat_interval: 15m

  - matchers:
      - tier = slo
      - severity = warning
    receiver: slack-prd-workload
    # → #eks-notifications-workload-prd
    repeat_interval: 1h

  - matchers:
      - tier = slo
      - severity = info
    receiver: slack-teams
    # → #eks-notifications-teams
    repeat_interval: 4h
```

## Grafana Dashboard

SLO Error Budget dashboard (datasource UID: `victoriametrics`):

| Panel | Query | Visualization |
|-------|-------|---------------|
| Budget Remaining (%) | `sli:error_budget:remaining_ratio_30d * 100` | Gauge (thresholds: 50/25/5/0) |
| Current Burn Rate | `sli:burn_rate_1h:availability` | Stat (thresholds: 1/3/6/14.4) |
| Budget Consumption (30d) | `1 - sli:error_budget:remaining_ratio_30d` | Time series |
| Error Rate vs Budget Line | `sli:http_error_rate:ratio_rate5m` + threshold annotation at `1 - 0.999` | Time series |
| Time to Exhaustion | `30 * 24 / sli:burn_rate_1h:availability` (hours) | Stat |

## Anti-patterns

- ❌ **Single-window burn rate alert** — fires on transient spikes, misses sustained issues. Always use multi-window (long + short).
- ❌ **Policy without teeth** — "we'll prioritize reliability" means nothing without deploy freeze enforcement.
- ❌ **Budget without ownership** — each service must have a team that owns its budget.
- ❌ **Alerting only on exhaustion** — by then it's too late. Alert on burn rate (predictive).
- ❌ **Same SLO for all services** — Tier 1 (99.95%) vs Tier 3 (99.5%) have vastly different budgets.
- ❌ **Manual budget tracking** — must be automated via recording rules + dashboards.
- ❌ **Ignoring BTC workloads** — batch services need freshness/success SLIs, not just availability.
- ❌ **Budget as punishment** — it's a tool for informed decisions, not a stick to beat teams.
- ❌ **No executive sponsorship** — budget policy requires management backing to enforce freezes.
- ❌ **Hardcoded SLO in recording rules** — parameterize via labels or separate rules per tier.

## Reference

- Google SRE Workbook Ch. 5: Alerting on SLOs
- Related skills: `sla-slo-design`, `alerting-strategy`, `vmalert-configuration`
- <org> stack: VictoriaMetrics (`vm-cluster-vmselect.monitoring:8481`) + VMAlert + Alertmanager (`https://alertmanager.<org-domain>`)
- Grafana: `https://grafana.<org-domain>`
