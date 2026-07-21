---
name: alerting-strategy
description: Use when designing alerting rules, reducing alert fatigue, configuring Alertmanager routing, or evaluating alert quality. Covers symptom-based alerting philosophy, severity levels, quality metrics (MTTA/MTTR), routing trees, silence/inhibit patterns, and <org>-specific Slack channel routing.
keywords: [alerting, alert, alertmanager, mtta, mttr]
---
# Alerting Strategy

Philosophy and implementation patterns for effective alerting at <org>. Goal: every alert is actionable, routed correctly, and leads to resolution.

## Alert Philosophy

### Symptom-Based Alerting

Alert on **what users experience** (symptoms), not what the system is doing internally (causes).

| ❌ Cause-Based (Avoid) | ✅ Symptom-Based (Prefer) |
|------------------------|--------------------------|
| CPU > 80% | Request latency p99 > 500ms |
| Memory > 90% | Error rate > 1% |
| Disk > 85% | Availability < 99.9% (burn rate) |
| Pod restarts > 3 | Health endpoint failing |
| Queue depth > 1000 | Processing freshness > SLA |

Why:
- Cause-based alerts fire without user impact (false positives)
- Symptom-based alerts fire only when users are affected (actionable)
- Causes are useful as **dashboard panels**, not alerts

### Exception: Infrastructure Alerts

Some cause-based alerts are valid when they predict imminent failure:

| Alert | Justification |
|-------|---------------|
| Disk > 90% | Imminent data loss (no self-healing) |
| Certificate expiry < 7d | Imminent TLS failure |
| Node NotReady | Imminent pod eviction |
| PVC near capacity | Imminent write failure |

These are **predictive** — they alert before symptoms appear because recovery is slow.

## Severity Levels

| Level | Destination | Response | SLA | Examples |
|-------|-------------|----------|-----|----------|
| **Critical (Page)** | `#eks-notifications-workload-prd` + on-call | Immediate (5 min) | Resolve in 1h | SLO burn rate >14.4x, service down |
| **Warning (Page)** | `#eks-notifications-workload-prd` | Urgent (15 min) | Resolve in 4h | SLO burn rate >6x, degraded |
| **Info (Ticket)** | `#eks-notifications-teams` | Business hours | Resolve in 1 sprint | SLO burn rate >3x, capacity trending |
| **None (Log)** | Dashboard only | No response | None | Informational, context for investigation |

### Severity Assignment Rules

- **Critical**: customer-facing outage OR data loss risk OR security breach
- **Warning**: degraded service OR elevated error rate OR approaching limits
- **Info**: internal impact OR trend requiring attention OR non-urgent maintenance
- **None**: useful for dashboards but no human action needed

## Alert Quality Metrics

| Metric | Definition | Target | How to Measure |
|--------|-----------|--------|----------------|
| **MTTA** | Mean Time to Acknowledge | < 5 min (SEV1) | Time from alert fire → first human response |
| **MTTR** | Mean Time to Resolve | < 1h (SEV1) | Time from alert fire → resolution |
| **False Positive Rate** | Alerts that fire without real impact | < 5% | Alerts resolved without action / total alerts |
| **Alert Flap Rate** | Alerts that fire and resolve repeatedly | < 2% | Alerts with >3 state changes in 1h |
| **Actionability** | Alerts that result in human action | > 90% | Alerts with associated action / total alerts |
| **Coverage** | Incidents detected by alerts (vs reported) | > 95% | Alert-detected incidents / total incidents |

### Weekly Alert Review

Every week, review:
1. Total alerts fired (trending up = problem)
2. Top 5 noisiest alerts (candidates for tuning or deletion)
3. Alerts nobody acted on (candidates for deletion)
4. Incidents without prior alert (coverage gap)
5. False positives (tune threshold or add condition)

Target: **< 10 actionable alerts per day** across all channels.

## Alertmanager Routing (<org>)

### Routing Tree

```yaml
route:
  receiver: slack-default
  group_by: ['alertname', 'cluster', 'namespace']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # SLO burn rate alerts → workload channel
    - matchers:
        - tier = slo
        - severity =~ "critical|warning"
      receiver: slack-workload-prd
      repeat_interval: 15m

    # Critical infra alerts → main channel
    - matchers:
        - severity = critical
        - tier != slo
      receiver: slack-critical
      repeat_interval: 30m

    # Warning alerts → main channel
    - matchers:
        - severity = warning
      receiver: slack-critical-warning
      repeat_interval: 1h

    # Team-specific routing
    - matchers:
        - team = dpm
      receiver: slack-team-dpm
      repeat_interval: 4h

    - matchers:
        - team = dcp
      receiver: slack-team-dcp
      repeat_interval: 4h

    # ArgoCD sync alerts → dedicated channel
    - matchers:
        - alertname =~ "Argocd.*"
      receiver: slack-argo
      repeat_interval: 2h

    # Dev workload alerts (lower priority)
    - matchers:
        - cluster = <org>-workloads-dev-nv
      receiver: slack-workload-dev
      repeat_interval: 8h

    # Silence noisy/managed alerts
    - matchers:
        - alertname =~ "Watchdog|InfoInhibitor|KubeControllerManagerDown|KubeSchedulerDown"
      receiver: 'null'
```

### <org> Slack Channels

| Channel | Receiver | Content |
|---------|----------|---------|
| `#eks-notifications` | slack-critical-warning | General cluster alerts (critical + warning) |
| `#eks-notifications-workload-prd` | slack-workload-prd | Production workload alerts + SLO burns |
| `#eks-notifications-workload-dev` | slack-workload-dev | Dev workload alerts (lower urgency) |
| `#eks-notifications-teams` | slack-team-* | Team-specific routing |
| `#eks-notifications-argo` | slack-argo | ArgoCD sync failures |

### Grouping Strategy

```yaml
group_by: ['alertname', 'cluster', 'namespace']
```

- Groups related alerts into single notification
- Prevents Slack flood during cascading failures
- `group_wait: 30s` — wait for related alerts before sending
- `group_interval: 5m` — minimum time between group updates

## Silence Patterns

### Planned Maintenance

```bash
# Silence all alerts for namespace during maintenance window
amtool silence add \
  namespace="monitoring" \
  --duration=2h \
  --comment="Planned maintenance: VM cluster upgrade" \
  --author="karlipegomes"

# Silence specific alert
amtool silence add \
  alertname="VMStorageDiskSpaceCritical" \
  --duration=1h \
  --comment="Disk expansion in progress"
```

### Via Alertmanager API

```bash
curl -X POST https://alertmanager.<org-domain>/api/v2/silences \
  -H "Content-Type: application/json" \
  -d '{
    "matchers": [{"name": "namespace", "value": "monitoring", "isRegex": false}],
    "startsAt": "2026-05-29T02:00:00Z",
    "endsAt": "2026-05-29T04:00:00Z",
    "createdBy": "karlipegomes",
    "comment": "Planned maintenance: Tempo upgrade"
  }'
```

### Rules

- Always set **end time** (no indefinite silences)
- Always add **comment** explaining why
- Review active silences weekly (stale silences = hidden problems)
- Silence the **specific alert**, not broad matchers (avoid masking real issues)

## Inhibition Rules

Suppress child alerts when parent fires:

```yaml
inhibit_rules:
  # Node down → suppress all pod alerts on that node
  - source_matchers:
      - alertname = KubeNodeNotReady
    target_matchers:
      - severity =~ "warning|info"
    equal: [node]

  # Cluster unreachable → suppress all namespace alerts
  - source_matchers:
      - alertname = ClusterUnreachable
    target_matchers:
      - severity =~ "critical|warning|info"
    equal: [cluster]

  # Critical fires → suppress warning for same alert
  - source_matchers:
      - severity = critical
    target_matchers:
      - severity = warning
    equal: [alertname, cluster, namespace]
```

## Multi-Window Burn Rate (Cross-Reference)

For SLO-based alerting, use multi-window burn rate pattern:

| Burn Rate | Windows | Severity | Action |
|-----------|---------|----------|--------|
| 14.4x | 1h + 5m | Critical (page) | Immediate response |
| 6x | 6h + 30m | Warning (page) | Urgent response |
| 3x | 1d + 2h | Info (ticket) | Fix in sprint |
| 1x | 3d + 6h | None (review) | Monitor trend |

Full implementation: see skill `error-budget-framework`.

## Alert Annotation Standards

Every VMRule alert MUST include:

```yaml
annotations:
  summary: "Human-readable one-liner with {{ $labels.service_name }}"
  description: "Detailed explanation with current value: {{ $value }}"
  runbook_url: "https://gitlab.<org-domain>/devops/runbooks/-/blob/main/<alert-name>.md"
  grafana_url: "https://grafana.<org-domain>/d/<dashboard>?var-service={{ $labels.service_name }}"
```

- `summary`: appears in Slack notification title
- `description`: appears in Slack body
- `runbook_url`: links to resolution steps (see skill `runbook-authoring`)
- `grafana_url`: deep link to relevant dashboard with variables pre-filled

## Alert Lifecycle

```
Design → Implement → Test → Deploy → Monitor → Tune → Retire
```

1. **Design**: symptom-based, severity assigned, runbook written
2. **Implement**: VMRule CRD with proper annotations
3. **Test**: verify fires correctly (inject failure or use `vmalert` dry-run)
4. **Deploy**: GitOps via ArgoCD (monitoring namespace)
5. **Monitor**: track false positive rate, MTTA, actionability
6. **Tune**: adjust thresholds based on real data (quarterly review)
7. **Retire**: delete alerts nobody acts on (30-day inactivity rule)

## Anti-patterns

- ❌ **Alert fatigue** — too many alerts desensitize responders. Target <10/day actionable.
- ❌ **Missing SLO-based alerts** — alerting on causes without burn rate alerts for user impact.
- ❌ **Severity inflation** — everything is "critical" → nothing is critical.
- ❌ **No runbook_url** — alert fires but responder doesn't know what to do.
- ❌ **Static thresholds only** — "CPU > 80%" without considering normal baseline.
- ❌ **Alert without owner** — nobody knows who should respond.
- ❌ **Duplicate alerts** — same symptom detected by 3 different rules (consolidate).
- ❌ **Indefinite silences** — silence without end time hides real problems.
- ❌ **Broad inhibition** — inhibiting too many alerts masks cascading failures.
- ❌ **No alert review cadence** — alerts accumulate, quality degrades over time.
- ❌ **Alerting on every metric** — metrics are for dashboards; alerts are for action.
- ❌ **Copy-paste alerts from upstream** — kubernetes-mixin alerts without <org> context tuning.

## Reference

- Google SRE Book Ch. 6: Monitoring Distributed Systems
- Rob Ewaschuk: "My Philosophy on Alerting"
- Related skills: `error-budget-framework`, `alertmanager-slack-config`, `vmalert-configuration`, `runbook-authoring`
- <org> Alertmanager: `https://alertmanager.<org-domain>`
- <org> Grafana: `https://grafana.<org-domain>`
- Slack channels: `#eks-notifications`, `#eks-notifications-workload-prd`, `#eks-notifications-teams`
