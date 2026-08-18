---
name: tempo-traceql-patterns
description: "Use when querying Tempo with TraceQL, searching traces by attributes, debugging distributed traces, or correlating exemplars from VictoriaMetrics to traces. Covers TraceQL syntax, span set operators, service graph, <org> endpoints, and common query patterns."
keywords: [tempo-traceql, tempo, traceql, "tempo traceql", victoriametrics]
---
# Tempo TraceQL Patterns

TraceQL query language patterns for Grafana Tempo at <org> — syntax, common queries, service graph, exemplar correlation, and operational patterns.

## <org> Tempo Infrastructure

| Component | Service DNS | Port | Cluster |
|-----------|-------------|------|---------|
| Tempo Gateway | `tempo-gateway.monitoring` | 80 | core-devops |
| Query Frontend | `tempo-query-frontend.monitoring` | 3200 | core-devops |
| External URL | `https://tempo.<org-domain>` | 443 | — |

Grafana datasource:

| Field | Value |
|-------|-------|
| Name | Tempo |
| Type | `tempo` |
| UID | `tempo` |
| URL | `http://tempo-query-frontend.monitoring:3200` |

## TraceQL Fundamentals

### Basic syntax

```
{ <spanset filter> }
```

A spanset filter selects spans matching conditions. Conditions use span intrinsics or attributes.

### Intrinsic fields

| Field | Type | Description |
|-------|------|-------------|
| `duration` | duration | Span duration |
| `status` | enum | `ok`, `error`, `unset` |
| `kind` | enum | `server`, `client`, `producer`, `consumer`, `internal` |
| `name` | string | Span name (operation) |
| `rootName` | string | Root span name of the trace |
| `rootServiceName` | string | Root span's service name |
| `traceDuration` | duration | Total trace duration |

### Attribute scopes

| Scope | Syntax | Example |
|-------|--------|---------|
| Span attribute | `span.<attr>` or `.<attr>` | `span.http.status_code` |
| Resource attribute | `resource.<attr>` | `resource.service.name` |
| Unscoped (any) | `<attr>` | `http.method` (searches both) |

### Comparison operators

| Operator | Meaning |
|----------|---------|
| `=` | Equals |
| `!=` | Not equals |
| `>`, `>=`, `<`, `<=` | Numeric/duration comparison |
| `=~` | Regex match |
| `!~` | Regex not match |
| `&&` | AND |
| `\|\|` | OR |

## Common Query Patterns

### Find slow traces

```traceql
{ duration > 1s }
```

### Find errors

```traceql
{ status = error }
```

### Specific service

```traceql
{ resource.service.name = "dpm-people-api" }
```

### Combined: slow errors for a service

```traceql
{ resource.service.name = "dpm-people-api" && status = error && duration > 500ms }
```

### By HTTP route

```traceql
{ span.http.route = "/api/v1/people" && span.http.status_code >= 500 }
```

### By environment

```traceql
{ resource.deployment.environment = "PRD" && status = error }
```

### By cluster

```traceql
{ resource.eks_cluster = "prd" && duration > 2s }
```

### Regex match on service name

```traceql
{ resource.service.name =~ "dpm-.*" && status = error }
```

### Root span only

```traceql
{ rootServiceName = "dpm-people-api" && traceDuration > 3s }
```

### gRPC errors

```traceql
{ span.rpc.system = "grpc" && span.rpc.grpc.status_code != 0 }
```

## Span Set Operators

### Descendant (`>>`)

Find traces where service A calls service B (anywhere in the tree):

```traceql
{ resource.service.name = "dotnet-api" } >> { resource.service.name = "dotnet-backend" }
```

### Child (`>`)

Direct parent-child relationship:

```traceql
{ resource.service.name = "dotnet-api" } > { resource.service.name = "dotnet-backend" && status = error }
```

### Sibling (`~`)

Spans sharing the same parent:

```traceql
{ resource.service.name = "service-a" } ~ { resource.service.name = "service-b" }
```

### Coalesce (`&&` at trace level)

Both conditions must exist in the same trace:

```traceql
{ resource.service.name = "frontend" } && { resource.service.name = "backend" && status = error }
```

## Aggregations

### Count spans per trace

```traceql
{ resource.service.name = "dpm-people-api" } | count() > 50
```

### Average duration

```traceql
{ resource.service.name = "dpm-people-api" } | avg(duration) > 200ms
```

### Max duration

```traceql
{ resource.service.name = "dpm-people-api" } | max(duration) > 5s
```

### Select specific fields

```traceql
{ status = error } | select(resource.service.name, span.http.route, duration)
```

## Trace Search vs Trace by ID

### Search (TraceQL)

Use Grafana Explore → Tempo datasource → "Search" tab or "TraceQL" tab.

```bash
# Direct API query from core-devops cluster
kubectl run tempo-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s \
  "http://tempo-gateway.monitoring:80/api/search?q=%7B+status+%3D+error+%7D&limit=20&start=$(date -d '1 hour ago' +%s)&end=$(date +%s)"
```

### By Trace ID

```bash
kubectl run tempo-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s \
  "http://tempo-gateway.monitoring:80/api/traces/<trace-id>"
```

In Grafana: paste trace ID directly in the search bar.

## Exemplar Correlation (VM → Tempo)

VictoriaMetrics stores exemplars linking metric data points to trace IDs.

### Flow

```
App (OTel SDK with exemplars) → VM (stores trace_id as exemplar)
                                      ↓
Grafana metric panel → click exemplar dot → opens trace in Tempo
```

### Requirements

1. **App side**: enable exemplar filter
   ```csharp
   // .NET
   .SetExemplarFilter(ExemplarFilterType.TraceBased)
   ```

2. **VM datasource**: configure exemplar destination
   ```yaml
   exemplarTraceIdDestinations:
     - name: traceID
       datasourceUid: tempo
   ```

3. **Grafana panel**: enable "Exemplars" toggle on time series panels

### Query exemplars via API

```promql
# In VM, exemplars appear as annotations on metric queries
sum(rate(spanmetrics_apm_calls_total{service_name="dpm-people-api"}[5m]))
```

Click the diamond-shaped dots on the graph → jumps to Tempo trace.

## Service Graph

Tempo generates service graph metrics via the `spanmetrics` connector in OTel Collector.

### Grafana configuration

```yaml
# Tempo datasource
serviceMap:
  datasourceUid: victoriametrics
nodeGraph:
  enabled: true
```

### Metrics used

| Metric | Purpose |
|--------|---------|
| `traces_service_graph_request_total` | Request count between services |
| `traces_service_graph_request_failed_total` | Failed requests |
| `traces_service_graph_request_server_seconds_*` | Latency histograms |

### View in Grafana

Tempo datasource → "Service Graph" tab → visual topology of service-to-service calls.

## Cross-Signal Links from Tempo

| Destination | Config key | Datasource UID |
|-------------|-----------|----------------|
| Loki (logs) | `tracesToLogsV2` | `loki` |
| VictoriaMetrics (metrics) | `tracesToMetrics` | `victoriametrics` |
| Pyroscope (profiles) | `tracesToProfiles` | `pyroscope` |

See skill `grafana-cross-signal-correlation` for full configuration.

## Multi-Cluster Trace Routing

Traces from all 3 clusters flow to Tempo on core-devops:

```
<org>-workloads-dev-nv  → OTel Agent → OTel Gateway → Tempo (core-devops)
<org>-workloads-prd-nv  → OTel Agent → OTel Gateway → Tempo (core-devops)
<org>-eks-prd (core)    → OTel Agent → Tempo (local)
```

Resource attributes identify origin:
- `eks_cluster`: `core` / `dev` / `prd`
- `cluster`: `<org>-eks-prd` / `<org>-workloads-dev-nv` / `<org>-workloads-prd-nv`
- `deployment.environment`: `LOCAL` / `DEV` / `HML` / `PRD` / `BTC`

## Tail Sampling Impact on Queries

Remember: PRD keeps only 10% probabilistic + 100% errors/high-latency. Queries in PRD:
- ✅ All error traces are present
- ✅ All slow traces (>1s) are present
- ⚠️ Normal traces are sampled — counts are approximate
- ✅ DEV/HML/LOCAL: 100% of traces retained

## Anti-patterns

- ❌ **Very wide queries without filters** — `{}` scans ALL traces. Always filter by service, time range, or status.
- ❌ **Missing `resource.service.name`** — traces without service name are unattributable. <org> OTel Helper sets this from `SERVICE_NAME` env var.
- ❌ **High-cardinality attributes in queries** — filtering by `span.user_id` or `span.request_id` is valid for point lookups but NOT for aggregations.
- ❌ **Using span attributes as resource attributes** — `resource.http.method` doesn't exist. Use `span.http.method`.
- ❌ **Ignoring scope** — `{ service.name = "x" }` searches unscoped (both span+resource). Be explicit: `{ resource.service.name = "x" }`.
- ❌ **Querying by tag instead of trace ID when you have it** — if you have the trace ID, use direct lookup (faster, exact).
- ❌ **Not using `traceDuration`** — to find slow end-to-end flows, use `traceDuration > 5s` instead of `duration > 5s` (which finds slow individual spans).
- ❌ **Expecting 100% traces in PRD** — tail sampling drops ~90% of normal traces. Don't use count-based analysis on sampled data.

## Reference

- Grafana Tempo docs: https://grafana.com/docs/tempo/latest/
- TraceQL: https://grafana.com/docs/tempo/latest/traceql/
- Local docs: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/tempo/docs`
- Related skills: `grafana-cross-signal-correlation`, `otel-collector-multi-cluster`, `monitoring-stack-overview`
