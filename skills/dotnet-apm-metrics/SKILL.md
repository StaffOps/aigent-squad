---
name: dotnet-apm-metrics
description: "Use when troubleshooting .NET services via runtime metrics, interpreting GC/ThreadPool/JIT/HTTP telemetry, or building Grafana dashboards for .NET 6\u201310 workloads. Covers LEGACY (process.runtime.dotnet.* via Instrumentation.Runtime NuGet) and NATIVE (.NET 9+ dotnet.* via System.Runtime meter) eras, ASP.NET Core hosting/Kestrel/HttpClient metrics (.NET 8+), EF Core, SignalR, and DNS."
keywords: [dotnet, dns, gc, grafana, jit, threadpool, net, legacy, native, asp, ef]
---
# .NET APM Metrics Reference

Two distinct metric eras exist in .NET. Mixing them up causes silent gaps in dashboards.

| Era | .NET versions | Source | Metric prefix | Stability |
|-----|---------------|--------|---------------|-----------|
| **LEGACY** | 6, 7, 8 | `OpenTelemetry.Instrumentation.Runtime` NuGet | `process.runtime.dotnet.*` | Stable (OTel contrib) |
| **NATIVE** | **9+** | Built-in `System.Runtime` meter (no NuGet needed) | `dotnet.*` | Stable (OTel semconv) |
| **Web/HTTP** | **8+** | Built-in `Microsoft.AspNetCore.*`, `System.Net.*` meters | `http.server.*`, `http.client.*`, `kestrel.*`, `dns.*` | Stable (OTel semconv) |
| **EF Core** | **9+** (EF Core 9) | Built-in `Microsoft.EntityFrameworkCore` meter | `microsoft.entityframeworkcore.*` | Stable |

> **TRAP**: `dotnet.*` runtime metrics are **.NET 9+ ONLY**. Do NOT expect them on .NET 8.
> On .NET 9+, `OpenTelemetry.Instrumentation.Runtime` simply subscribes to the built-in `System.Runtime` meter — it no longer emits `process.runtime.dotnet.*` names.

Sources:
- https://learn.microsoft.com/en-us/dotnet/core/diagnostics/built-in-metrics-runtime
- https://github.com/open-telemetry/opentelemetry-dotnet-contrib/blob/main/src/OpenTelemetry.Instrumentation.Runtime/README.md
- https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-http-metrics/

---

## LEGACY Runtime Metrics (.NET 6/7/8) — `OpenTelemetry.Instrumentation.Runtime`

Requires NuGet package `OpenTelemetry.Instrumentation.Runtime`. Meter name is internal to the package.
These names are **NOT** OTel semconv — they are package-specific.

Source: https://github.com/open-telemetry/opentelemetry-dotnet-contrib/blob/main/src/OpenTelemetry.Instrumentation.Runtime/README.md

| OTel metric name | VM translated name | Instrument | Unit | Measures | Attributes | Min .NET |
|---|---|---|---|---|---|---|
| `process.runtime.dotnet.gc.collections.count` | `process_runtime_dotnet_gc_collections_count_total` | Counter | `{collections}` | GC collections since start, per generation | `generation`=gen0/gen1/gen2 | 6 |
| `process.runtime.dotnet.gc.objects.size` | `process_runtime_dotnet_gc_objects_size_bytes` | UpDownCounter | `By` | Live object bytes on GC heap (excl. fragmentation) | — | 6 |
| `process.runtime.dotnet.gc.allocations.size` | `process_runtime_dotnet_gc_allocations_size_bytes_total` | Counter | `By` | Bytes allocated on managed heap since start | — | 6 |
| `process.runtime.dotnet.gc.committed_memory.size` | `process_runtime_dotnet_gc_committed_memory_size_bytes` | UpDownCounter | `By` | Committed virtual memory for GC | — | 6 |
| `process.runtime.dotnet.gc.heap.size` | `process_runtime_dotnet_gc_heap_size_bytes` | UpDownCounter | `By` | Heap size incl. fragmentation per generation | `generation`=gen0/gen1/gen2/loh/poh | 6 |
| `process.runtime.dotnet.gc.heap.fragmentation.size` | `process_runtime_dotnet_gc_heap_fragmentation_size_bytes` | UpDownCounter | `By` | Fragmentation per generation | `generation`=gen0/gen1/gen2/loh/poh | 7 |
| `process.runtime.dotnet.gc.duration` | `process_runtime_dotnet_gc_duration_nanoseconds_total` | Counter | `ns` | Total GC pause time since start | — | 7 |
| `process.runtime.dotnet.jit.il_compiled.size` | `process_runtime_dotnet_jit_il_compiled_size_bytes_total` | Counter | `By` | IL bytes JIT-compiled since start | — | 6 |
| `process.runtime.dotnet.jit.methods_compiled.count` | `process_runtime_dotnet_jit_methods_compiled_count_total` | Counter | `{methods}` | Methods JIT-compiled since start | — | 6 |
| `process.runtime.dotnet.jit.compilation_time` | `process_runtime_dotnet_jit_compilation_time_nanoseconds_total` | Counter | `ns` | Time spent JIT-compiling | — | 6 |
| `process.runtime.dotnet.monitor.lock_contention.count` | `process_runtime_dotnet_monitor_lock_contention_count_total` | Counter | `{contended_acquisitions}` | Lock contentions since start | — | 6 |
| `process.runtime.dotnet.thread_pool.threads.count` | `process_runtime_dotnet_thread_pool_threads_count` | UpDownCounter | `{threads}` | Current thread pool threads | — | 6 |
| `process.runtime.dotnet.thread_pool.completed_items.count` | `process_runtime_dotnet_thread_pool_completed_items_count_total` | Counter | `{items}` | Work items completed since start | — | 6 |
| `process.runtime.dotnet.thread_pool.queue.length` | `process_runtime_dotnet_thread_pool_queue_length` | UpDownCounter | `{items}` | Queued work items waiting | — | 6 |
| `process.runtime.dotnet.timer.count` | `process_runtime_dotnet_timer_count` | UpDownCounter | `{timers}` | Active timer instances | — | 6 |
| `process.runtime.dotnet.assemblies.count` | `process_runtime_dotnet_assemblies_count` | UpDownCounter | `{assemblies}` | Loaded assemblies | — | 6 |
| `process.runtime.dotnet.exceptions.count` | `process_runtime_dotnet_exceptions_count_total` | Counter | `{exceptions}` | Exceptions thrown since instrumentation init | — | 6 |

### Troubleshooting use (LEGACY)

| Metric | What it tells you |
|--------|-------------------|
| `gc.collections.count` (gen2 rising) | Memory pressure — too many full GCs |
| `gc.heap.size` monotonically growing | Possible memory leak |
| `gc.duration` large relative to wall-clock | GC pauses impacting latency |
| `thread_pool.queue.length` > 0 sustained | Thread starvation — work waiting |
| `thread_pool.threads.count` near max | Thread pool saturation |
| `monitor.lock_contention.count` rising fast | Lock contention bottleneck |
| `exceptions.count` spike | Exception storm (possibly unhandled) |
| `jit.compilation_time` high at startup | Cold start penalty — consider ReadyToRun/AOT |

---

## NATIVE Runtime Metrics (.NET 9+) — Built-in `System.Runtime` meter

**Available starting in: .NET 9.** No NuGet package required. OTel SDK automatically collects these.
Stability: **Stable** (OTel semconv).

Source: https://learn.microsoft.com/en-us/dotnet/core/diagnostics/built-in-metrics-runtime

| OTel metric name | VM translated name | Instrument | Unit | Measures | Attributes | For |
|---|---|---|---|---|---|---|
| `dotnet.process.cpu.time` | `dotnet_process_cpu_time_seconds_total` | Counter | `s` | CPU time used by process | `cpu.mode`=user/system | CPU saturation per mode |
| `dotnet.process.memory.working_set` | `dotnet_process_memory_working_set_bytes` | UpDownCounter | `By` | Physical memory mapped to process | — | Memory growth / OOM risk |
| _(env extra)_ | `dotnet_total_memory_bytes` | Gauge | `By` | Managed heap bytes in use (`GC.GetTotalMemory`) | — | Quick managed-memory gauge |
| _(env extra)_ | `dotnet_collection_count_total` | Counter | `{collection}` | GC collections (legacy exporter counter) | — | GC frequency (overlaps `dotnet_gc_collections_total`) |
| `dotnet.gc.collections` | `dotnet_gc_collections_total` | Counter | `{collection}` | GC collections since start | `gc.heap.generation`=gen0/gen1/gen2 | GC pressure by generation |
| `dotnet.gc.heap.total_allocated` | `dotnet_gc_heap_allocated_bytes_total` | Counter | `By` | Bytes allocated on managed heap since start | — | Allocation rate (high = GC pressure) |
| `dotnet.gc.last_collection.memory.committed_size` | `dotnet_gc_last_collection_memory_committed_size_bytes` | UpDownCounter | `By` | Committed VM for GC (last collection) | — | Memory footprint |
| `dotnet.gc.last_collection.heap.size` | `dotnet_gc_last_collection_heap_size_bytes` | UpDownCounter | `By` | Heap size incl. fragmentation | `gc.heap.generation`=gen0/gen1/gen2/loh/poh | Per-gen heap growth |
| `dotnet.gc.last_collection.heap.fragmentation.size` | `dotnet_gc_last_collection_heap_fragmentation_size_bytes` | UpDownCounter | `By` | Fragmentation per generation | `gc.heap.generation`=gen0/gen1/gen2/loh/poh | Fragmentation ratio |
| `dotnet.gc.pause.time` | `dotnet_gc_pause_time_seconds_total` | Counter | `s` | Total GC pause duration | — | % time paused in GC |
| `dotnet.jit.compiled_il.size` | `dotnet_jit_compiled_il_size_bytes_total` | Counter | `By` | IL bytes compiled | — | JIT workload |
| `dotnet.jit.compiled_methods` | `dotnet_jit_compiled_methods_total` | Counter | `{method}` | Methods (re)compiled | — | JIT activity |
| `dotnet.jit.compilation.time` | `dotnet_jit_compilation_time_seconds_total` | Counter | `s` | Time spent JIT-compiling | — | Startup cost |
| `dotnet.thread_pool.thread.count` | `dotnet_thread_pool_thread_count_total` | UpDownCounter | `{thread}` | Current thread pool threads | — | Thread pool sizing |
| `dotnet.thread_pool.work_item.count` | `dotnet_thread_pool_work_item_count_total` | Counter | `{work_item}` | Work items completed | — | Throughput |
| `dotnet.thread_pool.queue.length` | `dotnet_thread_pool_queue_length_total` | UpDownCounter | `{work_item}` | Queued work items | — | Thread starvation signal |
| `dotnet.monitor.lock_contentions` | `dotnet_monitor_lock_contentions_total` | Counter | `{contention}` | Lock contentions since start | — | Contention bottleneck |
| `dotnet.timer.count` | `dotnet_timer_count` | UpDownCounter | `{timer}` | Active timer instances | — | Timer leak detection |
| `dotnet.assembly.count` | `dotnet_assembly_count` | UpDownCounter | `{assembly}` | Loaded assemblies | — | Assembly leak |
| `dotnet.exceptions` | `dotnet_exceptions_total` | Counter | `{exception}` | Exceptions thrown | `error.type` (exception FQN) | Exception storms |

> ⚠️ **Cardinality warning**: `dotnet.exceptions` attribute `error.type` carries the full exception type name. In apps with many distinct exception types this can cause cardinality explosion. Monitor series count.

---

## LEGACY → NATIVE Mapping Table

| LEGACY (`process.runtime.dotnet.*`) | NATIVE (`dotnet.*`) | Semantic differences |
|---|---|---|
| `gc.collections.count` | `gc.collections` | Same concept; attribute renamed `generation` → `gc.heap.generation` |
| `gc.objects.size` | *(no direct equivalent)* | Legacy = live objects only; Native `gc.last_collection.heap.size` includes fragmentation |
| `gc.allocations.size` | `gc.heap.total_allocated` | Same semantics |
| `gc.committed_memory.size` | `gc.last_collection.memory.committed_size` | Same semantics |
| `gc.heap.size` | `gc.last_collection.heap.size` | Same semantics (per generation) |
| `gc.heap.fragmentation.size` | `gc.last_collection.heap.fragmentation.size` | Same semantics |
| `gc.duration` (unit: `ns`) | `gc.pause.time` (unit: `s`) | **Unit changed** from nanoseconds to seconds |
| `jit.il_compiled.size` | `jit.compiled_il.size` | Same semantics, name reworded |
| `jit.methods_compiled.count` | `jit.compiled_methods` | Same semantics |
| `jit.compilation_time` (unit: `ns`) | `jit.compilation.time` (unit: `s`) | **Unit changed** from nanoseconds to seconds |
| `monitor.lock_contention.count` | `monitor.lock_contentions` | Same semantics, name pluralized |
| `thread_pool.threads.count` | `thread_pool.thread.count` | Same semantics |
| `thread_pool.completed_items.count` | `thread_pool.work_item.count` | Same semantics, renamed |
| `thread_pool.queue.length` | `thread_pool.queue.length` | Same semantics |
| `timer.count` | `timer.count` | Same semantics |
| `assemblies.count` | `assembly.count` | Same semantics |
| `exceptions.count` | `exceptions` | Native adds `error.type` attribute |
| *(no equivalent)* | `process.cpu.time` | New in .NET 9 |
| *(no equivalent)* | `process.memory.working_set` | New in .NET 9 |

> **Migration note**: When upgrading from .NET 8 → 9, your Grafana dashboards MUST switch metric names.
> The old `process_runtime_dotnet_*` metrics will stop appearing entirely.

---

## ASP.NET Core HTTP Server Metrics (.NET 8+)

Meter: `Microsoft.AspNetCore.Hosting`. Stability: **Stable** (OTel semconv).

Source: https://learn.microsoft.com/en-us/aspnet/core/log-mon/metrics/built-in | https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-http-metrics/

| OTel metric name | VM translated name | Instrument | Unit | Measures | Key attributes |
|---|---|---|---|---|---|
| `http.server.request.duration` | `http_server_request_duration_seconds` | Histogram | `s` | Duration of inbound HTTP requests | `http.request.method`, `http.response.status_code`, `http.route`, `url.scheme`, `error.type` |
| `http.server.active_requests` | `http_server_active_requests` | UpDownCounter | `{request}` | Concurrent in-flight requests | `http.request.method`, `url.scheme` |

**Default histogram buckets**: [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]

> ⚠️ **Cardinality**: `http.route` is bounded by endpoint count (safe). `error.type` can include status codes like `404`, `500` — bounded. Avoid adding custom tags with user/request IDs.

---

## Kestrel Server Metrics (.NET 8+)

Meter: `Microsoft.AspNetCore.Server.Kestrel`. Stability: **Stable** (OTel semconv).

Source: https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-kestrel-metrics/

| OTel metric name | VM translated name | Instrument | Unit | Measures | Key attributes |
|---|---|---|---|---|---|
| `kestrel.active_connections` | `kestrel_active_connections` | UpDownCounter | `{connection}` | Currently active connections | `network.transport`, `server.address`, `server.port` |
| `kestrel.connection.duration` | `kestrel_connection_duration_seconds` | Histogram | `s` | Connection lifetime | `network.transport`, `network.protocol.version`, `tls.protocol.version`, `error.type` |
| `kestrel.rejected_connections` | `kestrel_rejected_connections_total` | Counter | `{connection}` | Connections rejected (max exceeded) — ⚠️ **NOT present in this env's inventory**; only `kestrel_queued_connections`/`kestrel_queued_requests` are emitted | `network.transport`, `server.address`, `server.port` |
| `kestrel.queued_connections` | `kestrel_queued_connections` | UpDownCounter | `{connection}` | Connections waiting to start | `network.transport`, `server.address`, `server.port` |
| `kestrel.queued_requests` | `kestrel_queued_requests` | UpDownCounter | `{request}` | HTTP/2+3 requests queued on connections | `network.protocol.version`, `server.address`, `server.port` |
| `kestrel.upgraded_connections` | `kestrel_upgraded_connections` | UpDownCounter | `{connection}` | Upgraded connections (WebSocket) | `network.transport`, `server.address`, `server.port` |
| `kestrel.tls_handshake.duration` | `kestrel_tls_handshake_duration_seconds` | Histogram | `s` | TLS handshake time | `tls.protocol.version`, `error.type` |
| `kestrel.active_tls_handshakes` | `kestrel_active_tls_handshakes` | UpDownCounter | `{handshake}` | In-progress TLS handshakes | `network.transport`, `server.address`, `server.port` |

---

## HTTP Client Metrics (.NET 8+)

Meter: `System.Net.Http`. Stability: **Stable** (OTel semconv).

Source: https://learn.microsoft.com/en-us/dotnet/core/diagnostics/built-in-metrics-system-net | https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-http-metrics/

| OTel metric name | VM translated name | Instrument | Unit | Measures | Key attributes |
|---|---|---|---|---|---|
| `http.client.request.duration` | `http_client_request_duration_seconds` | Histogram | `s` | Duration of outbound HTTP requests (up to headers received) | `http.request.method`, `http.response.status_code`, `server.address`, `url.scheme`, `error.type` |
| `http.client.active_requests` | `http_client_active_requests` | UpDownCounter | `{request}` | In-flight outbound requests | `http.request.method`, `server.address`, `url.scheme` |
| `http.client.open_connections` | `http_client_open_connections` | UpDownCounter | `{connection}` | Active + idle connections in pool | `http.connection.state`=active/idle, `network.protocol.version`, `server.address`, `url.scheme` |
| `http.client.connection.duration` | `http_client_connection_duration_seconds` | Histogram | `s` | Lifetime of outbound connections | `network.protocol.version`, `server.address`, `url.scheme` |
| `http.client.request.time_in_queue` | `http_client_request_time_in_queue_seconds` | Histogram | `s` | Time waiting for available connection | `http.request.method`, `network.protocol.version`, `server.address`, `url.scheme` |

> ⚠️ **Cardinality**: `server.address` is per-host — safe if calling few backends. If calling many dynamic hosts (e.g., per-tenant URLs), this becomes HIGH cardinality.

---

## DNS Metrics (.NET 8+)

Meter: `System.Net.NameResolution`. Stability: **Stable** (OTel semconv).

Source: https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-dns-metrics/

| OTel metric name | VM translated name | Instrument | Unit | Measures | Key attributes |
|---|---|---|---|---|---|
| `dns.lookup.duration` | `dns_lookup_duration_seconds` | Histogram | `s` | Time to perform DNS lookup | `dns.question.name`, `error.type` |

> ⚠️ **Cardinality**: `dns.question.name` is per-hostname. Safe for services calling a fixed set of backends. Dangerous for services resolving user-supplied hostnames.

---

## SignalR Metrics (.NET 8+)

Meter: `Microsoft.AspNetCore.Http.Connections`. Stability: **Stable** (OTel semconv).

Source: https://opentelemetry.io/docs/specs/semconv/dotnet/dotnet-signalr-metrics/

| OTel metric name | VM translated name | Instrument | Unit | Measures | Key attributes |
|---|---|---|---|---|---|
| `signalr.server.connection.duration` | `signalr_server_connection_duration_seconds` | Histogram | `s` | SignalR connection lifetime | `signalr.connection.status`, `signalr.transport` |
| `signalr.server.active_connections` | `signalr_server_active_connections` | UpDownCounter | `{connection}` | Currently active SignalR connections | `signalr.connection.status`, `signalr.transport` |

Attributes: `signalr.transport` = `web_sockets` | `long_polling` | `server_sent_events`. `signalr.connection.status` = `normal_closure` | `timeout` | `app_shutdown`.

---

## Entity Framework Core Metrics (EF Core 9+ / .NET 8+)

Meter: `Microsoft.EntityFrameworkCore`. Available starting in: **EF Core 9.0**.

Source: https://learn.microsoft.com/en-us/ef/core/logging-events-diagnostics/metrics

| OTel metric name | VM translated name | Instrument | Unit | Measures | For |
|---|---|---|---|---|---|
| `microsoft.entityframeworkcore.active_dbcontexts` | `microsoft_entityframeworkcore_active_dbcontexts` | ObservableUpDownCounter | `{dbcontext}` | Active DbContext instances | DbContext leak detection |
| `microsoft.entityframeworkcore.queries` | `microsoft_entityframeworkcore_queries_total` | ObservableCounter | `{query}` | Queries executed | Query throughput |
| `microsoft.entityframeworkcore.savechanges` | `microsoft_entityframeworkcore_savechanges_total` | ObservableCounter | `{savechanges}` | SaveChanges calls | Write throughput |
| `microsoft.entityframeworkcore.compiled_query_cache_hits` | `microsoft_entityframeworkcore_compiled_query_cache_hits_total` | ObservableCounter | `{hits}` | Query cache hits | Cache effectiveness |
| `microsoft.entityframeworkcore.compiled_query_cache_misses` | `microsoft_entityframeworkcore_compiled_query_cache_misses_total` | ObservableCounter | `{misses}` | Query cache misses | Dynamic query problems |
| `microsoft.entityframeworkcore.execution_strategy_operation_failures` | `microsoft_entityframeworkcore_execution_strategy_operation_failures_total` | ObservableCounter | `{failure}` | Failed operations (retried) | Transient DB errors |
| `microsoft.entityframeworkcore.optimistic_concurrency_failures` | `microsoft_entityframeworkcore_optimistic_concurrency_failures_total` | ObservableCounter | `{failure}` | Optimistic concurrency conflicts | Contention on writes |

> **Key insight**: `cache_misses / (cache_hits + cache_misses)` should be ~0% after warmup. Rising miss rate = dynamic LINQ generating unique queries (N+1, string interpolation in queries).

---

## Metric Correlation — How They Interrelate

```
http.server.request.duration (p99 rising)
  │
  ├─► dotnet.gc.pause.time increasing → GC pauses adding latency
  │     └─► dotnet.gc.heap.total_allocated rate high → allocation-heavy code path
  │
  ├─► dotnet.thread_pool.queue.length > 0 sustained → thread starvation
  │     └─► dotnet.thread_pool.thread.count near plateau → pool can't grow fast enough
  │           └─► dotnet.monitor.lock_contentions rising → sync-over-async or lock contention
  │
  ├─► http.client.request.time_in_queue rising → connection pool exhaustion to downstream
  │     └─► http.client.open_connections (idle=0, active=max) → confirm pool saturation
  │           └─► dns.lookup.duration spike → DNS resolution slow
  │
  └─► kestrel.queued_connections > 0 → inbound connection backpressure
        └─► kestrel.rejected_connections rising → MaxConcurrentConnections hit
```

### Key relationships

| Signal A | Signal B | Meaning |
|----------|----------|---------|
| `http.server.request.duration` p99 ↑ | `gc.pause.time` rate ↑ | GC pauses causing tail latency |
| `gc.collections` (gen2) rate ↑ | `gc.heap.total_allocated` rate ↑ | High allocation rate forcing full GC |
| `thread_pool.queue.length` ↑ | `thread_pool.thread.count` flat | Thread starvation (pool can't scale) |
| `http.client.request.time_in_queue` ↑ | `http.client.open_connections` (active high, idle 0) | Connection pool exhaustion |
| `http.server.active_requests` ↑ | `kestrel.active_connections` stable | Requests piling up without new connections |
| `ef.compiled_query_cache_misses` rate ↑ | `http.server.request.duration` p99 ↑ | Query compilation overhead on hot path |
| `dotnet.exceptions` spike | `http.server.request.duration` p99 ↑ | Exception storms adding overhead |

---

## Symptom → Metric Troubleshooting Quick Reference

| Symptom | First metrics to check | What to look for |
|---------|------------------------|------------------|
| **Slow HTTP responses** | `http.server.request.duration` (p99), `http.client.request.duration` | Which side is slow? Server processing or downstream call? |
| **Timeout errors to downstream** | `http.client.request.time_in_queue`, `http.client.open_connections` | Queue time > 0 = pool exhausted |
| **Memory growth / OOMKill** | `dotnet.process.memory.working_set` or `gc.heap.size`, `gc.last_collection.memory.committed_size` | Monotonic growth = leak |
| **Thread starvation** | `dotnet.thread_pool.queue.length`, `thread_pool.thread.count` | Queue growing while thread count flat |
| **GC pauses impacting latency** | `dotnet.gc.pause.time`, `gc.collections` (gen2 rate) | High gen2 rate + rising pause time |
| **Connection pool exhaustion** | `http.client.open_connections` (active=max, idle=0), `request.time_in_queue` | All connections busy, requests wait |
| **High error rate** | `http.server.request.duration` + filter `http.response.status_code >= 500` | Isolate error-producing routes |
| **Lock contention** | `dotnet.monitor.lock_contentions` rate, `thread_pool.queue.length` | Rising contention blocks threads |
| **Slow startup / cold start** | `dotnet.jit.compilation.time`, `jit.compiled_methods` | Large JIT workload at startup |
| **EF query perf degradation** | `ef.compiled_query_cache_misses` rate, `ef.queries` rate | Miss rate >5% after warmup = problem |
| **DNS resolution slow** | `dns.lookup.duration` p99 | Spikes correlate with downstream timeouts |
| **Kestrel connection rejection** | `kestrel.rejected_connections`, `kestrel.queued_connections` | MaxConcurrentConnections too low |
| **TLS handshake slow** | `kestrel.tls_handshake.duration` | Certificate chain validation slow |
| **SignalR connection drop** | `signalr.server.connection.duration`, `active_connections` | Short durations = unstable clients |

---

## VictoriaMetrics Query Examples

```promql
# Request rate by route (RED: Rate)
sum(rate(http_server_request_duration_seconds_count[5m])) by (http_route)

# p99 latency by route (RED: Duration)
histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (le, http_route))

# Error rate (RED: Errors)
sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
/
sum(rate(http_server_request_duration_seconds_count[5m]))

# Thread pool starvation signal
dotnet_thread_pool_queue_length > 0

# GC pause fraction (should be < 5%)
rate(dotnet_gc_pause_time_seconds_total[5m])

# Connection pool saturation
http_client_open_connections{http_connection_state="active"}
/
(http_client_open_connections{http_connection_state="active"} + http_client_open_connections{http_connection_state="idle"})

# EF query cache miss rate
rate(microsoft_entityframeworkcore_compiled_query_cache_misses_total[5m])
/
(rate(microsoft_entityframeworkcore_compiled_query_cache_hits_total[5m]) + rate(microsoft_entityframeworkcore_compiled_query_cache_misses_total[5m]))
```

---

## Cardinality Risk Summary

| Attribute | Risk | Reason |
|-----------|------|--------|
| `http.route` | ✅ Low | Bounded by endpoint count |
| `http.request.method` | ✅ Low | ~7 values |
| `http.response.status_code` | ✅ Low | ~50 values |
| `server.address` (client) | ⚠️ Medium | One series per destination host |
| `dns.question.name` | ⚠️ Medium | One series per hostname resolved |
| `error.type` (on `dotnet.exceptions`) | ⚠️ Medium | One series per exception type FQN |
| `network.peer.address` | 🔴 High | One series per peer IP — avoid in dashboards |
| User ID / Request ID | 🔴 NEVER | Unbounded — use traces, not metrics |

---

## Version Availability Summary

| Metric category | .NET 6 | .NET 7 | .NET 8 | .NET 9 | .NET 10 |
|----------------|--------|--------|--------|--------|---------|
| `process.runtime.dotnet.*` (legacy) | ✅ | ✅ | ✅ | ❌ (replaced) | ❌ |
| `dotnet.*` (native runtime) | ❌ | ❌ | ❌ | ✅ | ✅ |
| `http.server.*` / `http.client.*` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `kestrel.*` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `dns.lookup.duration` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `signalr.server.*` | ❌ | ❌ | ✅ | ✅ | ✅ |
| `microsoft.entityframeworkcore.*` | ❌ | ❌ | ❌ | ✅ (EF9) | ✅ |
