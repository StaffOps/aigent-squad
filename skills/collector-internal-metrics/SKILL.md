---
name: collector-internal-metrics
description: Use when diagnosing OTel Collector pipeline health — data loss, backpressure, queue saturation, export failures. Covers receiver/processor/exporter/process self-telemetry metrics with VictoriaMetrics query names.
keywords:
  - otel collector
  - otelcol
  - collector
  - pipeline
  - data loss
  - enqueue_failed
  - send_failed
  - queue saturation
  - backpressure
  - receiver refused
  - exporter
  - batch processor
  - memory_limiter
  - otelcol_receiver
  - otelcol_exporter
  - otelcol_processor
---

# OTel Collector Internal Metrics

Self-observability metrics emitted by the OpenTelemetry Collector. These answer: "is data actually flowing through the pipeline, or is it being silently dropped?"

> **Source**: [opentelemetry.io/docs/collector/internal-telemetry/](https://opentelemetry.io/docs/collector/internal-telemetry/) (last verified 2026-07-06).

---

## Important: Naming in VictoriaMetrics

The Collector exposes metrics via Prometheus endpoint (default `:8888/metrics`). When scraped into VictoriaMetrics:

| Convention | Behavior |
|-----------|----------|
| `otelcol_` prefix | All Collector-originated metrics carry this prefix |
| `_total` suffix | Added to Counter metrics by default Prometheus exporter |
| Unit suffixes (`_seconds`, `_bytes`) | Added unless `without_units: true` is set |
| Dots → underscores | `http.client.request.duration` → `http_client_request_duration` (Collector ≥v0.120.0 preserves dots; Prometheus scrapers may still translate) |

**Default Prometheus exporter** (no manual `readers` config) sets `without_type_suffix: true` and `without_units: true` — so you get the raw OTLP name (e.g., `otelcol_process_uptime`). Manual `readers` config does NOT set these by default.

> ⚠️ **Exact metric names vary by Collector version and telemetry `level` setting** (`basic`/`normal`/`detailed`). Always confirm against the running instance with `curl :8888/metrics | grep otelcol_`.

---

## Telemetry Levels

| Level | What's emitted |
|-------|----------------|
| `basic` | Receiver accepted/refused, exporter sent/failed/enqueue_failed, queue size/capacity, process metrics |
| `normal` (default) | Above + batch processor metrics (send_size, trigger counts, metadata_cardinality) |
| `detailed` | Above + HTTP/gRPC request duration/body size, batch send_size_bytes |

---

## Stability

Per [OTel Collector telemetry maturity levels](https://opentelemetry.io/docs/collector/internal-telemetry/#telemetry-maturity-levels):

- Collector-owned `otelcol_*` metrics follow a Development → Alpha → Beta → **Stable** lifecycle.
- `http.*` and `rpc.*` metrics come from Go instrumentation libraries and are **NOT covered** by Collector stability guarantees.
- Stable metrics will not be renamed, deleted, or have their type/attributes changed.

The core pipeline metrics (`otelcol_receiver_*`, `otelcol_exporter_*`, `otelcol_processor_incoming/outgoing_items`) are mature and present since early Collector versions. Treat them as **Beta/Stable** for operational use.

---

## Receiver Metrics (Level: basic)

Answers: "Is data arriving at the Collector?"

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_receiver_accepted_spans` | `otelcol_receiver_accepted_spans_total` | Counter | {spans} | Spans successfully ingested and pushed into pipeline | Baseline ingress rate; drop to zero = source stopped sending |
| `otelcol_receiver_accepted_metric_points` | `otelcol_receiver_accepted_metric_points_total` | Counter | {datapoints} | Metric points successfully ingested | Ingress rate for metrics pipeline |
| `otelcol_receiver_accepted_log_records` | `otelcol_receiver_accepted_log_records_total` | Counter | {log_records} | Log records successfully ingested | Ingress rate for logs pipeline |
| `otelcol_receiver_refused_spans` | `otelcol_receiver_refused_spans_total` | Counter | {spans} | Spans that could NOT be pushed into pipeline | Backpressure from downstream (memory_limiter, full queue) |
| `otelcol_receiver_refused_metric_points` | `otelcol_receiver_refused_metric_points_total` | Counter | {datapoints} | Metric points refused | Same — downstream pressure |
| `otelcol_receiver_refused_log_records` | `otelcol_receiver_refused_log_records_total` | Counter | {log_records} | Log records refused | Same — downstream pressure |

**Key attributes**: `receiver` (receiver instance name, e.g. `otlp`), `transport` (e.g. `grpc`, `http`).

> ⚠️ **Cardinality**: `receiver` and `transport` are bounded. Safe.

### What a problem looks like

- `refused > 0` sustained → clients receiving errors; may cause **client-side data loss** if clients don't retry.
- `accepted` drops to zero → source stopped sending, or receiver crashed/unreachable.

---

## Processor Metrics

### Pipeline flow (Level: basic)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_processor_incoming_items` | `otelcol_processor_incoming_items_total` | Counter | {items} | Items passed TO the processor | Input rate per processor |
| `otelcol_processor_outgoing_items` | `otelcol_processor_outgoing_items_total` | Counter | {items} | Items emitted FROM the processor | Output rate; difference with incoming = dropped/filtered |

**Key attributes**: `processor` (processor instance name), `otel_signal` (traces/metrics/logs).

> `incoming - outgoing = dropped or filtered`. For filter processors this is expected. For batch processor a sustained gap means data loss.

### Batch Processor (Level: normal)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_processor_batch_batch_send_size` | `otelcol_processor_batch_batch_send_size` | Histogram | {units} | Number of units per batch sent | Batch efficiency — small batches = timeout-dominated (wasteful) |
| `otelcol_processor_batch_batch_size_trigger_send` | `otelcol_processor_batch_batch_size_trigger_send_total` | Counter | {sends} | Batches sent due to size trigger | High = good throughput utilization |
| `otelcol_processor_batch_timeout_trigger_send` | `otelcol_processor_batch_timeout_trigger_send_total` | Counter | {sends} | Batches sent due to timeout trigger | High ratio vs size_trigger = low throughput or batch_size too large |
| `otelcol_processor_batch_metadata_cardinality` | `otelcol_processor_batch_metadata_cardinality_total` | Counter | {combinations} | Distinct metadata value combinations | ⚠️ HIGH CARDINALITY RISK — each combo creates separate batch queue |

**Key attributes**: `processor`.

> ⚠️ **`metadata_cardinality`**: If using `metadata_keys` in batch processor config, each unique metadata combination creates a separate batcher. Unbounded metadata = memory explosion.

### Memory Limiter Processor

The memory_limiter processor does NOT emit its own named metrics. Its behavior is visible through:
- `otelcol_receiver_refused_*` increasing (it triggers backpressure via receiver refusal)
- `otelcol_process_memory_rss` approaching configured `limit_mib`
- Collector logs: `"Memory usage is above hard limit"` / `"Data dropped due to memory_limiter"`

---

## Exporter Metrics (Level: basic)

Answers: "Is data leaving the Collector successfully?"

### Sent (success)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_exporter_sent_spans` | `otelcol_exporter_sent_spans_total` | Counter | {spans} | Spans successfully sent to destination | Egress rate — compare with receiver_accepted for pipeline loss |
| `otelcol_exporter_sent_metric_points` | `otelcol_exporter_sent_metric_points_total` | Counter | {datapoints} | Metric points successfully sent | Same |
| `otelcol_exporter_sent_log_records` | `otelcol_exporter_sent_log_records_total` | Counter | {log_records} | Log records successfully sent | Same |

### Send Failures

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_exporter_send_failed_spans` | `otelcol_exporter_send_failed_spans_total` | Counter | {spans} | Spans that failed to send to destination | Backend unreachable/rejecting; may retry (not necessarily loss) |
| `otelcol_exporter_send_failed_metric_points` | `otelcol_exporter_send_failed_metric_points_total` | Counter | {datapoints} | Metric points failed to send | Same |
| `otelcol_exporter_send_failed_log_records` | `otelcol_exporter_send_failed_log_records_total` | Counter | {log_records} | Log records failed to send | Same |

### Enqueue Failures (⚠️ SILENT DATA LOSS)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_exporter_enqueue_failed_spans` | `otelcol_exporter_enqueue_failed_spans_total` | Counter | {spans} | Spans that FAILED TO ENTER the sending queue | **DATA IS PERMANENTLY LOST** — queue was full |
| `otelcol_exporter_enqueue_failed_metric_points` | `otelcol_exporter_enqueue_failed_metric_points_total` | Counter | {datapoints} | Metric points failed to enqueue | **DATA IS PERMANENTLY LOST** |
| `otelcol_exporter_enqueue_failed_log_records` | `otelcol_exporter_enqueue_failed_log_records_total` | Counter | {log_records} | Log records failed to enqueue | **DATA IS PERMANENTLY LOST** |

> 🚨 **CRITICAL**: `enqueue_failed > 0` means **irrecoverable data loss**. Unlike `send_failed` (which may retry), enqueue failures mean the queue is full and data is DROPPED ON THE FLOOR. This is the #1 silent killer in Collector pipelines.
>
> **Real case** (from steering `application-metrics-first.md`): Gateway Collector with CPU 0.6/1.0 and memory 1.6/2Gi (resources "OK"), but `enqueue_failed_log_records` = 2840/sec = **12% of logs permanently lost**. Invisible if you only check CPU/memory.

### Queue State

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_exporter_queue_size` | `otelcol_exporter_queue_size` | Gauge | {batches} | Current number of batches in sending queue | Saturation signal — growing = backend can't keep up |
| `otelcol_exporter_queue_capacity` | `otelcol_exporter_queue_capacity` | Gauge | {batches} | Fixed capacity of the sending queue | Denominator for saturation ratio |

**Key attributes (all exporter metrics)**: `exporter` (exporter instance name, e.g. `otlphttp/tempo`), `signal` on some versions.

> ⚠️ **Cardinality**: `exporter` is bounded by pipeline config. Safe.

### What a problem looks like

- `queue_size / queue_capacity > 0.8` sustained → approaching data loss
- `enqueue_failed > 0` → already losing data NOW
- `send_failed` high + `queue_size` growing → backend down/slow, queue filling
- `sent` = 0 while `accepted` > 0 → exporter completely broken

---

## Process / Self Metrics (Level: basic)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_process_uptime` | `otelcol_process_uptime_total` | Counter | s | Seconds since Collector started | Detect restarts (resets to 0); correlate with data gaps |
| `otelcol_process_memory_rss` | `otelcol_process_memory_rss` | Gauge | By (bytes) | Resident Set Size (physical memory) | OOM risk; correlate with memory_limiter threshold |
| `otelcol_process_cpu_seconds` | `otelcol_process_cpu_seconds_total` | Counter | s | Total CPU time (user + system) | CPU saturation → processing delays → queue buildup |
| `otelcol_process_runtime_heap_alloc_bytes` | `otelcol_process_runtime_heap_alloc_bytes` | Gauge | By (bytes) | Go heap allocated bytes | Memory pressure from in-flight data |
| `otelcol_process_runtime_total_alloc_bytes` | `otelcol_process_runtime_total_alloc_bytes_total` | Counter | By (bytes) | Cumulative heap allocations | Allocation rate (GC pressure signal) |
| `otelcol_process_runtime_total_sys_memory_bytes` | `otelcol_process_runtime_total_sys_memory_bytes` | Gauge | By (bytes) | Total memory obtained from OS | Total memory footprint |

---

## In-Flight Requests (Level: basic)

| OTLP Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting Use |
|-----------|---------------------|------|------|----------|---------------------|
| `otelcol_exporter_in_flight_requests` | `otelcol_exporter_in_flight_requests` | UpDownCounter | {requests} | Export requests currently in flight (including retry backoff) | Concurrency saturation; high = backend slow |

---

## Metric Correlation Map

```
                    ┌─────────────────────────────────────────────────┐
                    │              DATA FLOW PIPELINE                  │
                    └─────────────────────────────────────────────────┘

  [Sources]           [Receiver]           [Processors]           [Exporter]           [Backend]
      │                   │                     │                     │                    │
      │──── OTLP ────►   │                     │                     │                    │
      │                   ├─ accepted ─────────►├─ incoming ─────────►├─ queue ──────────► │
      │                   │                     │                     │   │                │
      │                   ├─ REFUSED ──►(error) │  outgoing           │   ├─ sent ────────►│
      │                   │  (backpressure)     │  (< incoming =      │   │                │
      │                   │                     │   filtered/dropped)  │   ├─ send_failed   │
      │                   │                     │                     │   │  (may retry)    │
      │                   │                     │                     │   │                │
      │                   │                     │                     │   └─ ENQUEUE_FAILED │
      │                   │                     │                     │      ⚠️ DATA LOST   │
      │                   │                     │                     │                    │
```

### Key relationships

| Relationship | Formula | What it reveals |
|-------------|---------|-----------------|
| Pipeline loss ratio | `1 - (exporter_sent / receiver_accepted)` | End-to-end data loss percentage |
| Queue saturation | `queue_size / queue_capacity` | How full the queue is (>0.8 = danger) |
| Processor drop rate | `processor_incoming - processor_outgoing` | Items filtered/dropped per processor |
| Export success rate | `sent / (sent + send_failed)` | Backend reliability from Collector's perspective |
| Receiver pressure | `refused / (accepted + refused)` | How much the Collector is rejecting at the door |
| Batch efficiency | `size_trigger / (size_trigger + timeout_trigger)` | High = batches fill before timeout (good throughput) |

---

## Symptom → Metric Troubleshooting Table

| Symptom | First metrics to check | What to look for |
|---------|------------------------|------------------|
| **Data not arriving in backend** | `otelcol_exporter_sent_*_total` | Rate = 0 or dropping? Exporter broken or misconfigured |
| **Silent data loss (backend shows gaps)** | `otelcol_exporter_enqueue_failed_*_total` | Any value > 0 = **permanent loss**; queue full |
| **Silent data loss (resources look fine)** | `otelcol_exporter_enqueue_failed_*_total`, `queue_size` vs `queue_capacity` | **CPU/memory being OK does NOT mean no data loss** — check application metrics first |
| **Backend rejecting data** | `otelcol_exporter_send_failed_*_total` | Sustained rate = backend down/overloaded/auth issue |
| **Clients getting errors** | `otelcol_receiver_refused_*_total` | Sustained rate = Collector backpressuring sources (memory_limiter or queue full) |
| **Scrape targets not collected** | `up{job="..."}` (in VictoriaMetrics) | `up == 0` = target unreachable; also check `otelcol_scraper_errored_metric_points_total` |
| **High latency in pipeline** | `otelcol_exporter_queue_size`, `otelcol_exporter_in_flight_requests` | Growing queue = backend slower than ingestion rate |
| **Collector OOMKilled** | `otelcol_process_memory_rss`, `otelcol_process_runtime_heap_alloc_bytes` | Memory growing unbounded; check `metadata_cardinality`, queue sizes, batch sizes |
| **Collector restarted** | `otelcol_process_uptime_total` | Counter resets to 0 → restart detected; check K8s events for OOMKill/CrashLoop |
| **Batch processor inefficient** | `otelcol_processor_batch_timeout_trigger_send_total` vs `batch_size_trigger_send_total` | Mostly timeout triggers = low throughput; reduce `send_batch_size` or increase `timeout` |

---

## Key Queries (MetricsQL / VictoriaMetrics)

```promql
# Pipeline loss rate (last 5m, per exporter)
1 - (
  rate(otelcol_exporter_sent_log_records_total[5m])
  /
  rate(otelcol_receiver_accepted_log_records_total[5m])
)

# Queue saturation (0-1 scale)
otelcol_exporter_queue_size / otelcol_exporter_queue_capacity

# Enqueue failure rate (THE critical alert)
rate(otelcol_exporter_enqueue_failed_log_records_total[5m]) > 0

# Receiver refusal rate
rate(otelcol_receiver_refused_spans_total[5m])
  / (rate(otelcol_receiver_accepted_spans_total[5m]) + rate(otelcol_receiver_refused_spans_total[5m]))

# Export failure rate
rate(otelcol_exporter_send_failed_spans_total[5m])
  / (rate(otelcol_exporter_sent_spans_total[5m]) + rate(otelcol_exporter_send_failed_spans_total[5m]))
```

---

## Critical Alert Recommendations

| Alert | Condition | Severity |
|-------|-----------|----------|
| CollectorEnqueueFailed | `rate(otelcol_exporter_enqueue_failed_*_total[5m]) > 0` | **Critical** (active data loss) |
| CollectorQueueSaturation | `otelcol_exporter_queue_size / otelcol_exporter_queue_capacity > 0.8` for 5m | Warning (approaching loss) |
| CollectorSendFailing | `rate(otelcol_exporter_send_failed_*_total[5m]) > 0` for 5m | Warning (backend issues) |
| CollectorReceiverRefusing | `rate(otelcol_receiver_refused_*_total[5m]) > 0` for 5m | Warning (client-side loss possible) |
| CollectorRestarted | `otelcol_process_uptime_total < 300` | Info (recent restart) |

---

## Connection to Steering: Resources Lie

Per `steering/application-metrics-first.md`:

> **CPU and memory "ok" can hide 12% data loss.** The FIRST query when investigating Collector health is NEVER `kubectl top`. It is:
>
> `rate(otelcol_exporter_enqueue_failed_*_total[5m])`
>
> Only after confirming zero enqueue failures do resource metrics become relevant (to explain WHY there's a problem, not IF there is one).

Process metrics (`otelcol_process_memory_rss`, `otelcol_process_cpu_seconds`) explain the CAUSE of a problem already detected in pipeline metrics — they do not declare health.

---

## Reference

- [OTel Collector Internal Telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/) — metric lists, naming conventions, verbosity levels
- [Exporter Queue/Retry helper](https://github.com/open-telemetry/opentelemetry-collector/blob/main/exporter/exporterhelper/README.md) — queue configuration
- [OTel Collector Troubleshooting](https://opentelemetry.io/docs/collector/troubleshooting/) — diagnostic steps
