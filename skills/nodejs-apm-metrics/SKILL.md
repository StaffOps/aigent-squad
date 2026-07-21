---
name: nodejs-apm-metrics
description: Use when troubleshooting Node.js services via runtime metrics from prom-client default metrics — event loop lag, GC duration, heap memory, active handles/requests, and process stats. All metric names are the REAL Prometheus/VictoriaMetrics names confirmed present in live inventory. OTel semconv (nodejs.*/v8js.*) is NOT used in this environment.
keywords: [nodejs, gc, node, otel, prometheus, victoriametrics, real]
---
# Node.js APM Metrics Reference (prom-client)

> **Confirmed present in live VictoriaMetrics inventory (2026-07-06)**

Metrics emitted by Node.js services using **`prom-client` default metrics** (`collectDefaultMetrics()`). These are the real metric names as they appear in VictoriaMetrics — Prometheus underscore form.

**Source library**: [`prom-client`](https://github.com/siimon/prom-client) (prometheus/client_js)
**Telemetry flow**: App (prom-client `/metrics`) → scraped by vmagent or OTel Collector prometheus receiver → VictoriaMetrics

---

## IMPORTANT: Namespace Clarification

| What this environment uses | What this environment does NOT use |
|---------------------------|-----------------------------------|
| `nodejs_*` — prom-client default metrics (GC, heap, event loop, handles) | `v8js_*` — OTel semconv v8js namespace (ZERO metrics in this env) |
| `process_*` — prom-client process defaults (CPU, memory, FDs) | `nodejs.eventloop.*` / `nodejs.eventloop.delay.*` — OTel semconv dot-notation |
| Prometheus naming convention (underscores) | OTel `@opentelemetry/instrumentation-runtime-node` names |

**GC lives under `nodejs_gc_*` (NOT `v8js_gc_*`)** in this environment. The prom-client library uses the `nodejs_` prefix for all V8/Node.js runtime metrics including garbage collection.

---

## Event Loop Metrics

Source: [`perf_hooks.monitorEventLoopDelay()`](https://nodejs.org/api/perf_hooks.html#perf_hooksmonitoreventloopdelayoptions) — pre-computed percentiles as individual Gauges.

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_eventloop_lag_seconds` | Gauge | seconds | Event loop lag measured via `setImmediate()` delay | Overall loop responsiveness — primary saturation indicator | — |
| `nodejs_eventloop_lag_min_seconds` | Gauge | seconds | Minimum event loop delay in sampling period | Baseline floor — healthy min value | — |
| `nodejs_eventloop_lag_max_seconds` | Gauge | seconds | Maximum event loop delay in sampling period | Spike detection — single worst-case iteration | — |
| `nodejs_eventloop_lag_mean_seconds` | Gauge | seconds | Mean event loop delay in sampling period | Overall saturation trend | — |
| `nodejs_eventloop_lag_stddev_seconds` | Gauge | seconds | Standard deviation of event loop delay | Jitter — inconsistent latency even if mean is OK | — |
| `nodejs_eventloop_lag_p50_seconds` | Gauge | seconds | 50th percentile event loop delay | Typical user-experienced delay | — |
| `nodejs_eventloop_lag_p90_seconds` | Gauge | seconds | 90th percentile event loop delay | Tail latency — most users hit this or better | — |
| `nodejs_eventloop_lag_p99_seconds` | Gauge | seconds | 99th percentile event loop delay | Worst-case tail latency excluding outliers | — |

**CRITICAL design note**: These are **Gauges, NOT a Histogram**. The Node.js `monitorEventLoopDelay()` API returns pre-computed single values (min, max, mean, percentiles). Each percentile is a separate Gauge. No `rate()` needed — read directly.

---

## Garbage Collection Metrics

Source: [`perf_hooks.PerformanceObserver` with `entryTypes: ['gc']`](https://nodejs.org/api/perf_hooks.html#performanceobserverobserveoptions)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_gc_duration_seconds_bucket` | Histogram (bucket) | seconds | GC pause duration distribution | GC pressure — identify which GC type causes pauses | `kind`, `le` |
| `nodejs_gc_duration_seconds_count` | Histogram (count) | — | Total number of GC events | GC frequency — too many collections/sec = allocation pressure | `kind` |
| `nodejs_gc_duration_seconds_sum` | Histogram (sum) | seconds | Total time spent in GC | GC time fraction — % of wall-clock in GC | `kind` |

**`kind` label values** (bounded, low cardinality — 4 values):

| Value | Description | Severity |
|-------|-------------|----------|
| `major` | Mark-Sweep-Compact (stop-the-world, long pause) | 🔴 Critical if frequent/long |
| `minor` | Scavenge (young generation, fast) | 🟢 Normal, usually <5ms |
| `incremental` | Incremental Marking (split across ticks) | 🟡 Usually low impact |
| `weakcb` | Process Weak Callbacks | 🟢 Typically negligible |

**Default histogram buckets**: `[0.001, 0.01, 0.1, 1, 2, 5]` seconds.

---

## Heap Memory Metrics

Source: [`process.memoryUsage()`](https://nodejs.org/api/process.html#processmemoryusage)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_heap_size_total_bytes` | Gauge | bytes | Total heap size (V8 `heapTotal`) | Allocation headroom — total space V8 has reserved | — |
| `nodejs_heap_size_used_bytes` | Gauge | bytes | Used heap size (V8 `heapUsed`) | Memory leak detection — monotonic growth = leak | — |
| `nodejs_external_memory_bytes` | Gauge | bytes | Memory used by C++ objects bound to JS objects (V8 `external`) | Native addon / Buffer memory pressure | — |

---

## Heap Space Metrics (by space)

Source: [`v8.getHeapSpaceStatistics()`](https://nodejs.org/api/v8.html#v8getheapspacestatistics)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_heap_space_size_total_bytes` | Gauge | bytes | Total pre-allocated (virtual) heap per space | Allocation headroom vs V8 limit | `space` |
| `nodejs_heap_space_size_used_bytes` | Gauge | bytes | Currently used heap per space | Identify which space is growing (leak localization) | `space` |
| `nodejs_heap_space_size_available_bytes` | Gauge | bytes | Available (free) heap per space | OOM risk — approaching zero = imminent crash | `space` |

**`space` label values** (bounded, low cardinality):

| Value | Description |
|-------|-------------|
| `new` | Young generation — short-lived objects, minor GC target |
| `old` | Long-lived objects promoted from new_space |
| `code` | JIT-compiled code |
| `large_object` | Objects too large for regular pages |
| `map` | Hidden class / map metadata |

> Note: prom-client strips the `_space` suffix from V8's `space_name` (e.g., `new_space` → `new`).

---

## Active Handles & Requests (Resource Leak Detection)

Source: [`process._getActiveHandles()`](https://nodejs.org/api/process.html) / [`process._getActiveRequests()`](https://nodejs.org/api/process.html)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_active_handles` | Gauge | count | Active libuv handles grouped by type | Identify WHICH handle type is leaking | ⚠️ `type` |
| `nodejs_active_handles_total` | Gauge | count | Total number of active handles | Handle leak detection — monotonic growth = leak | — |
| `nodejs_active_requests` | Gauge | count | Active libuv requests grouped by type | Identify pending I/O request types | ⚠️ `type` |
| `nodejs_active_requests_total` | Gauge | count | Total number of active requests | Request backlog growth | — |

---

## Version Info

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `nodejs_version_info` | Gauge | info (always 1) | Node.js version running | Version audit, detect mixed versions across replicas | `version`, `major`, `minor`, `patch` |

---

## Process Metrics (prom-client defaults)

These are standard Prometheus process metrics emitted by prom-client on Linux. **Confirm presence** in your specific services — they require `/proc` filesystem (always available in Linux containers).

### CPU

Source: [`process.cpuUsage()`](https://nodejs.org/api/process.html#processcpuusagepreviousvalue)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `process_cpu_seconds_total` | Counter | seconds | Total CPU time (user + system) | Overall CPU consumption rate | — |
| `process_cpu_user_seconds_total` | Counter | seconds | CPU time in user space | Application code CPU usage | — |
| `process_cpu_system_seconds_total` | Counter | seconds | CPU time in kernel space | Syscall-heavy workloads (I/O, networking) | — |

### Memory

Source: `/proc/self/status` (Linux) or `process.memoryUsage.rss()` (non-Linux)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `process_resident_memory_bytes` | Gauge | bytes | Resident Set Size (VmRSS) — actual physical memory | OOMKill risk — compare against container `limits.memory` | — |
| `process_virtual_memory_bytes` | Gauge | bytes | Virtual memory size (VmSize) | Address space exhaustion (rare in 64-bit) | — |
| `process_heap_bytes` | Gauge | bytes | Process heap (VmData) — data segment | Heap growth beyond V8 (native addons, buffers) | — |

> Note: `process_virtual_memory_bytes` and `process_heap_bytes` are Linux-only (`/proc/self/status`).

### File Descriptors

Source: `/proc/self/fd` and `/proc/self/limits`

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `process_open_fds` | Gauge | count | Currently open file descriptors | FD leak detection — growing toward max = EMFILE errors | — |
| `process_max_fds` | Gauge | count | Maximum allowed file descriptors (ulimit) | Headroom — `open_fds / max_fds` ratio | — |

### Start Time

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels |
|-------------|------|------|------------------|--------------------|----|
| `process_start_time_seconds` | Gauge | unix seconds | Process start time since epoch | Detect restarts — value changes = pod restarted | — |

---

## Cardinality Warnings

| Label | On Metric | Risk | Mitigation |
|-------|-----------|------|-----------|
| `type` | `nodejs_active_handles`, `nodejs_active_requests` | ⚠️ Medium — C++ class names, generally bounded but can include custom native handles | Monitor cardinality; prefer `_total` metrics for alerting |
| `space` | `nodejs_heap_space_size_*` | ✅ Safe — 5 bounded values from V8 | — |
| `kind` | `nodejs_gc_duration_seconds` | ✅ Safe — exactly 4 values (major/minor/incremental/weakcb) | — |
| `version`, `major`, `minor`, `patch` | `nodejs_version_info` | ✅ Safe — 1 series per pod (info metric) | — |
| `le` | `nodejs_gc_duration_seconds_bucket` | ✅ Safe — fixed bucket count (6 default) | — |

---

## Metric Correlations (How They Interrelate)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Node.js Runtime Health Model                            │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  HTTP latency ←── CAUSED BY ──→ Event loop lag                            │
│  (effect)                        (nodejs_eventloop_lag_p99_seconds)        │
│                                                                           │
│  Event loop lag ←── CAUSED BY ──→ GC pauses (nodejs_gc_duration_seconds)  │
│                                   Long synchronous work                   │
│                                                                           │
│  GC frequency/duration ←── CAUSED BY ──→ Heap growth                      │
│                                           (nodejs_heap_size_used_bytes)   │
│                                                                           │
│  Heap growth ←── VISIBLE IN ──→ Heap space breakdown                      │
│                                  (nodejs_heap_space_size_used_bytes)      │
│                                                                           │
│  Handle/FD leak ←── VISIBLE IN ──→ nodejs_active_handles_total growing    │
│                                    process_open_fds growing               │
│                                                                           │
│  OOMKill risk: process_resident_memory_bytes → container limit            │
│                nodejs_heap_space_size_available_bytes → 0                  │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

| Relationship | Explanation |
|-------------|-------------|
| `nodejs_eventloop_lag_p99_seconds` ↑ → HTTP response time ↑ | Event loop blocking directly adds to request latency (single-threaded) |
| `nodejs_gc_duration_seconds{kind="major"}` ↑ → `nodejs_eventloop_lag_max_seconds` ↑ | Major GC is stop-the-world — directly blocks the loop |
| `nodejs_heap_size_used_bytes` monotonic growth → GC frequency/duration ↑ | More live objects = longer GC sweeps = more frequent collections |
| `nodejs_active_handles_total` growing → `process_open_fds` growing | Unclosed connections/timers keep both handles and FDs alive |
| `nodejs_heap_space_size_available_bytes{space="old"}` → 0 | OOMKill imminent — V8 cannot allocate, will throw or crash |
| `process_resident_memory_bytes` > 80% of container limit | OOMKill risk — includes V8 heap + native memory + buffers |
| `rate(process_cpu_seconds_total[5m])` approaching container CPU limit | CPU throttling — event loop will slow down, lag increases |

---

## Symptom → Metric Troubleshooting Quick Reference

| Symptom | First Metric to Check | Query Example | Second Check | Likely Cause |
|---------|----------------------|---------------|--------------|-------------|
| Slow HTTP responses (p99 spike) | `nodejs_eventloop_lag_p99_seconds` | `nodejs_eventloop_lag_p99_seconds{service_name="my-svc"}` | `rate(nodejs_gc_duration_seconds_sum{kind="major"}[5m])` | Event loop blocked by GC or sync work |
| Increasing latency over time | `nodejs_heap_size_used_bytes` | `deriv(nodejs_heap_size_used_bytes{service_name="my-svc"}[30m])` | `rate(nodejs_gc_duration_seconds_count[5m])` rising | Memory leak → GC pressure → loop delay |
| OOMKilled pod | `nodejs_heap_space_size_available_bytes` | `nodejs_heap_space_size_available_bytes{space="old",service_name="my-svc"}` | `process_resident_memory_bytes` vs limit | Heap exhaustion or native memory growth |
| Intermittent timeouts | `nodejs_eventloop_lag_max_seconds` | `nodejs_eventloop_lag_max_seconds{service_name="my-svc"} > 1` | `nodejs_eventloop_lag_stddev_seconds` | Occasional long GC or CPU-bound burst |
| CPU 100% but low throughput | Event loop lag saturation | `nodejs_eventloop_lag_seconds{service_name="my-svc"} > 0.1` | `rate(nodejs_gc_duration_seconds_sum[5m])` | Loop saturated by GC or sync compute |
| Handle/connection leak | `nodejs_active_handles_total` | `deriv(nodejs_active_handles_total{service_name="my-svc"}[1h]) > 0` | `process_open_fds` | Unclosed connections keeping loop alive |
| FD exhaustion (EMFILE errors) | `process_open_fds` | `process_open_fds{service_name="my-svc"} / process_max_fds{service_name="my-svc"}` | `nodejs_active_handles{type="TCPWrap"}` | Socket/file handle leak |
| Degraded after deploy | GC duration shift | `rate(nodejs_gc_duration_seconds_sum[5m])` pre vs post | `nodejs_heap_size_used_bytes` comparison | New code allocates more or leaks |
| Process restarting silently | `process_start_time_seconds` | `changes(process_start_time_seconds{service_name="my-svc"}[1h])` | Pod events (OOMKilled, CrashLoop) | OOM or unhandled exception |

---

## Example MetricsQL / PromQL Queries

```promql
# Event loop lag p99 (direct Gauge — no rate needed)
nodejs_eventloop_lag_p99_seconds{service_name="my-node-svc"}

# Event loop lag baseline (alerting threshold: >100ms p99 is degraded)
nodejs_eventloop_lag_p99_seconds{service_name="my-node-svc"} > 0.1

# GC pause rate (pauses/sec by kind)
rate(nodejs_gc_duration_seconds_count{service_name="my-node-svc"}[5m])

# GC time fraction (% of wall-clock spent in GC — >5% is concerning)
rate(nodejs_gc_duration_seconds_sum{service_name="my-node-svc"}[5m])

# GC p99 duration (from histogram buckets)
histogram_quantile(0.99, rate(nodejs_gc_duration_seconds_bucket{service_name="my-node-svc"}[5m]))

# Heap growth rate (positive deriv = leak signal)
deriv(nodejs_heap_size_used_bytes{service_name="my-node-svc"}[30m])

# Heap usage ratio (used / total)
nodejs_heap_size_used_bytes{service_name="my-node-svc"}
  / nodejs_heap_size_total_bytes{service_name="my-node-svc"}

# Old space available (OOM proximity)
nodejs_heap_space_size_available_bytes{space="old", service_name="my-node-svc"}

# Handle leak detection (growing total over 1h)
deriv(nodejs_active_handles_total{service_name="my-node-svc"}[1h])

# FD saturation ratio (>0.8 = danger)
process_open_fds{service_name="my-node-svc"}
  / process_max_fds{service_name="my-node-svc"}

# CPU usage rate (cores consumed)
rate(process_cpu_seconds_total{service_name="my-node-svc"}[5m])

# Memory vs container limit (requires kube_pod_container_resource_limits)
process_resident_memory_bytes{service_name="my-node-svc"}
  / on(pod) kube_pod_container_resource_limits{resource="memory"}

# Detect process restarts
changes(process_start_time_seconds{service_name="my-node-svc"}[1h])

# Node.js version audit (find mixed versions)
count by (version) (nodejs_version_info)
```

---

## Alerting Recommendations

| Alert | Condition | Severity |
|-------|-----------|----------|
| High event loop lag | `nodejs_eventloop_lag_p99_seconds > 0.5` for 5m | Critical |
| Event loop degraded | `nodejs_eventloop_lag_p99_seconds > 0.1` for 10m | Warning |
| Memory leak detected | `deriv(nodejs_heap_size_used_bytes[1h]) > 10e6` (>10MB/h growth) | Warning |
| GC time excessive | `rate(nodejs_gc_duration_seconds_sum[5m]) > 0.05` (>5% CPU in GC) | Warning |
| FD exhaustion risk | `process_open_fds / process_max_fds > 0.8` | Critical |
| OOMKill proximity | `process_resident_memory_bytes / container_limit > 0.9` | Critical |

---

## Anti-patterns

- ❌ Looking for `v8js_*` metrics — they do NOT exist in this environment
- ❌ Looking for `nodejs.eventloop.delay.*` (OTel semconv dot-notation) — not used here
- ❌ Treating `nodejs_eventloop_lag_*` as a Histogram — they are individual Gauges (percentiles pre-computed by Node.js runtime)
- ❌ Using `rate()` on event loop lag Gauges — they are instantaneous values, not counters
- ❌ Alerting only on CPU/memory for Node.js — event loop lag is the primary saturation signal
- ❌ Ignoring `kind` label on GC — a `major` GC at 500ms is critical; a `minor` at 5ms is normal
- ❌ Using `nodejs_active_handles` (by type) for alerting — prefer `_total` to avoid cardinality risk
- ❌ Assuming `nodejs_heap_size_used_bytes` includes native memory — it doesn't; use `process_resident_memory_bytes` for total
- ❌ Confusing `nodejs_heap_size_total_bytes` with container memory limit — it's only V8's reserved heap

---

## Source Reference

| Metric Group | Source Library | Source File | Node.js API |
|-------------|---------------|-------------|-------------|
| Event loop lag | `prom-client` | `lib/metrics/eventLoopLag.js` | `perf_hooks.monitorEventLoopDelay()` |
| GC duration | `prom-client` | `lib/metrics/gc.js` | `PerformanceObserver({ entryTypes: ['gc'] })` |
| Heap total/used/external | `prom-client` | `lib/metrics/heapSizeAndUsed.js` | `process.memoryUsage()` |
| Heap spaces | `prom-client` | `lib/metrics/heapSpacesSizeAndUsed.js` | `v8.getHeapSpaceStatistics()` |
| Active handles | `prom-client` | `lib/metrics/processHandles.js` | `process._getActiveHandles()` |
| Active requests | `prom-client` | `lib/metrics/processRequests.js` | `process._getActiveRequests()` |
| Version info | `prom-client` | `lib/metrics/version.js` | `process.version` |
| CPU | `prom-client` | `lib/metrics/processCpuTotal.js` | `process.cpuUsage()` |
| Memory (RSS/VM/Heap) | `prom-client` | `lib/metrics/osMemoryHeap.js` + `osMemoryHeapLinux.js` | `/proc/self/status` |
| Open FDs | `prom-client` | `lib/metrics/processOpenFileDescriptors.js` | `/proc/self/fd` |
| Max FDs | `prom-client` | `lib/metrics/processMaxFileDescriptors.js` | `/proc/self/limits` |
| Start time | `prom-client` | `lib/metrics/processStartTime.js` | `process.uptime()` + `Date.now()` |
