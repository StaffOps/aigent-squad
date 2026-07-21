---
name: python-apm-metrics
description: Use when troubleshooting Python services, designing dashboards, or querying VictoriaMetrics for CPython runtime, HTTP, gRPC, and database client metrics emitted by OTel SDK instrumentations.
keywords: [python, otel, victoriametrics, sdk]
---
# Python APM Metrics Reference

Metrics emitted by Python services instrumented with the OpenTelemetry SDK. Covers CPython runtime, HTTP server/client, gRPC, and database client operations.

**Critical distinction**: metrics are sourced from two categories with very different stability guarantees:

| Source | Stability | Breakage risk |
|--------|-----------|---------------|
| **OTel Semantic Conventions (semconv)** | Marked per-metric (Stable/Development/RC) | Low for Stable; may rename for Development |
| **`opentelemetry-instrumentation-system-metrics` package** | NOT semconv-blessed; package-specific | High — names may change between releases |

**Pipeline**: App (OTel SDK, `AlwaysOnSampler`) → OTel Collector (tail sampling at gateway) → VictoriaMetrics / Tempo.

---

## 1. CPython Runtime — Semconv (DEVELOPMENT)

Only 3 metrics are defined in the official OTel semantic conventions for CPython.

Source: https://opentelemetry.io/docs/specs/semconv/runtime/cpython-metrics/ (semconv 1.43.0)

| OTel Name | VictoriaMetrics Name | Type | Unit | Stability |
|-----------|---------------------|------|------|-----------|
| `cpython.gc.collections` | `cpython_gc_collections_total` | Counter | `{collection}` | Development |
| `cpython.gc.collected_objects` | `cpython_gc_collected_objects_total` | Counter | `{object}` | Development |
| `cpython.gc.uncollectable_objects` | `cpython_gc_uncollectable_objects_total` | Counter | `{object}` | Development |

**Attribute** (all 3 metrics): `cpython.gc.generation` (int: `0`, `1`, `2`) — **Required**.

| Metric | Measures | Troubleshooting use |
|--------|----------|---------------------|
| `cpython.gc.collections` | Times each GC generation was collected since interpreter start | GC pressure — high gen-2 rate = memory churn |
| `cpython.gc.collected_objects` | Total objects reclaimed per generation | Allocation rate / leak signal (gen-2 growing) |
| `cpython.gc.uncollectable_objects` | Objects with reference cycles the GC cannot free | Memory leak — non-zero gen-2 = definite leak |

Data source: Python `gc.stats()`.

---

## 2. CPython Runtime — Package-Specific (NOT semconv)

Package: `opentelemetry-instrumentation-system-metrics` (PyPI).
Source: https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-system-metrics

⚠️ **These names are NOT governed by semconv stability guarantees.** They may change between package versions.

| OTel Name | VictoriaMetrics Name | Type | Unit | Measures | Troubleshooting use |
|-----------|---------------------|------|------|----------|---------------------|
| `system.cpu.utilization` | `system_cpu_utilization` | Gauge | `1` | CPU utilization ratio (0–1) per logical CPU | CPU saturation per-core |
| `system.memory.utilization` | `system_memory_utilization` | Gauge | `1` | Memory utilization ratio (0–1) by state | OOMKill risk, memory pressure |
| `system.memory.usage` | `system_memory_usage_bytes` | UpDownCounter | `By` | Memory usage in bytes by state | Absolute memory consumption |
| `system.network.io` | `system_network_io_bytes_total` | Counter | `By` | Network bytes sent/received | Egress cost, bandwidth saturation |
| `system.thread_count` | `system_thread_count` | UpDownCounter | `{thread}` | Active OS threads | Thread leak / concurrency issues |
| `process.runtime.cpython.cpu_time` | `process_runtime_cpython_cpu_time_seconds_total` | Counter | `s` | CPU time consumed (user/system) | CPU-bound detection |
| `process.runtime.cpython.memory` | `process_runtime_cpython_memory_bytes` | UpDownCounter | `By` | Process memory (rss/vms) | RSS growth → leak signal |
| `process.runtime.cpython.gc_count` | `process_runtime_cpython_gc_count_bytes_total` | Counter | `{collection}` | GC collection count by generation | GC pressure (legacy, overlaps semconv) |
| `process.runtime.cpython.thread_count` | `process_runtime_cpython_thread_count` | UpDownCounter | `{thread}` | Python thread count | Thread starvation in asyncio |
| `process.runtime.cpython.context_switches` | `process_runtime_cpython_context_switches_total` | Counter | `{count}` | Voluntary/involuntary context switches | Contention signal |
| `process.runtime.cpython.cpu.utilization` | `process_runtime_cpython_cpu_utilization` | Gauge | `1` | Process CPU utilization ratio | Per-process CPU usage |
| `process.open_file_descriptor.count` | `process_open_file_descriptor_count` | UpDownCounter | `{count}` | Open file descriptors | FD exhaustion risk |

### prometheus_client default metrics (also present)

Emitted by the `prometheus_client` library (separate from OTel SDK). Confirmed present in live VM inventory (2026-07-06):

| VM name | Type | What it measures |
|---------|------|------------------|
| `python_gc_collections_total` | Counter | GC collections by generation (`generation` label) |
| `python_gc_objects_collected_total` | Counter | Objects collected during GC |
| `python_gc_objects_uncollectable_total` | Counter | Uncollectable objects found |
| `python_info` | Gauge | Python version info (`implementation`, `version`, `major`, `minor`) |

### Key attributes (package-specific)

| Metric | Attributes |
|--------|-----------|
| `system.cpu.utilization` | `cpu` (int, logical core), `state` (user/system/idle/…) |
| `system.memory.utilization` | `state` (used/free/cached/…) |
| `system.network.io` | `device` (interface name), `direction` (transmit/receive) |
| `process.runtime.cpython.cpu_time` | `type` (user/system) |
| `process.runtime.cpython.memory` | `type` (rss/vms) |
| `process.runtime.cpython.gc_count` | `count` (generation 0/1/2) |
| `process.runtime.cpython.context_switches` | `type` (voluntary/involuntary) |

⚠️ **Cardinality warning**: `cpu` attribute on `system.cpu.utilization` creates one series per logical CPU. In containers with many visible CPUs, this can explode cardinality. Prefer aggregated queries or filter by specific cores.

---

## 3. HTTP Server Metrics (Stable)

Emitted by: `opentelemetry-instrumentation-fastapi`, `-django`, `-flask`.
Source: https://opentelemetry.io/docs/specs/semconv/http/http-metrics/ (semconv 1.43.0)

| OTel Name | VictoriaMetrics Name | Type | Unit | Stability |
|-----------|---------------------|------|------|-----------|
| `http.server.request.duration` | `http_server_request_duration_seconds` | Histogram | `s` | **Stable** |
| `http.server.active_requests` | `http_server_active_requests` | UpDownCounter | `{request}` | Development |

### `http.server.request.duration` — the primary RED metric

| Attribute | Requirement | Cardinality concern |
|-----------|-------------|---------------------|
| `http.request.method` | Required | Low (~10 values) |
| `url.scheme` | Required | Low (http/https) |
| `http.response.status_code` | Cond. Required | Low (~50 values) |
| `http.route` | Cond. Required | Low (bounded by endpoint count) |
| `error.type` | Cond. Required | Low |
| `network.protocol.version` | Recommended | Low |
| `server.address` | Opt-In | ⚠️ Header-derived — attacker-controlled |
| `server.port` | Opt-In | ⚠️ Header-derived — attacker-controlled |

Bucket boundaries: `[ 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10 ]`

---

## 4. HTTP Client Metrics (Stable)

Emitted by: `opentelemetry-instrumentation-requests`, `-urllib`, `-urllib3`, `-httpx`.
Source: https://opentelemetry.io/docs/specs/semconv/http/http-metrics/

| OTel Name | VictoriaMetrics Name | Type | Unit | Stability |
|-----------|---------------------|------|------|-----------|
| `http.client.request.duration` | `http_client_request_duration_seconds` | Histogram | `s` | **Stable** |
| `http.client.active_requests` | `http_client_active_requests` | UpDownCounter | `{request}` | Development |

### `http.client.request.duration` attributes

| Attribute | Requirement | Cardinality concern |
|-----------|-------------|---------------------|
| `http.request.method` | Required | Low |
| `server.address` | Required | ⚠️ Unbounded if calling many hosts |
| `server.port` | Required | Low per address |
| `http.response.status_code` | Cond. Required | Low |
| `error.type` | Cond. Required | Low |

⚠️ **Cardinality warning**: `server.address` is Required and can produce high cardinality if the app calls many external hosts. Use relabeling in the Collector to aggregate or drop low-value targets.

---

## 5. RPC / gRPC Metrics (Release Candidate)

Emitted by: `opentelemetry-instrumentation-grpc` (via `GrpcAioInstrumentorServer` / `GrpcAioInstrumentorClient`).
Source: https://opentelemetry.io/docs/specs/semconv/rpc/rpc-metrics/ (semconv 1.43.0)

| OTel Name | VictoriaMetrics Name | Type | Unit | Stability |
|-----------|---------------------|------|------|-----------|
| `rpc.server.call.duration` | `rpc_server_call_duration_seconds` | Histogram | `s` | Release Candidate |
| `rpc.client.call.duration` | `rpc_client_call_duration_seconds` | Histogram | `s` | Release Candidate |

### Key attributes

| Attribute | Requirement | Example |
|-----------|-------------|---------|
| `rpc.system.name` | Required | `grpc` |
| `rpc.method` | Cond. Required | `my.package.MyService/GetUser` (fully-qualified) |
| `error.type` | Cond. Required | `DEADLINE_EXCEEDED` |
| `server.address` | Recommended | `my-service.svc` |
| `server.port` | Recommended | `50051` |

⚠️ **Cardinality warning**: `rpc.method` can have unbounded cardinality if gRPC reflection or dynamic methods are used. The instrumentation sets `_OTHER` for unrecognized methods.

---

## 6. Database Client Metrics (Stable)

Emitted by: `opentelemetry-instrumentation-sqlalchemy`, `-psycopg2`, `-redis`, `-pymongo`, etc.
Source: https://opentelemetry.io/docs/specs/semconv/db/database-metrics/ (semconv 1.43.0)

| OTel Name | VictoriaMetrics Name | Type | Unit | Stability |
|-----------|---------------------|------|------|-----------|
| `db.client.operation.duration` | `db_client_operation_duration_seconds` | Histogram | `s` | **Stable** |

### Key attributes

| Attribute | Requirement | Example |
|-----------|-------------|---------|
| `db.system.name` | Required | `postgresql`, `redis`, `mongodb` |
| `db.collection.name` | Cond. Required | `public.users` |
| `db.namespace` | Cond. Required | `customers` |
| `db.operation.name` | Cond. Required | `SELECT`, `INSERT` |
| `error.type` | Cond. Required | `_OTHER` |
| `server.address` | Cond. Required | `db.svc.cluster.local` |
| `server.port` | Cond. Required | `5432` |

⚠️ **Cardinality warning**: `db.query.text` is Opt-In. NEVER enable it as a metric attribute — it has unbounded cardinality. Use it only in traces (spans).

Bucket boundaries: `[ 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10 ]`

---

## 7. Metric Correlation Map

How metrics interrelate — use this to navigate from symptom to root cause:

```
http.server.request.duration (high p99)
├── http.client.request.duration (slow dependency?)
│   └── db.client.operation.duration (slow query?)
│       └── db.client.connection.wait_time (pool exhaustion?)
├── rpc.client.call.duration (slow gRPC backend?)
├── system.cpu.utilization (CPU-bound?)
│   └── process.runtime.cpython.cpu_time (user vs system)
├── process.runtime.cpython.memory (GC pressure → pauses)
│   └── cpython.gc.collections (gen-2 frequent?)
│       └── cpython.gc.uncollectable_objects (leak!)
└── http.server.active_requests (concurrency spike?)
    └── process.runtime.cpython.thread_count (thread starvation?)
```

---

## 8. Symptom → Metric Quick Reference

| Symptom | First metrics to check | PromQL/MetricsQL example |
|---------|------------------------|--------------------------|
| Slow API responses | `http_server_request_duration_seconds` (p99 by route) | `histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le, http_route))` |
| Timeout errors | `http_client_request_duration_seconds` (p99), `rpc_client_call_duration_seconds` | `histogram_quantile(0.99, sum(rate(http_client_request_duration_seconds_bucket{server_address="..."}[5m])) by (le))` |
| Memory leak | `process_runtime_cpython_memory_bytes{type="rss"}` growing monotonically | `process_runtime_cpython_memory_bytes{type="rss"}` |
| GC pauses | `cpython_gc_collections_total` (high gen-2 rate), `cpython_gc_uncollectable_objects_total` | `rate(cpython_gc_collections_total{cpython_gc_generation="2"}[5m])` |
| Thread starvation (asyncio) | `process_runtime_cpython_thread_count`, `system_thread_count` | `process_runtime_cpython_thread_count` |
| CPU saturation | `system_cpu_utilization`, `process_runtime_cpython_cpu_utilization` | `process_runtime_cpython_cpu_utilization > 0.9` |
| FD exhaustion | `process_open_file_descriptor_count` | `process_open_file_descriptor_count > 900` |
| Slow DB queries | `db_client_operation_duration_seconds` (p99 by operation) | `histogram_quantile(0.99, sum(rate(db_client_operation_duration_seconds_bucket[5m])) by (le, db_operation_name))` |
| High error rate | `http_server_request_duration_seconds` filtered by `http_response_status_code >= 500` | `sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])) / sum(rate(http_server_request_duration_seconds_count[5m]))` |
| Connection pool exhaustion | `db_client_connection_pending_requests`, `db_client_connection_wait_time_seconds` | `db_client_connection_pending_requests > 5` |

---

## 9. Known Gaps & Traps

### No asyncio event-loop lag metric

There is **no official OTel metric** for asyncio event-loop lag. If you need it, create a custom Gauge:

```python
import asyncio, time
from opentelemetry import metrics

meter = metrics.get_meter("custom.asyncio")
loop_lag = meter.create_gauge("asyncio.event_loop.lag", unit="s", description="Event loop scheduling lag")

async def measure_loop_lag():
    while True:
        start = time.monotonic()
        await asyncio.sleep(0)  # yield to the loop
        lag = time.monotonic() - start
        loop_lag.set(lag)
        await asyncio.sleep(1)
```

This is a **custom metric** — not semconv-standardized.

### Python 3.11 vs 3.12

| Concern | Impact |
|---------|--------|
| `pkg_resources` removal in 3.12 | OTel instrumentation packages that depend on `pkg_resources` (setuptools) break. **Pin Python 3.11** per repo steering. |
| GC internals changed in 3.12 | CPython 3.12 uses an incremental GC. The 3-generation model (`gc.stats()`) still works but semantics shift. Semconv metrics remain valid but generation-2 collection frequency changes. |

### Metric name transition (legacy → current)

Some older instrumentation versions emit `rpc.server.duration` / `rpc.client.duration` instead of `rpc.server.call.duration` / `rpc.client.call.duration`. The rename happened in semconv ~1.38. If querying VictoriaMetrics and getting no data, check for the legacy name (`rpc_server_duration_seconds`).

### `process.runtime.cpython.gc_count` vs `cpython.gc.collections`

Both measure GC collections. The `process.runtime.cpython.gc_count` is from the `system-metrics` package (legacy). The `cpython.gc.collections` is the new semconv-standard metric. During transition, **both may be emitted**. Prefer the semconv version for dashboards.

---

## 10. Prometheus Name Translation Rules

OTel → VictoriaMetrics/Prometheus translation:

| Rule | Example |
|------|---------|
| Dots → underscores | `http.server.request.duration` → `http_server_request_duration` |
| Unit suffix added | `s` → `_seconds`, `By` → `_bytes`, `1` → no suffix |
| Counter gets `_total` | `cpython.gc.collections` → `cpython_gc_collections_total` |
| Histogram gets `_bucket`/`_sum`/`_count` | query with `_bucket` for quantiles |

---

## References

- CPython semconv: https://opentelemetry.io/docs/specs/semconv/runtime/cpython-metrics/
- HTTP metrics semconv: https://opentelemetry.io/docs/specs/semconv/http/http-metrics/
- RPC metrics semconv: https://opentelemetry.io/docs/specs/semconv/rpc/rpc-metrics/
- DB metrics semconv: https://opentelemetry.io/docs/specs/semconv/db/database-metrics/
- system-metrics package: https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-system-metrics
- Steering `dev-environment.md`: Python 3.11 pinned (NOT 3.12)
- Steering `observability-principles.md`: AlwaysOnSampler in SDK, tail sampling at gateway
