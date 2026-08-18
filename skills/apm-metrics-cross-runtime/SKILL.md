---
name: apm-metrics-cross-runtime
description: "Use when comparing APM metrics across .NET/Go/Node/Python runtimes, building SLI recording rules, understanding OTel\u2192Prometheus name translation, exemplar correlation, histogram pitfalls, or diagnosing saturation signals per language."
keywords: [go, node, otel, prometheus, python, sli, net]
---
# APM Metrics — Cross-Runtime Cheat Sheet

Comparative reference tying together HTTP, runtime, dependency, and SLI metrics across .NET, Go, Node.js, and Python. All metric names verified against OTel Semantic Conventions v1.43.0.

---

## 1. RED + USE Mapping Across Runtimes

The RED method (Rate, Errors, Duration) uses the **same** OTel HTTP metric across all languages. The USE method (Utilization, Saturation, Errors) uses **language-specific runtime metrics**.

Source: [OTel HTTP Metrics](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/) | [Go Runtime](https://opentelemetry.io/docs/specs/semconv/runtime/go-metrics/) | [Node.js Runtime](https://opentelemetry.io/docs/specs/semconv/runtime/nodejs-metrics/) | [.NET Runtime](https://opentelemetry.io/docs/specs/semconv/runtime/dotnet-metrics/)

| Signal | .NET | Go | Node.js | Python |
|--------|------|-----|---------|--------|
| **R**ate (req/s) | `http.server.request.duration` (count) | same | same | same |
| **E**rror rate | `http.server.request.duration{error.type!=""}` | same | same | same |
| **D**uration (p99) | `http.server.request.duration` (histogram) | same | same | same |
| **CPU** | `dotnet_process_cpu_time_seconds_total` | `go_cpu_classes_total_cpu_seconds_total` | `process_cpu_seconds_total` | `process_runtime_cpython_cpu_time_seconds_total` |
| **Memory** | `dotnet_gc_last_collection_heap_size_bytes` | `go_memstats_heap_inuse_bytes` | `nodejs_heap_size_used_bytes` | `process_runtime_cpython_memory_bytes` |
| **GC equivalent** | `dotnet_gc_collections_total` / `dotnet_gc_pause_time_seconds_total` | `go_gc_duration_seconds` / `go_gc_pauses_seconds_bucket` | `nodejs_gc_duration_seconds` | `cpython_gc_collections_total` / `python_gc_collections_total` |
| **True saturation** | `dotnet_thread_pool_queue_length_total` + `http_client_request_time_in_queue_seconds` | `go_sched_latencies_seconds` | `nodejs_eventloop_lag_p99_seconds` | *(no loop-lag metric emitted)* |

> These are the **real VictoriaMetrics names in this environment** — `client_golang` for Go, `prom-client` for Node (NOT OTel semconv `go.*`/`nodejs.*`/`v8js.*`). See the per-language skills for full detail.

### Key saturation metrics detail

| Language | OTel Metric | VM Name | Type | Unit | What it reveals |
|----------|-------------|---------|------|------|-----------------|
| .NET | *(Kestrel meter)* | `kestrel_queued_connections` | UpDownCounter | `{connection}` | Connections waiting to start — server overwhelmed |
| .NET | `http.client.request.time_in_queue` | `http_client_request_time_in_queue_seconds` | Histogram | `s` | HttpClient pool exhaustion |
| Go | *(client_golang)* | `go_sched_latencies_seconds_bucket` | Histogram | `s` | Goroutines waiting for CPU — scheduler saturation |
| Node.js | *(prom-client)* | `nodejs_eventloop_lag_p99_seconds` | Gauge | `s` | Event loop lag p99; rising = loop saturated |
| Python | *(none emitted)* | — | — | — | No loop-lag metric present; would need custom instrumentation |

> ⚠️ In this environment: .NET Kestrel emits `kestrel_queued_connections`/`kestrel_queued_requests` (there is **no** `kestrel_rejected_connections`). Go uses `client_golang` and Node uses `prom-client` — OTel-native `go.*`/`nodejs.*`/`v8js.*` semconv is **not** emitted here. See the `go-apm-metrics` and `nodejs-apm-metrics` skills for the real names.

---

## 2. Dependency Metrics (Common to All Languages)

These metrics are emitted by OTel SDK instrumentations regardless of language.

### RPC (gRPC) — Release Candidate

Source: [OTel RPC Metrics semconv v1.43.0](https://opentelemetry.io/docs/specs/semconv/rpc/rpc-metrics/)

| OTel Name | VM/Prometheus Name | Type | Unit | Measures | Key Attributes |
|-----------|--------------------|------|------|----------|----------------|
| `rpc.server.call.duration` | `rpc_server_call_duration_seconds` | Histogram | `s` | Incoming RPC duration | `rpc.system.name`, `rpc.method`, `rpc.response.status_code`, `error.type` |
| `rpc.client.call.duration` | `rpc_client_call_duration_seconds` | Histogram | `s` | Outgoing RPC duration | `rpc.system.name`, `rpc.method`, `rpc.response.status_code`, `server.address`, `error.type` |

> **Attribute note (semconv 1.43.0)**: there is **no** `rpc.service` attribute — `rpc.method` carries the **fully-qualified** method name (e.g. `com.example.ExampleService/exampleMethod`), which incorporates the service. Older instrumentations (semconv ≤1.37) used a separate `rpc.service`; gate the new conventions via `OTEL_SEMCONV_STABILITY_OPT_IN=rpc`.

### Database — Stable

Source: [OTel Database Metrics semconv v1.43.0](https://opentelemetry.io/docs/specs/semconv/database/database-metrics/)

| OTel Name | VM/Prometheus Name | Type | Unit | Measures | Key Attributes |
|-----------|--------------------|------|------|----------|----------------|
| `db.client.operation.duration` | `db_client_operation_duration_seconds` | Histogram | `s` | DB operation latency | `db.system.name`, `db.namespace`, `db.collection.name`, `db.operation.name`, `error.type` |

### Messaging — Development

Source: [OTel Messaging Metrics semconv v1.43.0](https://opentelemetry.io/docs/specs/semconv/messaging/messaging-metrics/)

| OTel Name | VM/Prometheus Name | Type | Unit | Measures | Key Attributes |
|-----------|--------------------|------|------|----------|----------------|
| `messaging.client.operation.duration` | `messaging_client_operation_duration_seconds` | Histogram | `s` | Send/receive operation duration | `messaging.system`, `messaging.operation.name`, `messaging.destination.name`, `error.type` |
| `messaging.process.duration` | `messaging_process_duration_seconds` | Histogram | `s` | Message processing duration | `messaging.system`, `messaging.operation.name`, `error.type` |
| `messaging.client.sent.messages` | `messaging_client_sent_messages_total` | Counter | `{message}` | Messages sent count | `messaging.system`, `messaging.operation.name`, `error.type` |
| `messaging.client.consumed.messages` | `messaging_client_consumed_messages_total` | Counter | `{message}` | Messages consumed count | `messaging.system`, `messaging.operation.name`, `error.type` |

---

## 3. Exemplars (Metric → Trace Correlation)

OTel exemplars attach a `trace_id` + `span_id` to individual histogram/counter data points, linking a metric spike directly to the trace that caused it.

### How it works

```
SDK: histogram.Record(value, attributes, exemplar{trace_id, span_id, timestamp})
     → exported to Collector → written to VictoriaMetrics with exemplar labels
Grafana: click histogram datapoint → follows trace_id link to Tempo
```

### SDK configuration

```csharp
// .NET — enable exemplars (trace-based filter)
.SetExemplarFilter(ExemplarFilterType.TraceBased)
```

All languages use the same pattern: `ExemplarFilter = TraceBased` means exemplars are attached only when a trace is active.

### VictoriaMetrics exemplar support

VM supports exemplars on histograms and counters. Query with:
```promql
# Exemplars are visible in Grafana when the datasource has "Exemplars" toggle ON
http_server_request_duration_seconds_bucket
```

### Cross-reference

See repo skill `grafana-cross-signal-correlation` for datasource configuration (`tracesToLogsV2`, `tracesToMetrics`, exemplar toggle).

---

## 4. Cardinality & Dangerous Attributes

### Attributes per key metric

| Metric | Required Attributes | Opt-In (safe) | ⚠️ DANGEROUS |
|--------|--------------------|----|---|
| `http.server.request.duration` | `http.request.method`, `url.scheme`, `http.response.status_code`, `http.route` | `network.protocol.version` | `server.address` (header-derived), `url.full` ❌ |
| `rpc.server.call.duration` | `rpc.system.name`, `rpc.method` (fully-qualified) | `server.address`, `rpc.response.status_code` | unbounded `rpc.method` if dynamic ❌ (no `rpc.service` in semconv ≥1.38) |
| `db.client.operation.duration` | `db.system.name`, `db.namespace`, `db.operation.name` | `db.collection.name` | `db.query.text` ❌, `db.statement` ❌ |
| `messaging.client.operation.duration` | `messaging.system`, `messaging.operation.name` | `messaging.destination.name` | per-message IDs ❌ |

### Cardinality explosion patterns

| Anti-pattern | Series explosion | Fix |
|---|---|---|
| `url.full` as attribute | ∞ (one per unique URL) | Use `http.route` (templated) |
| `db.query.text` unparameterized | ∞ (one per query) | Parameterize queries; use `db.operation.name` |
| Spanmetrics `client` × `server` | O(n²) services | Limit via Collector `spanmetrics` connector dimensions |
| `user_id` / `request_id` as label | ∞ | Never — use trace attributes instead |

---

## 5. Histogram & Percentile Pitfalls

### OTel default bucket boundaries (duration histograms)

```
[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
```

Source: [OTel Metrics API — ExplicitBucketBoundaries](https://opentelemetry.io/docs/specs/otel/metrics/api/#instrument-advisory-parameters)

### Key pitfalls

| Pitfall | Why it matters | Mitigation |
|---------|---------------|------------|
| SLO threshold between buckets | `histogram_quantile` interpolates linearly; if SLO=200ms but buckets are [100ms, 250ms], p99 is inaccurate | Add bucket at SLO boundary (0.2) |
| Aggregating across operations | p99 of mixed GET+POST is meaningless (latency distributions differ) | Always filter by `http.route` or `rpc.method` |
| Quantile over quantile | `avg(histogram_quantile(...)) by (pod)` is statistically invalid | Aggregate buckets FIRST, then compute quantile |
| Exponential/native histograms | VM supports them (`vm_histogram_bucket`); better resolution, no pre-defined boundaries | Use when available; check [VM docs](https://docs.victoriametrics.com/keyconcepts/#histogram) |

---

## 6. rate() Window vs Scrape Interval

Source: [Prometheus docs — rate()](https://prometheus.io/docs/prometheus/latest/querying/functions/#rate)

### Rule of thumb

> **Range window ≥ 4× scrape interval** to survive missed scrapes.

| Scrape interval | Minimum range | Recommended range |
|-----------------|---------------|-------------------|
| 15s | 60s (`[1m]`) | `[2m]` |
| 30s | 120s (`[2m]`) | `[4m]` |
| 60s | 240s (`[4m]`) | `[5m]` |

### rate() vs increase() for SLIs

| Function | Use for | Returns |
|----------|---------|---------|
| `rate(metric[5m])` | Per-second rate (dashboards, alerts) | Instantaneous per-second value |
| `increase(metric[5m])` | SLI ratios over a window (recording rules) | Total increase in window (≈ rate × window) |

For burn-rate alerting, prefer `rate()` because the multi-window comparison is already normalized.

---

## 7. Recording Rule Patterns (VMRule)

Source: [Google SRE Workbook — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)

### Availability SLI (error ratio)

```yaml
# sli:<service>:availability:ratio_rate5m
- record: sli:myservice:availability:ratio_rate5m
  expr: |
    1 - (
      sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5..",service_name="myservice"}[5m]))
      /
      sum(rate(http_server_request_duration_seconds_count{service_name="myservice"}[5m]))
    )
```

### Latency-good ratio (requests under SLO threshold)

```yaml
# sli:<service>:latency_good:ratio_rate5m (SLO: p99 < 500ms)
- record: sli:myservice:latency_good:ratio_rate5m
  expr: |
    sum(rate(http_server_request_duration_seconds_bucket{le="0.5",service_name="myservice"}[5m]))
    /
    sum(rate(http_server_request_duration_seconds_count{service_name="myservice"}[5m]))
```

### Multi-window burn rate (Google SRE 6-window)

```yaml
# Fast burn: 5m window, budget consumed 14.4x target
- alert: HighBurnRate_Fast
  expr: |
    (1 - sli:myservice:availability:ratio_rate5m) > (14.4 * 0.001)
    and
    (1 - sli:myservice:availability:ratio_rate1h) > (14.4 * 0.001)
  for: 2m
  labels:
    severity: critical

# Slow burn: 30m window, budget consumed 6x target
- alert: HighBurnRate_Slow
  expr: |
    (1 - sli:myservice:availability:ratio_rate30m) > (6 * 0.001)
    and
    (1 - sli:myservice:availability:ratio_rate6h) > (6 * 0.001)
  for: 15m
  labels:
    severity: warning
```

### From spanmetrics (Tempo → VM via spanmetrics connector)

The OTel Collector `spanmetrics` connector generates `duration_milliseconds` histograms from traces. Use for SLIs when SDK-level metrics aren't available:

```yaml
- record: sli:myservice:latency_good_spanmetrics:ratio_rate5m
  expr: |
    sum(rate(duration_milliseconds_bucket{le="500",service_name="myservice",span_kind="SPAN_KIND_SERVER"}[5m]))
    /
    sum(rate(duration_milliseconds_count{service_name="myservice",span_kind="SPAN_KIND_SERVER"}[5m]))
```

---

## 8. OTel → Prometheus/VictoriaMetrics Name Translation

Source: [OTel Metrics Data Model — Prometheus compatibility](https://opentelemetry.io/docs/specs/otel/compatibility/prometheus_and_openmetrics/)

### Rules

| OTel convention | Prometheus/VM convention | Example |
|-----------------|--------------------------|---------|
| Dots in name → underscores | `http.server.request.duration` → `http_server_request_duration` | — |
| Unit suffix appended | `s` → `_seconds`, `By` → `_bytes`, `1` → `_ratio` | `http_server_request_duration_seconds` |
| Counter gets `_total` | `messaging.client.sent.messages` → `messaging_client_sent_messages_total` | — |
| Histogram gets `_bucket`/`_sum`/`_count` | auto-generated by exporter | `http_server_request_duration_seconds_bucket{le="0.5"}` |
| Unit `{request}` → no suffix | Curly-brace units are dropped | `http_server_active_requests` |
| UpDownCounter → Gauge (no suffix) | `http.server.active_requests` → `http_server_active_requests` | — |

### Complete examples

| OTel dotted name | Instrument | Unit | Prometheus/VM name |
|------------------|-----------|------|---------------------|
| `http.server.request.duration` | Histogram | `s` | `http_server_request_duration_seconds_bucket` |
| `http.server.active_requests` | UpDownCounter | `{request}` | `http_server_active_requests` |
| `rpc.client.call.duration` | Histogram | `s` | `rpc_client_call_duration_seconds_bucket` |
| `db.client.operation.duration` | Histogram | `s` | `db_client_operation_duration_seconds_bucket` |
| `dotnet.gc.collections` | Counter | `{collection}` | `dotnet_gc_collections_total` |
| `dotnet.gc.pause.time` | Counter | `s` | `dotnet_gc_pause_time_seconds_total` |
| `dns.lookup.duration` | Histogram | `s` | `dns_lookup_duration_seconds_bucket` |
| `messaging.client.sent.messages` | Counter | `{message}` | `messaging_client_sent_messages_total` |

---

## 9. Stability Level Reference

Source: OTel Semconv v1.43.0 — each section's status header.

| Domain | OTel Stability | Notes |
|--------|---------------|-------|
| HTTP metrics (`http.server.*`, `http.client.*`) | **Stable** | Core RED metrics safe for production SLIs |
| RPC metrics (`rpc.server.call.duration`, `rpc.client.call.duration`) | **Release Candidate** | Near-stable; name changed from `rpc.server.duration` |
| Database metrics (`db.client.operation.duration`) | **Stable** | Safe for SLIs |
| Messaging metrics (`messaging.client.operation.duration`) | **Development** | May change; use cautiously for SLIs |
| .NET runtime (`dotnet.*`, `kestrel.*`) | **Stable** (since .NET 9 / semconv) | Microsoft-maintained; production-ready |
| Go runtime (`go.memory.*`, `go.schedule.*`, `go.goroutine.*`) | **Development** | Names may change; pin to semconv version |
| Node.js runtime (`nodejs.eventloop.*`) | **Development** | Names may change |
| Python/CPython runtime | **Development** | Sparse coverage in semconv; many metrics unverified |

---

## 10. Metric Interrelation & Correlation

### Causal chain (how metrics affect each other)

```
http.server.request.duration ↑ (latency spike)
  └── caused by → db.client.operation.duration ↑ (slow DB)
      └── or → rpc.client.call.duration ↑ (slow downstream gRPC)
          └── or → runtime saturation:
              ├── .NET: kestrel.connection.queue.length ↑ → threadpool starvation
              ├── Go:   go.schedule.duration ↑ → goroutine scheduling delay
              └── Node: nodejs.eventloop.utilization → 1.0 → event loop blocked
```

### Cross-signal correlation table

| If you see... | Check next... | Why |
|---------------|---------------|-----|
| `http.server.request.duration` p99 spike | `db.client.operation.duration`, `rpc.client.call.duration` | Dependency is usually the cause |
| `rpc.client.call.duration` spike (one method) | Target service's `rpc.server.call.duration` for same method | Confirms server-side vs network |
| `go.schedule.duration` p99 > 10ms | `go.goroutine.count`, `go.processor.limit` | Too many goroutines for GOMAXPROCS |
| `nodejs.eventloop.utilization` > 0.8 | `nodejs.eventloop.delay.p99` | Confirms event loop is blocked |
| `http.server.active_requests` stuck high | `http.server.request.duration` | Requests not completing (deadlock?) |
| `db.client.operation.duration` spike | DB-specific pool metrics (`db.client.connection.wait_time`) | Pool exhaustion vs query regression |

---

## 11. Symptom → Metric Troubleshooting Quick Reference

| Symptom | First metric to check | VM Query |
|---------|----------------------|----------|
| Slow API responses | `http.server.request.duration` p99 | `histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket{service_name="X"}[5m])) by (le))` |
| Timeout errors to downstream | `http.client.request.duration` p99 | `histogram_quantile(0.99, sum(rate(http_client_request_duration_seconds_bucket{server_address="Y"}[5m])) by (le))` |
| gRPC DEADLINE_EXCEEDED | `rpc.client.call.duration{error.type="DEADLINE_EXCEEDED"}` | `sum(rate(rpc_client_call_duration_seconds_count{error_type="DEADLINE_EXCEEDED"}[5m]))` |
| DB connection exhaustion | `db.client.connection.wait_time` | `histogram_quantile(0.99, sum(rate(db_client_connection_wait_time_seconds_bucket[5m])) by (le))` |
| .NET thread starvation | `http.client.request.time_in_queue` | `histogram_quantile(0.99, sum(rate(http_client_request_time_in_queue_seconds_bucket[5m])) by (le))` |
| Go goroutine leak | `go.goroutine.count` trending up | `go_goroutine_count{service_name="X"}` |
| Node.js event loop blocked | `nodejs.eventloop.utilization` → 1.0 | `nodejs_eventloop_utilization_ratio{service_name="X"} > 0.8` |
| High error rate | `http.server.request.duration` count with error.type | `sum(rate(http_server_request_duration_seconds_count{error_type!=""}[5m])) / sum(rate(http_server_request_duration_seconds_count[5m]))` |
| Memory leak (.NET) | `dotnet.gc.heap.total_size` trending up | `dotnet_gc_heap_total_size_bytes` |
| Memory leak (Go) | `go.memory.used{go.memory.type="other"}` trending up | `go_memory_used_bytes{go_memory_type="other"}` |
