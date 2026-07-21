---
name: loki-tempo-self-metrics
description: "Use when diagnosing Loki or Tempo backend health \u2014 ingest loss, flush failures, query latency, WAL corruption, metrics-generator series pressure. Complements collector-internal-metrics (OTel pipeline) and victoriametrics-troubleshooting (metrics backend). All metric names confirmed present in live VictoriaMetrics inventory (2026-07-06)."
keywords: [loki-tempo, loki, tempo, "loki tempo", otel, pipeline, victoriametrics, wal]
---
# Loki & Tempo Backend Self-Metrics

Backend health metrics for the **log** (Loki) and **trace** (Tempo) storage layer.

**Question answered**: "Are logs/traces actually being stored, or silently lost?"

**Scope**: Loki distributor/ingester/query-frontend self-telemetry + Tempo
distributor/ingester/metrics-generator/query-frontend self-telemetry, as scraped
into VictoriaMetrics by vmagent.

> **Confirmed present in live VictoriaMetrics inventory (2026-07-06).**
> Metric names may vary slightly across Loki/Tempo versions. Names below match
> the Prometheus/underscore form stored in VM.

---

## Loki Backend Metrics

### Ingest Health (Distributor)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `loki_distributor_bytes_received_total` | Counter | bytes | Total bytes received by the distributor from push clients | Baseline ingest throughput; drop = upstream problem (Fluent Bit/OTel Collector stopped sending) | `tenant` |
| `loki_discarded_bytes_total` | Counter | bytes | **KEY — bytes dropped** due to rate-limiting, per-stream limits, or validation failures | **Silent loss signal.** Non-zero rate = logs are being permanently lost. The `reason` label tells WHY. | `tenant`, `reason` ⚠️ |

**`loki_discarded_bytes_total` reason values** (verified from Loki source):
- `rate_limited` — tenant exceeded ingestion rate limit
- `per_stream_rate_limit` — single stream exceeded per-stream byte rate
- `stream_limit` — tenant exceeded max active streams
- `validation_error` — malformed labels, timestamps, etc.
- `missing_enforced_labels` — required labels absent from stream
- `blocked_ingestion` — tenant's ingestion explicitly blocked by config

### Storage / Flush Health (Ingester)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `loki_ingester_chunks_flushed_total` | Counter | chunks | Total chunks successfully flushed to object storage | Baseline flush rate; sustained drop = storage backend problem | — |
| `loki_ingester_chunks_flush_failures_total` | Counter | chunks | Failed chunk flushes | Non-zero = data at risk (chunks stuck in memory/WAL) | — |
| `loki_ingester_chunk_utilization_bucket` | Histogram | ratio (0–1) | How full chunks are at flush time | Low utilization (<0.5) = too-frequent flushes wasting storage; high (>0.9) = healthy | `le` |
| `loki_ingester_memory_streams` | Gauge | streams | Active streams held in memory | Capacity signal; unexpected spike = cardinality explosion | — |
| `loki_ingester_flush_queue_length` | Gauge | items | Pending items in the flush queue | Growing queue = flush rate < ingest rate = OOM risk | — |

### WAL Health (Ingester)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `loki_ingester_wal_disk_usage_percent` | Gauge | ratio (0–1) | WAL disk utilization | >0.8 = risk of WAL backpressure / rejected writes | — |
| `loki_ingester_wal_corruptions_total` | Counter | events | WAL corruption events detected | Any >0 = data integrity risk, investigate immediately | — |

### Query RED (All Components)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `loki_request_duration_seconds_bucket` | Histogram | seconds | Request latency for all Loki HTTP endpoints | RED method: filter by `route` and `status_code` to identify slow/failing routes | `route`, `status_code`, `method`, `le` |
| `loki_query_frontend_*` | Various | — | Query frontend scheduling, splitting, retries | Diagnose query performance (queue time, splits, downstream errors) | varies by sub-metric |

⚠️ **High-cardinality warning**: `route` label is bounded but `tenant` can be
high in multi-tenant deployments. Never `group by (tenant)` without `topk()`.

---

## Tempo Backend Metrics

### Ingest Health (Distributor)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `tempo_distributor_bytes_received_total` | Counter | bytes | Total proto bytes received after limits check (per tenant) | Baseline trace ingest throughput | `tenant` |
| `tempo_distributor_spans_received_total` | Counter | spans | Total spans received per tenant | Span-based throughput (complements bytes) | `tenant` |
| `tempo_discarded_spans_total` | Counter | spans | **KEY — spans permanently discarded** due to limits or validation | **Silent loss signal.** The `reason` label tells WHY spans are lost. | `tenant`, `reason` ⚠️ |

**`tempo_discarded_spans_total` reason values** (verified from Tempo docs/source — Tempo ≥3.0):
- `rate_limited` — tenant byte rate exceeded `rate_limit_bytes`
- `trace_too_large` — single trace exceeded `max_bytes_per_trace`
- `live_traces_exceeded` — active trace count exceeded `max_traces_per_user`
- `invalid_trace_id` — trace ID not 128 bits
- `invalid_span_id` — span ID not 64 bits or all zeros
- `trace_too_large_to_compact` — backend-worker can't compact oversized trace
- `unknown_error` — unexpected processing error

### Metrics-Generator Health

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `tempo_distributor_metrics_generator_pushes_failures_total` | Counter | pushes | Failed span pushes from distributor to metrics-generator | Non-zero = span-metrics/service-graph not receiving data | `metrics_generator` |
| `tempo_metrics_generator_spans_received_total` | Counter | spans | Spans actually received by the metrics-generator | Compare with `distributor_spans_received` to detect generator-side loss | `tenant` |
| `tempo_metrics_generator_registry_active_series` | Gauge | series | Active time series in the generator's internal registry | Capacity signal; approaching limit = series about to be dropped | `tenant` |
| `tempo_metrics_generator_registry_series_limited_total` | Counter | events | **KEY — series creation attempts rejected** because limit was hit | **Silent loss for span-metrics.** Non-zero = generated RED metrics are INCOMPLETE. | `tenant` |

### Storage / Flush Health (Ingester)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `tempo_ingester_traces_created_total` | Counter | traces | Traces created in the ingester | Baseline write volume; sudden drop = upstream issue | `tenant` |
| `tempo_ingester_failed_flushes_total` | Counter | events | Failed block flushes to object storage | Non-zero = trace data at risk in WAL only | — |
| `tempo_ingester_flush_duration_seconds` | Histogram | seconds | Time to flush a block to storage | Growing p99 = storage backend slowdown | `le` |

### Receiver (OTLP/gRPC intake)

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `tempo_receiver_accepted_spans` | Counter | spans | Spans accepted by the receiver (before distributor processing) | Earliest intake counter; compare with `distributor_spans_received` | `receiver`, `transport` |

### Query RED

| Metric | Type | Unit | What it measures | Troubleshooting use | Key labels |
|--------|------|------|------------------|---------------------|------------|
| `tempo_request_duration_seconds_bucket` | Histogram | seconds | Latency for all Tempo HTTP/gRPC endpoints | RED method for Tempo query path; filter by `route` | `route`, `status_code`, `method`, `le` |
| `tempo_query_frontend_queries_within_slo_total` | Counter | queries | Queries that completed within the configured SLO threshold | SLO tracking for query path; `(within_slo / total)` = SLO compliance ratio | — |

---

## How Metrics Interrelate (Correlation Map)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LOKI WRITE PATH                                   │
│                                                                     │
│  OTel/FluentBit → Collector → [otelcol_exporter_send_failed_*]     │
│                                      ↓                              │
│  loki_distributor_bytes_received_total  (what arrived)              │
│         ↓                                                           │
│  loki_discarded_bytes_total{reason=...}  (what was DROPPED)         │
│         ↓ (accepted)                                                │
│  loki_ingester_memory_streams  (buffered)                           │
│         ↓                                                           │
│  loki_ingester_chunks_flushed_total  (persisted)                    │
│  loki_ingester_chunks_flush_failures_total  (failed to persist)     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    TEMPO WRITE PATH                                  │
│                                                                     │
│  OTel Collector → [otelcol_exporter_send_failed_*]                  │
│                              ↓                                      │
│  tempo_receiver_accepted_spans  (OTLP received)                     │
│         ↓                                                           │
│  tempo_distributor_spans_received_total  (post-validation)          │
│  tempo_distributor_bytes_received_total                              │
│         ↓                                                           │
│  tempo_discarded_spans_total{reason=...}  (what was DROPPED)        │
│         ↓ (accepted)                                                │
│  tempo_ingester_traces_created_total  (stored in WAL)               │
│         ↓                                                           │
│  tempo_ingester_flush_duration_seconds  (flushing to S3)            │
│  tempo_ingester_failed_flushes_total  (flush failures)              │
│                                                                     │
│  ── Metrics-Generator fork ──                                       │
│  tempo_metrics_generator_spans_received_total                       │
│         ↓                                                           │
│  tempo_metrics_generator_registry_active_series (capacity)          │
│  tempo_metrics_generator_registry_series_limited_total (DROPPED)    │
└─────────────────────────────────────────────────────────────────────┘
```

### Cross-system correlations

| Symptom in one metric | Correlate with |
|-----------------------|----------------|
| `otelcol_exporter_send_failed_log_records` rising | `loki_distributor_bytes_received_total` should DROP proportionally |
| `otelcol_exporter_send_failed_spans` rising | `tempo_distributor_spans_received_total` should DROP proportionally |
| `loki_discarded_bytes_total` rising | Check OTel Collector `otelcol_exporter_queue_size` for corresponding backpressure |
| `tempo_discarded_spans_total{reason="rate_limited"}` | Check `tempo_distributor_bytes_received_total` — is genuine traffic spike or misconfigured limit? |
| `tempo_metrics_generator_registry_series_limited_total` | Check `tempo_metrics_generator_registry_active_series` approaching configured `max_active_series` |
| `loki_ingester_chunks_flush_failures_total` rising | Check S3/GCS health; correlate with `loki_ingester_wal_disk_usage_percent` growing |
| `tempo_ingester_failed_flushes_total` rising | Check object storage latency/errors; WAL growing |

---

## Symptom → Metric Quick-Reference

### Loki

| Symptom | First query | Follow-up |
|---------|-------------|-----------|
| Logs missing / incomplete | `sum by (reason) (rate(loki_discarded_bytes_total[5m]))` | If >0: identify reason; if 0: check `otelcol_exporter_send_failed_log_records` |
| Ingester OOMing | `loki_ingester_memory_streams` + `loki_ingester_flush_queue_length` | If queue growing: check `loki_ingester_chunks_flush_failures_total` |
| Queries slow | `histogram_quantile(0.99, sum by (le,route) (rate(loki_request_duration_seconds_bucket{route=~"/loki/api/v1/query.*"}[5m])))` | Check `loki_query_frontend_*` for queue time |
| WAL filling up | `loki_ingester_wal_disk_usage_percent > 0.8` | Check `loki_ingester_chunks_flush_failures_total` for why flushes are stuck |
| Chunk storage inefficient | `histogram_quantile(0.5, sum by (le) (rate(loki_ingester_chunk_utilization_bucket[5m]))) < 0.5` | Tune `chunk_idle_period` / `max_chunk_age` |

### Tempo

| Symptom | First query | Follow-up |
|---------|-------------|-----------|
| Traces missing / spans dropped | `sum by (reason) (rate(tempo_discarded_spans_total[5m]))` | Identify reason: `rate_limited` → raise limit; `trace_too_large` → investigate instrumentation |
| Span-metrics incomplete (RED gaps) | `rate(tempo_metrics_generator_registry_series_limited_total[5m]) > 0` | Check `tempo_metrics_generator_registry_active_series` vs configured limit |
| Metrics-generator not receiving | `rate(tempo_distributor_metrics_generator_pushes_failures_total[5m]) > 0` | Generator unhealthy or overloaded |
| Flush failures (data at risk) | `rate(tempo_ingester_failed_flushes_total[5m]) > 0` | Check S3/GCS backend; `tempo_ingester_flush_duration_seconds` p99 growing? |
| Queries slow / SLO breach | `1 - (rate(tempo_query_frontend_queries_within_slo_total[5m]) / rate(tempo_request_duration_seconds_count{route=~".*"}[5m]))` | Identify slow routes; check backend I/O |
| Ingest throughput dropped | `rate(tempo_distributor_bytes_received_total[5m])` | If dropped: check upstream `otelcol_exporter_send_failed_spans`; if stable: check `tempo_discarded_spans_total` |

---

## MetricsQL Examples (Copy-Paste)

### Loki — discard rate by reason (last 1h)

```promql
sum by (reason) (
  rate(loki_discarded_bytes_total[5m])
)
```

### Loki — push error rate (HTTP 5xx on /loki/api/v1/push)

```promql
sum(rate(loki_request_duration_seconds_count{route="/loki/api/v1/push", status_code=~"5.."}[5m]))
/
sum(rate(loki_request_duration_seconds_count{route="/loki/api/v1/push"}[5m]))
```

### Loki — ingester flush failure ratio

```promql
rate(loki_ingester_chunks_flush_failures_total[5m])
/
rate(loki_ingester_chunks_flushed_total[5m])
```

### Tempo — discard rate by reason

```promql
sum by (reason) (
  rate(tempo_discarded_spans_total[5m])
)
```

### Tempo — metrics-generator series saturation

```promql
tempo_metrics_generator_registry_active_series
/
# replace 100000 with your configured max_active_series
100000
```

### Tempo — flush duration p99

```promql
histogram_quantile(0.99,
  sum by (le) (rate(tempo_ingester_flush_duration_seconds_bucket[5m]))
)
```

### Tempo — ingest-to-storage loss ratio

```promql
sum(rate(tempo_discarded_spans_total[5m]))
/
sum(rate(tempo_distributor_spans_received_total[5m]))
```

### Cross-system — Collector export failure vs backend received

```promql
# Loki side: what Collector failed to deliver
rate(otelcol_exporter_send_failed_log_records{exporter=~".*loki.*"}[5m])

# Tempo side: what Collector failed to deliver
rate(otelcol_exporter_send_failed_spans{exporter=~".*otlp.*"}[5m])
```

---

## High-Cardinality Label Warnings

| Metric | Label | Risk | Mitigation |
|--------|-------|------|------------|
| `loki_discarded_bytes_total` | `tenant` | High in multi-tenant (100+ tenants) | Always aggregate or `topk(10, ...)` |
| `tempo_discarded_spans_total` | `tenant` | Same | Same |
| `tempo_distributor_spans_received_total` | `tenant` | Same | Same |
| `loki_request_duration_seconds_bucket` | `route` × `status_code` | Moderate (bounded) | Safe in practice |
| `tempo_distributor_metrics_generator_pushes_failures_total` | `metrics_generator` | Low (1-2 values) | Safe |

---

## Version Notes

- `tempo_discarded_spans_total`: confirmed in Tempo ≥2.x; reason labels expanded
  in Tempo 3.0 (added `trace_too_large_to_compact`, `invalid_span_id`).
- `tempo_distributor_spans_received_total`: confirmed in source code
  (`modules/distributor/distributor.go`). In older Tempo versions this may appear
  as `tempo_distributor_spans_received` (without `_total` suffix) depending on
  client_golang version.
- `tempo_metrics_generator_registry_active_series`: confirmed in source as a Gauge
  (`modules/generator/registry/registry.go`). The `series_limited_total` counter
  name is `tempo_metrics_generator_registry_series_limited_total` — verify exact
  name in your `/metrics` endpoint if querying returns empty.
- Loki metrics confirmed from Loki source (`pkg/distributor/distributor.go`) and
  official documentation (`grafana.com/docs/loki/latest/operations/observability`).
- `loki_ingester_wal_disk_usage_percent` and `loki_ingester_wal_corruptions_total`:
  present in inventory; confirmed WAL-related ingester metrics from Loki docs.

---

## Related Skills

- `collector-internal-metrics` — OTel Collector pipeline health (what happens
  BEFORE data reaches Loki/Tempo)
- `victoriametrics-troubleshooting` — VictoriaMetrics cluster health (the metrics
  BACKEND that stores these metrics)
- `trace-derived-metrics` — metrics generated FROM traces (the output of Tempo's
  metrics-generator)
- `loki-logql-patterns` — querying stored logs via LogQL
- `tempo-traceql-patterns` — querying stored traces via TraceQL
