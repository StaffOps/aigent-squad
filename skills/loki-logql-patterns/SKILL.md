---
name: loki-logql-patterns
description: Use when querying Loki with LogQL, filtering logs by labels/content, building metric queries from logs, or correlating logs with traces. Covers LogQL syntax, parsers, structured metadata, derived fields, and <org>-specific patterns.
keywords: [loki-logql, loki, logql, "loki logql"]
---
# Loki LogQL Patterns

LogQL query language patterns for Grafana Loki at <org> — stream selectors, parsers, metric queries, structured metadata, and trace correlation.

## <org> Loki Infrastructure

| Component | Service DNS | Port | Cluster |
|-----------|-------------|------|---------|
| Loki Gateway | `loki-gateway.monitoring` | 80 | core-devops |
| External URL | `https://loki.<org-domain>` | 443 | — |

Grafana datasource:

| Field | Value |
|-------|-------|
| Name | Loki |
| Type | `loki` |
| UID | `loki` |
| URL | `http://loki-gateway.monitoring:80` |

### Log sources (via Fluent Bit)

Logs flow from all 3 clusters through Fluent Bit DaemonSets:

| Cluster | Namespaces |
|---------|-----------|
| `<org>-eks-prd` (core) | argo, monitoring, devops, nginx, kyverno, istio-system, cert-manager, etc. |
| `<org>-workloads-dev-nv` | DEV workload namespaces |
| `<org>-workloads-prd-nv` | PRD + HML + BTC workload namespaces |

## LogQL Fundamentals

### Query structure

```
{stream selector} | line filters | parsers | label filters | formatters
```

### Stream selectors (label matchers)

```logql
{service_namespace="dpm", service_workload="dpm-people-api"}
```

| Operator | Meaning |
|----------|---------|
| `=` | Equals |
| `!=` | Not equals |
| `=~` | Regex match |
| `!~` | Regex not match |

### Available labels (<org> standard)

| Label | Cardinality | Example |
|-------|-------------|---------|
| `eks_cluster` | ~3 | `core`, `dev`, `prd` |
| `service_namespace` | ~25 | `dpm`, `monitoring`, `argo` |
| `service_workload` | ~50-200 | `dpm-people-api` |
| `k8s_pod_name` | High | `dpm-people-api-7f8b9c-x2k4l` |
| `service_name` | ~50-200 | `dpm-people-api` |
| `service_workload_component` | Low | `api`, `worker` |
| `service_workload_instance` | Low | `primary`, `canary` |

### Line filters

| Operator | Meaning | Example |
|----------|---------|---------|
| `\|=` | Contains | `\|= "ERROR"` |
| `!=` | Not contains | `!= "healthz"` |
| `\|~` | Regex match | `\|~ "timeout\|deadline"` |
| `!~` | Regex not match | `!~ "DEBUG\|TRACE"` |

**Performance rule**: line filters are applied BEFORE parsing. Put them early to reduce data scanned.

## Parsers

### JSON parser

```logql
{service_workload="dpm-people-api"} | json
```

Extracts all JSON keys as labels. Use with label filter:

```logql
{service_workload="dpm-people-api"} | json | level="error"
```

### Logfmt parser

```logql
{service_workload="my-go-service"} | logfmt | level="error"
```

### Regexp parser

```logql
{service_workload="nginx"} | regexp `(?P<method>\w+) (?P<path>\S+) (?P<status>\d+)`
```

### Pattern parser (fastest for structured logs)

```logql
{service_workload="nginx"} | pattern `<method> <path> <status> <duration>`
```

### Parser selection guide

| Log format | Parser | Performance |
|-----------|--------|-------------|
| JSON | `json` | Good |
| key=value | `logfmt` | Best |
| Fixed structure | `pattern` | Best |
| Complex/mixed | `regexp` | Slowest |

## Label Filters (post-parse)

After parsing, filter on extracted labels:

```logql
{service_namespace="dpm"} | json | level="error" | status_code >= 500
```

| Operator | Types |
|----------|-------|
| `=`, `!=` | String |
| `>`, `>=`, `<`, `<=` | Numeric (auto-detected) |
| `=~`, `!~` | Regex |

## Metric Queries

Transform log streams into numeric time series.

### Rate (log lines per second)

```logql
rate({service_workload="dpm-people-api"} |= "ERROR" [5m])
```

### Sum by label

```logql
sum by (service_workload) (rate({service_namespace="dpm"} |= "ERROR" [5m]))
```

### Count over time

```logql
count_over_time({service_workload="dpm-people-api"} |= "Exception" [1h])
```

### Top-K error producers

```logql
topk(10, sum by (service_workload) (rate({eks_cluster="prd"} |= "ERROR" [5m])))
```

### Quantile (unwrap numeric field)

```logql
quantile_over_time(0.99,
  {service_workload="dpm-people-api"}
  | json
  | unwrap duration
  [5m]
) by (service_workload)
```

### Bytes rate

```logql
sum by (service_namespace) (bytes_rate({eks_cluster="prd"} [5m]))
```

## Structured Metadata (Loki 2.9+)

High-cardinality fields stored as metadata — queryable but NOT indexed as labels.

### <org> fields in structured metadata

| Field | Why metadata (not label) |
|-------|--------------------------|
| `container_name` | Medium cardinality |
| `detected_level` | Auto-detected log level |
| `trace_id` | Extremely high cardinality |
| `span_id` | Extremely high cardinality |

### Querying structured metadata

```logql
{service_workload="dpm-people-api"} | trace_id="abc123def456"
```

Structured metadata fields are available as filter targets after the stream selector, without needing a parser.

## Common Query Patterns

### All errors for a service

```logql
{service_workload="dpm-people-api"} |= "ERROR"
```

### Errors excluding health checks

```logql
{service_workload="dpm-people-api"} |= "ERROR" != "healthz" != "ready"
```

### Rate of errors per service (dashboard panel)

```logql
sum by (service_workload) (
  rate({service_namespace="dpm", eks_cluster="prd"} |= "ERROR" [5m])
)
```

### HTTP 5xx from JSON logs

```logql
{service_workload="dpm-people-api"} | json | statusCode >= 500
```

### Latency P99 from JSON logs

```logql
quantile_over_time(0.99,
  {service_workload="dpm-people-api"}
  | json
  | unwrap elapsed_ms
  [5m]
)
```

### .NET exceptions (multiline)

```logql
{service_workload="dpm-people-api"} |= "Exception" |= "at "
```

### Logs for a specific trace

```logql
{service_workload="dpm-people-api"} | trace_id="<trace-id-here>"
```

### Volume by namespace (cost visibility)

```logql
sum by (service_namespace) (bytes_over_time({eks_cluster="prd"} [24h]))
```

## Derived Fields — Trace Correlation

Loki derived fields extract trace IDs from log lines and link to Tempo.

### Grafana datasource configuration

```yaml
# Loki datasource
derivedFields:
  - matcherRegex: 'traceID=(\w+)'
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
  - matcherRegex: 'trace_id=(\w+)'
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
  - matcherRegex: '"traceId":"(\w+)"'
    name: TraceID
    url: '$${__value.raw}'
    datasourceUid: tempo
```

### How it works

1. Log line contains trace ID (e.g., `traceId=abc123`)
2. Regex extracts the value
3. Grafana shows a clickable link
4. Click → opens trace in Tempo

**Requirement**: apps must emit trace ID in logs. <org> OTel Helper does this automatically via ILogger OpenTelemetry integration (adds `traceId`/`spanId` to log scopes).

## Direct API Queries

```bash
# From core-devops cluster — instant query
kubectl run loki-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s -G \
  "http://loki-gateway.monitoring:80/loki/api/v1/query" \
  --data-urlencode 'query={service_workload="dpm-people-api"} |= "ERROR"' \
  --data-urlencode 'limit=50'

# Range query (metric)
kubectl run loki-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s -G \
  "http://loki-gateway.monitoring:80/loki/api/v1/query_range" \
  --data-urlencode 'query=sum(rate({service_namespace="dpm"} |= "ERROR" [5m]))' \
  --data-urlencode 'start=1716940800' \
  --data-urlencode 'end=1716944400' \
  --data-urlencode 'step=60'

# Label values
kubectl run loki-q -n monitoring --rm -i --restart=Never \
  --image=curlimages/curl:latest -- -s \
  "http://loki-gateway.monitoring:80/loki/api/v1/label/service_workload/values"
```

## Query Efficiency Rules

### 1. Labels first, line filters second

```logql
# ✅ Good — narrows stream first
{service_namespace="dpm", service_workload="dpm-people-api"} |= "ERROR"

# ❌ Bad — scans all streams in cluster
{eks_cluster="prd"} |= "ERROR"
```

### 2. Line filter before parser

```logql
# ✅ Good — filters before expensive JSON parse
{service_workload="x"} |= "error" | json | level="error"

# ❌ Bad — parses ALL lines then filters
{service_workload="x"} | json | level="error"
```

### 3. Avoid regex when simple contains works

```logql
# ✅ Good
{service_workload="x"} |= "timeout"

# ❌ Bad (same result, slower)
{service_workload="x"} |~ ".*timeout.*"
```

### 4. Limit time range

Always use the narrowest time range possible. Loki scans chunks sequentially — wider range = more chunks = slower.

## Anti-patterns

- ❌ **High-cardinality labels** — `request_id`, `user_id`, `trace_id` as stream labels. These create millions of streams → Loki ingester OOM. Use structured metadata instead.
- ❌ **Regex filters instead of line/label filters** — `|~ ".*ERROR.*"` is slower than `|= "ERROR"`. Use contains (`|=`) when possible.
- ❌ **Very broad queries** — `{eks_cluster="prd"}` without further label narrowing scans ALL production logs. Always add `service_namespace` or `service_workload`.
- ❌ **Parsing without line filter** — `| json` on every line is expensive. Pre-filter with `|=` to reduce parse volume.
- ❌ **Using `count_over_time` for alerting** — prefer `rate()` for alerts (per-second normalization). `count_over_time` depends on time range.
- ❌ **Querying by pod name for dashboards** — pods are ephemeral. Use `service_workload` (stable across restarts/rollouts).
- ❌ **Missing derived fields** — without trace ID extraction, log→trace correlation doesn't work. Ensure Loki datasource has `derivedFields` configured.
- ❌ **`auto_kubernetes_labels: true` in Fluent Bit** — sends ALL pod labels as Loki labels → cardinality explosion. <org> uses `false` with explicit label selection.

## Reference

- Grafana Loki docs: https://grafana.com/docs/loki/latest/
- LogQL: https://grafana.com/docs/loki/latest/query/
- Local docs: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/loki/docs`
- Related skills: `fluent-bit-loki-pipeline`, `grafana-cross-signal-correlation`, `monitoring-stack-overview`
