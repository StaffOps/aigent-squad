---
name: monitoring-stack-overview
description: BDC monitoring stack architecture overview. Use when understanding how signals flow from applications to backends, choosing which tool to query, or debugging missing telemetry. Covers the complete pipeline from app SDK to Grafana visualization.
keywords: [monitoring-stack, monitoring, stack, "monitoring stack", grafana, pipeline, sdk]
---
# BDC Monitoring Stack Overview

> ⚠️ **Pipeline atual usa Kafka como buffer** (agent→gateway→Kafka→process). O fluxo
> descrito abaixo é o **pré-Kafka**. Estado atual (SSOT):
> `devops-platform-knowledge/architecture/observability-stack.md`.

## Signal flow

### Metrics
```
App SDK → OTel Collector (agent DaemonSet) → OTel Collector (gateway StatefulSet)
  → VictoriaMetrics vminsert → vmstorage → vmselect → Grafana
```

### Traces
```
App SDK → OTel Collector (agent DaemonSet) → OTel Collector (gateway StatefulSet)
  → Tempo (distributor → ingester → backend storage) → Grafana
```

### Logs (current — Fluent Bit)
```
App stdout → Fluent Bit DaemonSet → Loki (distributor → ingester → storage) → Grafana
```

### Logs (future — OTel native)
```
App SDK (OTLP logs) → OTel Collector → Loki
```

### Profiles (stand-by)
```
Pyroscope SDK removed (April 2026). Waiting for OTel Profiles signal to stabilize.
When re-enabled: App → OTel Collector pprof receiver → Pyroscope → Grafana
```

## OTel Collector topology

Three-tier architecture in the core cluster:

| Tier | Kind | Replicas | Purpose |
|------|------|----------|---------|
| Agent | DaemonSet | 1 per node | Receives from local pods, enriches with k8s metadata |
| Gateway | StatefulSet | 3 | Tail sampling, load balancing, cross-cluster aggregation |
| OTLP | Deployment | 2 | Receives from external clusters via TLS |

### Internal service DNS (core cluster)

| Component | Service | Port |
|-----------|---------|------|
| OTel Agent | `otel-agent-collector.monitoring` | 4317 (gRPC), 4318 (HTTP) |
| OTel Gateway | `otel-gateway-collector.monitoring` | 4317 |
| OTel OTLP | `opentelemetry-otlp-collector.monitoring` | 4317 |

### External endpoints (cross-cluster)

| Service | URL |
|---------|-----|
| OTel Collector (TLS) | `https://otelcollector-prd.bdc.internal:443` |
| OTel Gateway instances | `otel-gateway-{0,1,2}.bdc.app.br:4317` |
| OTel MDT | `https://otel-mdt.bdc.internal:443` |

## Backend components

| Backend | Service DNS | Port | Signal |
|---------|-------------|------|--------|
| VictoriaMetrics insert | `vm-cluster-vminsert.monitoring` | 8480 | Metrics (write) |
| VictoriaMetrics select | `vm-cluster-vmselect.monitoring` | 8481 | Metrics (read) |
| Tempo gateway | `tempo-gateway.monitoring` | 80 | Traces |
| Tempo query frontend | `tempo-query-frontend.monitoring` | 3200 | Traces (query) |
| Loki gateway | `loki-gateway.monitoring` | 80 | Logs |
| Pyroscope | `pyroscope-query-frontend.monitoring` | 4040 | Profiles |
| Alertmanager | `prometheus-alertmanager.monitoring` | 9093 | Alerts |

### External read endpoints

| Service | URL |
|---------|-----|
| VictoriaMetrics | `https://victoria-metrics-read.bdc.app.br/select/0/prometheus` |
| Loki | `https://loki.bdc.app.br` |
| Alertmanager | `https://alertmanager.bdc.app.br` |
| Grafana | `https://grafana.bdc.app.br` |

## Grafana datasources

| Name | Type | UID | Query language |
|------|------|-----|----------------|
| VictoriaMetrics | prometheus | `victoriametrics` | MetricsQL |
| Tempo | tempo | `tempo` | TraceQL |
| Loki | loki | `loki` | LogQL |
| Pyroscope | grafana-pyroscope-datasource | `pyroscope` | — |
| Alertmanager | alertmanager | `alertmanager` | — |

## Query languages

| Language | Backend | Use case | Example |
|----------|---------|----------|---------|
| MetricsQL | VictoriaMetrics | Dashboards, alerts | `rate(http_requests_total{service="api"}[5m])` |
| TraceQL | Tempo | Trace search | `{resource.service.name="api" && status=error}` |
| LogQL | Loki | Log search | `{namespace="dpm"} \|= "error" \| json` |

MetricsQL is a superset of PromQL with additional functions (e.g., `range_median`, `rollup_rate`).

## Alerting pipeline

```
VMAlert (evaluates VMRules) → fires alerts → Alertmanager → routes → Slack channels
```

| Component | Purpose |
|-----------|---------|
| VMAlert | Evaluates recording and alerting rules against VictoriaMetrics |
| VMRule | CRD defining alert conditions (PromQL/MetricsQL expressions) |
| Alertmanager | Deduplication, grouping, routing, silencing |
| Slack | Final notification destination |

Slack channels: `#eks-notifications`, `#eks-notifications-teams`, `#eks-notifications-workload-prd`

## Cross-signal correlation

Grafana enables jumping between signals using correlation:

```
Metric spike → click exemplar → Trace in Tempo → click span → Logs in Loki
```

### How it works

1. **Metric → Trace**: Exemplars on metrics carry `traceID`. Click exemplar → opens Tempo.
2. **Trace → Logs**: `tracesToLogsV2` datasource config. Click span → queries Loki with `{traceID="..."}`.
3. **Trace → Metrics**: `tracesToMetrics` shows related metrics for a span's service.
4. **Logs → Trace**: Loki `derivedFields` extracts `traceID` from log lines → links to Tempo.

### Requirements for correlation

- SDK must enable exemplars: `.SetExemplarFilter(ExemplarFilterType.TraceBased)`
- Logs must include `traceID`/`spanID` (OTel SDK does this automatically)
- Grafana datasources must have correlation configs set (UIDs referenced above)

## When to use what

| Need | Signal | Tool |
|------|--------|------|
| Dashboard / SLO / alert | Metrics | VictoriaMetrics + Grafana |
| Request debugging / latency analysis | Traces | Tempo + Grafana |
| Detailed error context / stack traces | Logs | Loki + Grafana |
| CPU/memory hotspot analysis | Profiles | Pyroscope + Grafana (stand-by) |
| Alert routing / silencing | Alerts | Alertmanager |

## Anti-patterns

- ❌ Querying Tempo for dashboards (traces are sampled — use metrics for aggregates)
- ❌ Storing high-cardinality data in metrics (use traces or logs for user_id, request_id)
- ❌ Skipping OTel Collector (direct backend export breaks sampling, enrichment, routing)
- ❌ Using Loki for metrics-style aggregation (LogQL `rate()` is expensive at scale)
- ❌ Alerting on trace data (traces are sampled — alert on metrics)
- ❌ Ignoring exemplars (losing metric-to-trace correlation)
- ❌ Querying vminsert endpoint for reads (use vmselect)
- ❌ Sending logs via OTLP AND stdout (duplicate ingestion, double cost)

## Related skills

- `otel-collector-multi-cluster` — collector pipeline details
- `victoriametrics-troubleshooting` — VM cluster debugging
- `grafana-cross-signal-correlation` — datasource correlation config
- `fluent-bit-loki-pipeline` — log collection details
- `vmalert-configuration` — alerting rules setup
- `alertmanager-slack-config` — Slack routing
