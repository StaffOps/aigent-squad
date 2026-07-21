---
name: nexus3-metrics
description: "Use when diagnosing Sonatype Nexus Repository 3 health \u2014 JVM heap pressure, Jetty thread pool saturation, HTTP request latency, connection exhaustion, or GC overhead. Nexus exposes Dropwizard Metrics in Prometheus format at /service/metrics/prometheus. Covers jvm_*, org_eclipse_jetty_*, nexus_*, process_*. Grounded on Helm chart stevehipwell/nexus3 v5.5.1 (appVersion 3.75.1). Metrics ENABLED and SCRAPED via ServiceMonitor in the deployed config."
keywords: [nexus3, gc, nexus, prometheus, jvm]
---
# Sonatype Nexus Repository 3 — Prometheus Metrics

Nexus Repository 3 is a Java application (Eclipse Jetty + Karaf/OSGi) that exposes
internal metrics via the **Dropwizard Metrics** library, serialized to Prometheus
format at an HTTP endpoint.

**Question answered**: "Is Nexus healthy? Is it running out of heap, threads, or
connections?"

---

## Scrape Pipeline

```
Nexus 3 pod (:8081/service/metrics/prometheus)
  → ServiceMonitor (nexus namespace, auth via nx-metrics role on anonymous user)
    → vmagent scrape
      → VictoriaMetrics
```

### How metrics are enabled (deployed config)

| Setting | Value | Effect |
|---------|-------|--------|
| `metrics.enabled` | `true` | Exposes `/service/metrics/prometheus` endpoint |
| `metrics.serviceMonitor.enabled` | `true` | Creates a `ServiceMonitor` CR for vmagent auto-discovery |
| `config.anonymous.roles` | `[nx-anonymous, nx-metrics]` | Grants anonymous access to metrics endpoint (no auth needed for scrape) |

**Endpoint**: `/service/metrics/prometheus` (for Nexus < 3.81; deployed version is 3.75.1).

> ⚠️ **No official Sonatype documentation of individual metric names exists**
> (confirmed via [community thread](https://community.sonatype.com/t/documentation-for-exposed-prometheus-metrics/8960)
> and JIRA NEXUS-24090). The metrics below are from the **Dropwizard Metrics +
> Prometheus Servlet** standard set that Nexus bundles (JVM, Jetty, and
> Nexus-internal registrations). Names follow the Dropwizard → Prometheus naming
> convention (dots→underscores, type suffixes).

---

## JVM Metrics (Dropwizard JVM Collectors)

Standard JVM metrics exposed by Dropwizard's `MemoryUsageGaugeSet`, `GarbageCollectorMetricSet`,
`ThreadStatesGaugeSet`, and `BufferPoolMetricSet`.

### Memory

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_memory_bytes_used` | Gauge | Current JVM memory usage | High `heap` value approaching `-Xmx` = OOMKill risk | `area` (heap, nonheap) |
| `jvm_memory_bytes_max` | Gauge | Maximum memory available to JVM | Denominator for utilization calculation | `area` |
| `jvm_memory_bytes_committed` | Gauge | Memory committed by OS to JVM | Gap between committed and used = overhead | `area` |
| `jvm_memory_pool_bytes_used` | Gauge | Per-pool memory usage | Pinpoint which pool is growing (G1 Old Gen = leak signal) | `pool` (G1 Eden Space, G1 Old Gen, G1 Survivor Space, Metaspace, etc.) |
| `jvm_memory_pool_bytes_max` | Gauge | Per-pool maximum | Pool-level saturation | `pool` |
| `jvm_buffer_pool_used_bytes` | Gauge | Buffer pool (direct/mapped) usage | `direct` pool high = MaxDirectMemorySize pressure (set to 2560m in deployed config) | `pool` (direct, mapped) |
| `jvm_buffer_pool_capacity_bytes` | Gauge | Buffer pool capacity | Cap for direct memory buffers | `pool` |

### Garbage Collection

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_gc_collection_seconds_count` | Counter | Total GC invocations | High rate = memory pressure, excessive allocation | `gc` (G1 Young Generation, G1 Old Generation) |
| `jvm_gc_collection_seconds_sum` | Counter | Total time spent in GC | `sum/count` = avg pause; rising sum rate = stop-the-world impact | `gc` |

### Threads

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_threads_current` | Gauge | Current live thread count | Unexpected growth = thread leak or blocked threads | — |
| `jvm_threads_daemon` | Gauge | Daemon thread count | Nexus background tasks | — |
| `jvm_threads_peak` | Gauge | Peak thread count since JVM start | Indicates historical max thread pressure | — |
| `jvm_threads_deadlocked` | Gauge | Threads in deadlock | **Any >0 = critical** — application may hang | — |
| `jvm_threads_state` | Gauge | Thread count per state | `BLOCKED` high = lock contention; `WAITING` high = idle threads | `state` (RUNNABLE, BLOCKED, WAITING, TIMED_WAITING, NEW, TERMINATED) |

### Classes

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_classes_loaded` | Gauge | Currently loaded classes | Monotonic growth = classloader leak (plugins?) | — |
| `jvm_classes_loaded_total` | Counter | Total classes loaded since start | — | — |
| `jvm_classes_unloaded_total` | Counter | Total classes unloaded | — | — |

---

## Jetty HTTP Server Metrics

Nexus runs on Eclipse Jetty. Dropwizard's `InstrumentedHandler` exposes HTTP
request metrics under the `org_eclipse_jetty_*` or `org.eclipse.jetty.*` prefix
(dots converted to underscores in Prometheus format).

### Request RED

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `org_eclipse_jetty_server_handler_requests_total` | Counter | Total HTTP requests processed | Request rate baseline; drop = upstream problem or Nexus unresponsive | — |
| `org_eclipse_jetty_server_handler_responses_total` | Counter | Responses by HTTP status bucket | 5xx spike = server errors; 4xx spike = auth/client issues | `code` (1xx, 2xx, 3xx, 4xx, 5xx) |
| `org_eclipse_jetty_server_handler_requests_active` | Gauge | Currently in-flight requests | Sustained high = slow backends or thread starvation | — |
| `org_eclipse_jetty_server_handler_request_time_seconds` | Summary/Timer | Request processing time | High p99 = slow requests; correlate with GC pauses or DB queries | (quantile labels if available) |
| `org_eclipse_jetty_server_handler_dispatches_total` | Counter | Total dispatches (includes async) | — | — |

### Thread Pool

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `org_eclipse_jetty_util_thread_QueuedThreadPool_threads` | Gauge | Current Jetty thread pool size | — | — |
| `org_eclipse_jetty_util_thread_QueuedThreadPool_idle` | Gauge | Idle threads in pool | 0 idle = saturation; all requests queued | — |
| `org_eclipse_jetty_util_thread_QueuedThreadPool_jobs` | Gauge | Queued jobs waiting for a thread | Growing queue = thread pool exhaustion (max 400 threads default) | — |
| `org_eclipse_jetty_util_thread_QueuedThreadPool_size` | Gauge | Thread pool max size | Max threads available (default 400 since Nexus 3.13) | — |

### Connections

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `org_eclipse_jetty_io_ManagedSelector_connections` | Gauge | Active TCP connections | High count = many concurrent clients or slow connections | — |

---

## Process Metrics

Standard JVM process metrics (Prometheus client_java `ProcessCollector`-style, if bundled):

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `process_cpu_seconds_total` | Counter | Total CPU time consumed | CPU rate vs requests = efficiency | — |
| `process_open_fds` | Gauge | Open file descriptors | Approaching `process_max_fds` = FD exhaustion (breaks blob store ops) | — |
| `process_max_fds` | Gauge | Maximum file descriptors | Hard limit; compare with open_fds | — |
| `process_resident_memory_bytes` | Gauge | RSS memory | Should stay within pod limits (6Gi deployed) | — |
| `process_start_time_seconds` | Gauge | JVM start timestamp | Detect restarts without k8s events | — |

---

## Nexus-Internal Metrics (Limited/Undocumented)

Nexus registers some application-specific metrics into Dropwizard. These are
**NOT officially documented** by Sonatype and may vary between versions. Common
patterns observed in 3.x:

| Metric Pattern | Type | What It Likely Measures | Notes |
|---|---|---|---|
| `nexus_content_*` | Timer/Counter | Content upload/download operations | Repository format-specific |
| `nexus_quartz_*` | Gauge/Counter | Scheduled task execution (Quartz scheduler) | Active tasks, execution time |

> ⚠️ **Honesty note**: Sonatype has explicitly stated these metrics are
> undocumented (JIRA NEXUS-24090, open since 2022). The exact `nexus_*` metric
> names depend on which plugins/formats are loaded and are subject to change
> without notice. Do NOT build critical alerts on undocumented `nexus_*` metrics
> without empirical verification against the running instance.

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Nexus slow to respond | `org_eclipse_jetty_server_handler_request_time_seconds` (p99), `jvm_gc_collection_seconds_sum` rate, `QueuedThreadPool_idle` |
| OOMKilled pod | `jvm_memory_bytes_used{area="heap"}` vs `-Xmx` (5120m deployed), `jvm_memory_pool_bytes_used{pool="G1 Old Gen"}` |
| Thread exhaustion / 503 errors | `QueuedThreadPool_idle` = 0, `QueuedThreadPool_jobs` growing, `handler_requests_active` = max threads |
| Deadlock / hang | `jvm_threads_deadlocked` > 0 |
| Direct memory issues | `jvm_buffer_pool_used_bytes{pool="direct"}` approaching MaxDirectMemorySize (2560m deployed) |
| File descriptor exhaustion | `process_open_fds` approaching `process_max_fds` |
| High GC overhead | `rate(jvm_gc_collection_seconds_sum[5m])` / 300 > 0.1 (>10% time in GC) |
| Upload/download degradation | `org_eclipse_jetty_server_handler_responses_total{code="5xx"}` rate spike |
| Post-restart detection | `process_start_time_seconds` changed, or `jvm_gc_collection_seconds_count` reset to 0 |

### Key Thresholds (deployed config)

| Resource | Deployed Value | Alert-Worthy When |
|----------|---------------|-------------------|
| Heap (-Xmx) | 5120 MiB | `jvm_memory_bytes_used{area="heap"}` > 4.5 GiB sustained |
| MaxDirectMemorySize | 2560 MiB | `jvm_buffer_pool_used_bytes{pool="direct"}` > 2.3 GiB |
| Pod memory limit | 6144 MiB | RSS approaching limit → OOMKill |
| Jetty max threads | 400 (default) | `QueuedThreadPool_idle` = 0 for > 30s |

---

## Metric Discovery (Empirical Verification)

Since Nexus metric names are undocumented and may vary, the best approach to
discover the actual metrics exposed is:

```bash
# Port-forward to Nexus and dump all metric names
kubectl port-forward svc/nexus3 8081:8081 -n nexus
curl -s http://localhost:8081/service/metrics/prometheus | grep "^[a-z]" | awk '{print $1}' | sed 's/{.*//' | sort -u
```

Or via VictoriaMetrics (if already scraped):
```promql
# Find all metric names from the nexus job
count({job="nexus3"}) by (__name__)
```

---

## Complements

- **k8s-workload-metrics** — pod-level resource metrics (CPU/memory requests vs actual usage via cAdvisor/kubelet)
- **go-apm-metrics** — NOT applicable (Nexus is Java, not Go)
- **backing-services-metrics** — if Nexus uses external PostgreSQL (deployed uses internal H2/datastore)
- **cert-manager-metrics** — TLS certificate health for the ingress

---

## Sources

- Helm chart: `stevehipwell/nexus3` v5.5.1 (appVersion: Sonatype Nexus Repository **3.75.1**)
- Deployed values: `02-KUBE/00-CONFIG/k8s-setup/nexus3/nexus3/values.yaml.gotmpl`
- Official docs: https://help.sonatype.com/en/prometheus.html (confirms endpoint + auth, no metric list)
- Sonatype community: https://community.sonatype.com/t/documentation-for-exposed-prometheus-metrics/8960 (confirms no official metric docs)
- Dropwizard Metrics: https://metrics.dropwizard.io/ (metric naming conventions)
- Grafana community dashboard: https://grafana.com/grafana/dashboards/16459-infra-nexus/ (empirical metric usage)
- stevehipwell/helm-charts release: https://github.com/stevehipwell/helm-charts/releases/tag/nexus3-5.5.1
