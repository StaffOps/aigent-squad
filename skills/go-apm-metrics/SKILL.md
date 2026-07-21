---
name: go-apm-metrics
description: Use when troubleshooting Go services via runtime metrics confirmed present in the organization's live VictoriaMetrics. Covers client_golang + runtime/metrics collector names (go_memstats_*, go_gc_*, go_sched_*, go_memory_classes_*, go_cpu_classes_*, go_sync_*, go_sql_*). NOT OTel semconv go.* names — those do NOT exist in this environment.
keywords: [go, collector, otel, victoriametrics]
---
# Go APM Metrics — Environment-Anchored Reference

**Confirmed present in live VictoriaMetrics inventory (2026-07-06).**

All metrics in this document are from the **Prometheus `client_golang`** library (`github.com/prometheus/client_golang/prometheus/collectors`), which exposes Go runtime state via the `runtime/metrics` package and legacy `runtime.MemStats`. These are the names as they appear in VictoriaMetrics after scraping.

> ⚠️ **OTel semconv `go.*` names (`go_memory_used_bytes`, `go_goroutine_count`, `go_schedule_duration_seconds`) are NOT present in this environment.** The Go services here use `client_golang` native collectors, not the OTel `go.opentelemetry.io/contrib/instrumentation/runtime` package.

**Pipeline**: Go app (client_golang `/metrics`) → vmagent scrape → VictoriaMetrics.
**Backends**: VictoriaMetrics (MetricsQL/PromQL), Tempo, Loki, Grafana.

---

## Source & Name Conversion

client_golang converts `runtime/metrics` keys to Prometheus names using:
```
/sched/latencies:seconds → go_sched_latencies_seconds (+ _bucket/_count/_sum for histograms)
/memory/classes/total:bytes → go_memory_classes_total_bytes
/cpu/classes/user:cpu-seconds → go_cpu_classes_user_cpu_seconds_total (cumulative → _total)
/sync/mutex/wait/total:seconds → go_sync_mutex_wait_total_seconds_total
```

Rule: namespace=`go`, subsystem=path (/ and - → _), name=last element, unit suffix appended, `_total` for counters/cumulatives.

---

## 1. Goroutines & Threads

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_goroutines` | Gauge | goroutines | Count of live goroutines (`/sched/goroutines:goroutines`) | Goroutine leak detection (monotonic rise) | — | ✅ |
| `go_threads` | Gauge | threads | Count of OS threads created by the runtime (`/sched/threads/total:threads`) | Excessive thread creation (cgo, blocked syscalls) | — | ✅ |
| `go_gomaxprocs` | Gauge | threads | Current GOMAXPROCS setting (`/sched/gomaxprocs:threads`) | Know parallelism ceiling; goroutines >> GOMAXPROCS = saturation | — | ✅ |
| `go_sched_goroutines_goroutines` | Gauge | goroutines | Same as `go_goroutines` — runtime/metrics variant | Alias; use either | — | ✅ |

### Scheduler state breakdown (runtime/metrics advanced)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_sched_goroutines_runnable_goroutines` | Gauge | goroutines | Goroutines ready to run but not executing (`/sched/goroutines/runnable:goroutines`) | High value = scheduler saturation (not enough P's) | — | ✅ |
| `go_sched_goroutines_running_goroutines` | Gauge | goroutines | Goroutines currently executing (`/sched/goroutines/running:goroutines`) | Should be ≤ GOMAXPROCS | — | ✅ |
| `go_sched_goroutines_waiting_goroutines` | Gauge | goroutines | Goroutines waiting on I/O or sync primitives (`/sched/goroutines/waiting:goroutines`) | High and rising = resource starvation | — | ✅ |

---

## 2. Scheduler Saturation (KEY)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_sched_latencies_seconds_bucket` | Histogram | seconds | Distribution of time goroutines spend in runnable state before running (`/sched/latencies:seconds`) | **Primary scheduler saturation indicator** — p99 > 10ms = starvation | `le` | ✅ |
| `go_sched_latencies_seconds_count` | Counter | — | Total observations of scheduler latency | Rate = goroutine scheduling throughput | — | ✅ |
| `go_sched_latencies_seconds_sum` | Counter | seconds | Sum of all scheduling latencies | Average scheduling latency = sum/count | — | ✅ |
| `go_sched_pauses_total_gc_seconds_bucket` | Histogram | seconds | Distribution of total GC stop-the-world pause durations (`/sched/pauses/total/gc:seconds`) | Long p99 = latency spikes during GC | `le` | ✅ |
| `go_sched_pauses_total_gc_seconds_count` | Counter | — | Count of GC STW pause events | GC pause frequency | — | ✅ |
| `go_sched_pauses_total_gc_seconds_sum` | Counter | seconds | Cumulative GC STW pause time | Total time spent paused for GC | — | ✅ |
| `go_sched_pauses_stopping_gc_seconds_bucket` | Histogram | seconds | Distribution of time to actually stop all P's for GC (`/sched/pauses/stopping/gc:seconds`) — subset of total pause | Slow STW stopping = thread preemption issues | `le` | ✅ |
| `go_sched_pauses_stopping_gc_seconds_count` | Counter | — | Count of GC stopping events | — | — | ✅ |
| `go_sched_pauses_stopping_gc_seconds_sum` | Counter | seconds | Cumulative GC stopping time | — | — | ✅ |

---

## 3. Garbage Collection

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_gc_duration_seconds` | Summary | seconds | GC invocation durations (legacy MemStats-era metric) | Quick GC duration check via quantiles | `quantile` (0, 0.25, 0.5, 0.75, 1) | ✅ |
| `go_gc_duration_seconds_count` | Counter | — | Total GC cycles completed | GC frequency (rate) | — | ✅ |
| `go_gc_duration_seconds_sum` | Counter | seconds | Cumulative GC duration | Total CPU time in GC pauses | — | ✅ |
| `go_gc_gogc_percent` | Gauge | percent | GOGC value — heap growth trigger (`/gc/gogc:percent`) | Default 100; lower = more frequent GC; 0 = very aggressive | — | ✅ |
| `go_gc_gomemlimit_bytes` | Gauge | bytes | GOMEMLIMIT value (`/gc/gomemlimit:bytes`) | If set: hard memory ceiling; used approaching it → aggressive GC then OOM | — | ✅ |
| `go_gc_heap_allocs_bytes_total` | Counter | bytes | Cumulative bytes allocated to heap (`/gc/heap/allocs:bytes`) | Rate = allocation pressure driving GC | — | ✅ |
| `go_gc_heap_goal_bytes` | Gauge | bytes | Target heap size at end of GC cycle (`/gc/heap/goal:bytes`) | Understand GC trigger threshold | — | ✅ |
| `go_memstats_gc_cpu_fraction` | Gauge | ratio | Fraction of CPU used by GC (legacy; may be 0 in newer Go) | If > 0.05 (5%) → GC stealing too much CPU | — | ✅ |

---

## 4. Memory — Classic MemStats

These come from the legacy `runtime.MemStats` collector (enabled by default in client_golang).

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_memstats_heap_alloc_bytes` | Gauge | bytes | Bytes of allocated heap objects (live + not yet freed) | Current heap usage; compare with container limit | — | ✅ |
| `go_memstats_heap_inuse_bytes` | Gauge | bytes | Bytes in in-use heap spans | Active heap memory (includes fragmentation within spans) | — | ✅ |
| `go_memstats_heap_idle_bytes` | Gauge | bytes | Bytes in idle (unused) heap spans | Memory held but not actively used; available for reuse | — | ✅ |
| `go_memstats_heap_released_bytes` | Gauge | bytes | Bytes of heap memory returned to OS | Low = runtime hoarding memory; high = healthy scavenging | — | ✅ |
| `go_memstats_heap_objects` | Gauge | objects | Count of allocated heap objects | Object count growth = potential leak (correlate with alloc_bytes) | — | ✅ |
| `go_memstats_heap_sys_bytes` | Gauge | bytes | Bytes of heap memory obtained from OS | Total virtual memory committed for heap | — | ✅ |
| `go_memstats_stack_inuse_bytes` | Gauge | bytes | Bytes used by goroutine stacks | Rising = goroutine leak (each goroutine has a stack) | — | ✅ |
| `go_memstats_next_gc_bytes` | Gauge | bytes | Target heap size for next GC cycle | When heap_alloc approaches this → GC triggers | — | ✅ |
| `go_memstats_alloc_bytes_total` | Counter | bytes | Cumulative bytes allocated (same as `go_gc_heap_allocs_bytes_total`) | Rate = allocation rate | — | ✅ |
| `go_memstats_mallocs_total` | Counter | objects | Cumulative count of heap object allocations | Allocation frequency | — | ✅ |
| `go_memstats_frees_total` | Counter | objects | Cumulative count of heap object frees | Rate should track mallocs; divergence = accumulation | — | ✅ |
| `go_memstats_sys_bytes` | Gauge | bytes | Total bytes of memory obtained from OS (all purposes) | Overall memory footprint of the Go runtime | — | ✅ |

---

## 5. Memory — runtime/metrics Classes

These come from the `WithGoCollectorRuntimeMetrics(MetricsMemory)` configuration.

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_memory_classes_total_bytes` | Gauge | bytes | All memory mapped by Go runtime as read-write (`/memory/classes/total:bytes`) | Total process memory owned by Go runtime | — | ✅ |
| `go_memory_classes_heap_objects_bytes` | Gauge | bytes | Memory occupied by live + dead (not yet freed) heap objects (`/memory/classes/heap/objects:bytes`) | Actual heap working set | — | ✅ |
| `go_memory_classes_heap_free_bytes` | Gauge | bytes | Free heap memory eligible to return to OS but not yet returned (`/memory/classes/heap/free:bytes`) | High = scavenger not keeping up or runtime retaining for reuse | — | ✅ |
| `go_memory_classes_heap_released_bytes` | Gauge | bytes | Heap memory returned to OS (`/memory/classes/heap/released:bytes`) | Gap between total and released = actual RSS commitment | — | ✅ |
| `go_memory_classes_heap_unused_bytes` | Gauge | bytes | Reserved for heap but not holding objects (`/memory/classes/heap/unused:bytes`) | Internal fragmentation within heap spans | — | ✅ |
| `go_memory_classes_metadata_mcache_inuse_bytes` | Gauge | bytes | Memory for runtime mcache structures in use | — | — | ✅ |
| `go_memory_classes_metadata_mcache_free_bytes` | Gauge | bytes | Reserved for mcache but not in use | — | — | ✅ |
| `go_memory_classes_metadata_mspan_inuse_bytes` | Gauge | bytes | Memory for runtime mspan structures in use | — | — | ✅ |
| `go_memory_classes_metadata_mspan_free_bytes` | Gauge | bytes | Reserved for mspan but not in use | — | — | ✅ |
| `go_memory_classes_metadata_other_bytes` | Gauge | bytes | Other runtime metadata memory | — | — | ✅ |
| `go_memory_classes_os_stacks_bytes` | Gauge | bytes | Stack memory allocated by the OS (non-zero mainly in cgo programs) (`/memory/classes/os-stacks:bytes`) | If non-zero: OS thread stacks from cgo | — | ✅ |

---

## 6. CPU Classes

These come from `WithGoCollectorRuntimeMetrics(GoRuntimeMetricsRule{Matcher: regexp.MustCompile("^/cpu/.*")})`.

> ⚠️ **These are OVERESTIMATES** — not directly comparable to system CPU (`container_cpu_usage_seconds_total`). Compare only with other `go_cpu_classes_*` values. Useful for GC/user ratio.

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_cpu_classes_total_cpu_seconds_total` | Counter | cpu-seconds | Total available CPU time (GOMAXPROCS × wall-time) (`/cpu/classes/total:cpu-seconds`) | Denominator for all CPU class ratios | — | ✅ |
| `go_cpu_classes_user_cpu_seconds_total` | Counter | cpu-seconds | CPU time in user Go code (`/cpu/classes/user:cpu-seconds`) | Productive CPU usage | — | ✅ |
| `go_cpu_classes_gc_total_cpu_seconds_total` | Counter | cpu-seconds | Total GC CPU time (sum of all GC sub-classes) (`/cpu/classes/gc/total:cpu-seconds`) | If gc/total approaches user → GC is dominating | — | ✅ |
| `go_cpu_classes_scavenge_total_cpu_seconds_total` | Counter | cpu-seconds | CPU time returning memory to OS (`/cpu/classes/scavenge/total:cpu-seconds`) | Usually low; spike = memory pressure triggering aggressive scavenge | — | ✅ |
| `go_cpu_classes_idle_cpu_seconds_total` | Counter | cpu-seconds | Available CPU time not used (`/cpu/classes/idle:cpu-seconds`) | If near zero → CPU saturated | — | ✅ |

---

## 7. Mutex Contention (KEY)

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_sync_mutex_wait_total_seconds_total` | Counter | seconds | Cumulative time goroutines spent blocked on sync.Mutex/RWMutex/runtime locks (`/sync/mutex/wait/total:seconds`) | **Rising rate = lock contention.** Most important single metric for concurrency bottlenecks | — | ✅ |

---

## 8. database/sql Pool (KEY for Services)

From `collectors.NewDBStatsCollector(db, dbName)`. Critical for any Go service using `database/sql`.

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_sql_open_connections` | Gauge | connections | Established connections (in-use + idle) | Should be ≤ max_open; if equal → pool exhausted | `db_name` | ✅ |
| `go_sql_in_use_connections` | Gauge | connections | Connections currently in use | High ratio in_use/open → near saturation | `db_name` | ✅ |
| `go_sql_idle_connections` | Gauge | connections | Idle connections in pool | Zero + high wait_count → pool too small | `db_name` | ✅ |
| `go_sql_max_open_connections` | Gauge | connections | Configured `SetMaxOpenConns` value | Compare with open_connections for headroom | `db_name` | ✅ |
| `go_sql_wait_count_total` | Counter | waits | Total connections waited for (pool exhaustion events) | **Rate > 0 = requests queuing for a connection** | `db_name` | ✅ |
| `go_sql_wait_duration_seconds_total` | Counter | seconds | Total time blocked waiting for a connection | Rate = average wait per second; high = DB pool bottleneck | `db_name` | ✅ |
| `go_sql_max_idle_closed_total` | Counter | connections | Connections closed due to SetMaxIdleConns | Frequent closing = MaxIdleConns too low | `db_name` | ✅ |
| `go_sql_max_idle_time_closed_total` | Counter | connections | Connections closed due to SetConnMaxIdleTime | Expected lifecycle behavior | `db_name` | ✅ |
| `go_sql_max_lifetime_closed_total` | Counter | connections | Connections closed due to SetConnMaxLifetime | Expected; prevents stale connections | `db_name` | ✅ |

---

## 9. CGO

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_cgo_go_to_c_calls_calls_total` | Counter | calls | Count of Go-to-C calls (`/cgo/go-to-c-calls:calls`) | High rate = FFI overhead; each call has scheduling cost | — | ✅ |

---

## 10. Build / Runtime Info

| Metric Name | Type | Unit | What It Measures | Troubleshooting Use | Key Labels | Confirmed |
|---|---|---|---|---|---|---|
| `go_build_info` | Gauge (const 1) | — | Build metadata | Identify Go version, module path, checksum | `path`, `version`, `checksum` | ✅ |
| `go_info` | Gauge (const 1) | — | Go runtime version | Cross-reference behavior changes between Go releases | `version` | ✅ |

---

## Cardinality Warnings

| Label | Risk | Context |
|---|---|---|
| `le` (on histogram buckets) | ✅ LOW | Bounded by bucket count (typically 10-15 per histogram) |
| `quantile` (on `go_gc_duration_seconds`) | ✅ LOW | Fixed set: 0, 0.25, 0.5, 0.75, 1 |
| `db_name` (on `go_sql_*`) | ⚠️ MEDIUM | One series set per DB connection pool — watch if service has many pools |
| `version` (on `go_info`, `go_build_info`) | ✅ LOW | One value per binary |

---

## How Metrics Interrelate

```
                    ┌───────────────────────────────────────┐
                    │  go_memstats_alloc_bytes_total (rate)  │
                    │  = allocation pressure                 │
                    └──────────────┬────────────────────────┘
                                   │ drives
                                   ▼
                    ┌───────────────────────────────────────┐
                    │  go_gc_duration_seconds_count (rate)   │
                    │  = GC frequency                        │
                    └──────────────┬────────────────────────┘
                                   │ each cycle has
                                   ▼
                    ┌───────────────────────────────────────┐
                    │  go_sched_pauses_total_gc_seconds p99  │
                    │  = STW pause latency                   │
                    └──────────────┬────────────────────────┘
                                   │ contributes to
                                   ▼
                    ┌───────────────────────────────────────┐
                    │  http_server_request_duration p99      │
                    │  = user-visible latency                │
                    └───────────────────────────────────────┘

  go_goroutines ──────────► go_sched_latencies_seconds p99
     (demand)                    (scheduler saturation)
                                       │
                                       ▼
  go_gomaxprocs ──────────► if goroutines >> GOMAXPROCS
     (capacity)              then sched_latencies rises


  go_sync_mutex_wait_total_seconds_total (rate) ──► go_sched_latencies_seconds ↑
     (lock contention blocks goroutines)              (inflates scheduling wait)


  go_sql_wait_count_total (rate) > 0 ──► http_server_request_duration ↑
     (DB pool exhaustion)                   (requests block on connection)
```

| Relationship | Explanation |
|---|---|
| `alloc_bytes_total` rate ↑ → `gc_duration_seconds_count` rate ↑ | More allocations trigger more GC cycles |
| `gc_duration_seconds_count` rate ↑ → `sched_pauses_total_gc` events ↑ | More cycles = more STW pauses |
| `sched_pauses_total_gc` p99 ↑ → HTTP p99 ↑ | Long GC pauses freeze all goroutines |
| `go_goroutines` >> `go_gomaxprocs` → `sched_latencies` ↑ | More runnable goroutines than CPUs = queuing |
| `go_cpu_classes_gc_total` / `go_cpu_classes_user` ratio ↑ → throughput ↓ | GC stealing CPU from user code |
| `go_memstats_heap_alloc_bytes` approaching `go_gc_gomemlimit_bytes` → OOM risk | Runtime GCs aggressively, then OOMKill |
| `go_sync_mutex_wait_total_seconds_total` rate ↑ → `sched_latencies` ↑ | Contention blocks goroutines in runnable state |
| `go_sql_wait_count_total` rate ↑ → request latency ↑ | Pool exhaustion = requests queuing for DB conn |
| `go_memstats_heap_objects` rising without `frees_total` tracking → leak | Objects accumulating = memory leak |

---

## Symptom → Metric Quick-Reference

| Symptom | First Metrics to Check | MetricsQL Query |
|---|---|---|
| **Periodic latency spikes** | `go_sched_pauses_total_gc_seconds` p99 | `histogram_quantile(0.99, rate(go_sched_pauses_total_gc_seconds_bucket{service="X"}[5m]))` |
| **Latency creeping up steadily** | `go_sched_latencies_seconds` p99 | `histogram_quantile(0.99, rate(go_sched_latencies_seconds_bucket{service="X"}[5m]))` |
| **Memory growing monotonically** | `go_memstats_heap_alloc_bytes`, allocation rate | `deriv(go_memstats_heap_alloc_bytes{service="X"}[30m]) > 0` (sustained) |
| **OOMKilled pods** | heap vs limit | `go_memstats_heap_alloc_bytes / go_gc_gomemlimit_bytes > 0.85` |
| **Goroutine leak** | `go_goroutines` (monotonic rise) | `deriv(go_goroutines{service="X"}[10m]) > 1` sustained for >30m |
| **High CPU, low throughput** | GC/user CPU ratio | `rate(go_cpu_classes_gc_total_cpu_seconds_total[5m]) / rate(go_cpu_classes_user_cpu_seconds_total[5m]) > 0.25` |
| **Scheduler starvation** | `go_sched_latencies_seconds` p99 > 10ms | `histogram_quantile(0.99, rate(go_sched_latencies_seconds_bucket[5m])) > 0.01` |
| **Lock contention** | `go_sync_mutex_wait_total_seconds_total` rate | `rate(go_sync_mutex_wait_total_seconds_total{service="X"}[5m]) > 0.1` |
| **DB pool exhaustion** | `go_sql_wait_count_total` rate, `go_sql_open_connections` | `rate(go_sql_wait_count_total{db_name="main"}[5m]) > 0` |
| **DB pool saturation** | in_use/max ratio | `go_sql_in_use_connections / go_sql_max_open_connections > 0.9` |
| **Excessive GC frequency** | GC cycles rate | `rate(go_gc_duration_seconds_count{service="X"}[5m]) > 10` (>10 GC/s) |
| **Slow STW stopping** | stopping subset of pause | `histogram_quantile(0.99, rate(go_sched_pauses_stopping_gc_seconds_bucket[5m])) > 0.001` |
| **Memory not returned to OS** | released vs total | `1 - (go_memstats_heap_released_bytes / go_memstats_heap_idle_bytes)` (close to 1 = hoarding) |
| **Thread explosion (cgo)** | `go_threads`, cgo call rate | `go_threads > 100` or `rate(go_cgo_go_to_c_calls_calls_total[5m]) > 1000` |

---

## Key Dashboards (Grafana Panel Suggestions)

### Essential 4 panels (minimal Go service dashboard)

1. **Heap usage**: `go_memstats_heap_alloc_bytes` vs `go_gc_gomemlimit_bytes` (or container limit)
2. **GC pause latency**: `histogram_quantile(0.99, rate(go_sched_pauses_total_gc_seconds_bucket[5m]))`
3. **Scheduler saturation**: `histogram_quantile(0.99, rate(go_sched_latencies_seconds_bucket[5m]))`
4. **Goroutine count**: `go_goroutines`

### Extended (add for services with DB):

5. **DB pool**: `go_sql_open_connections` / `go_sql_max_open_connections` stacked with `go_sql_in_use_connections`
6. **DB wait rate**: `rate(go_sql_wait_count_total[5m])`
7. **Mutex contention**: `rate(go_sync_mutex_wait_total_seconds_total[5m])`

---

## References

- [client_golang collectors package](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/collectors) — source of all `go_*` metrics
- [Go runtime/metrics package](https://pkg.go.dev/runtime/metrics) — authoritative list of runtime metrics with descriptions
- [client_golang RuntimeMetricsToProm](https://github.com/prometheus/client_golang/blob/main/prometheus/internal/go_runtime_metrics.go) — name conversion logic
- [client_golang NewDBStatsCollector](https://github.com/prometheus/client_golang/blob/main/prometheus/collectors/dbstats_collector.go) — `go_sql_*` metrics source
