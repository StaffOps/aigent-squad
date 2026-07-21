---
name: dependency-track-metrics
description: "Use when diagnosing OWASP Dependency-Track API server health — ORM persistence operations, HikariCP connection pool saturation, event/notification system throughput, executor thread pool pressure, Lucene search index state, and resilience4j retry behavior. Covers datanucleus_*, hikaricp_*, alpine_*, executor_*, search_index_*, resilience4j_*, plus standard JVM metrics (jvm_*, process_*). Grounded on Helm chart DependencyTrack/dependency-track 0.44.0 (appVersion ~4.12.x), official docs v4.14 monitoring reference."
keywords: [dependency-track, dependency, track, "dependency track", owasp, orm, jvm]
---
# Dependency-Track API Server Metrics

**Grounded on**: Helm chart `dependency-track/dependency-track` v0.44.0 (appVersion ~4.12.x).
Official docs: https://docs.dependencytrack.org/getting-started/monitoring/

---

## Scrape Pipeline & Enablement

```
DT API Server (:8080/metrics) → ServiceMonitor → vmagent → VictoriaMetrics
```

**How metrics are enabled**:

1. **Application-level**: The chart sets `apiServer.metrics.enabled: true` by default, which
   configures `alpine.metrics.enabled=true` inside the container. This exposes the `/metrics`
   endpoint in Prometheus text format on the API server's HTTP port (8080).
2. **Scrape-level**: The deployed config sets `apiServer.serviceMonitor.enabled: true` +
   `namespace: dependency-track`, creating a ServiceMonitor that vmagent picks up.

**Status in deployed config**: ✅ **Both enabled.** Metrics are being scraped.

Optional auth (`alpine.metrics.auth.username`/`password`) is NOT set in the deployed values
(no extra env vars for it), so the `/metrics` endpoint is unauthenticated.

> **Note**: These are system/JVM metrics for monitoring the application itself.
> Portfolio metrics (vulnerability counts per project) are exposed via the REST API
> (`/api/v1/metrics`) and require a separate exporter (e.g., Jetstack's
> `dependency-track-exporter`) — NOT covered here.

---

## 1. Database / ORM — DataNucleus Metrics

Dependency-Track uses DataNucleus JDO as ORM. These metrics expose persistence layer health.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `datanucleus_transactions_total` | Counter | Total transactions executed | Baseline transaction throughput | — |
| `datanucleus_transactions_committed_total` | Counter | Committed transactions | Should equal `transactions_total` in healthy state | — |
| `datanucleus_transactions_rolledback_total` | Counter | Rolled-back transactions | Non-zero rate = data consistency issues or deadlocks | — |
| `datanucleus_transactions_active_total` | Counter | Currently active transactions | Sustained high = long-running transactions blocking pool | — |
| `datanucleus_queries_executed_total` | Counter | Total executed queries | Query throughput baseline | — |
| `datanucleus_queries_failed_total` | Counter | Queries that completed with error | Non-zero rate = broken queries, schema issues | — |
| `datanucleus_queries_active` | Gauge | Currently active queries | Sustained high = slow queries blocking resources | — |
| `datanucleus_query_execution_time_ms_avg` | Gauge | Average query execution time (ms) | Rising = DB performance degradation | — |
| `datanucleus_transaction_execution_time_ms_avg` | Gauge | Average transaction execution time (ms) | Rising = complex transactions or lock contention | — |
| `datanucleus_datastore_reads_total` | Counter | Read operations from datastore | I/O read pressure | — |
| `datanucleus_datastore_writes_total` | Counter | Write operations to datastore | I/O write pressure | — |
| `datanucleus_object_fetches_total` | Counter | Objects fetched from datastore | Object-level read throughput | — |
| `datanucleus_object_inserts_total` | Counter | Objects inserted into datastore | SBOM ingestion rate signal | — |
| `datanucleus_object_updates_total` | Counter | Objects updated in datastore | Vulnerability analysis activity | — |
| `datanucleus_object_deletes_total` | Counter | Objects deleted from datastore | Purge/cleanup activity | — |
| `datanucleus_connections_active` | Gauge | Active managed datastore connections | Connection pool saturation at ORM level | — |

---

## 2. Connection Pool — HikariCP Metrics

Two pools exist: `transactional` and `non-transactional`. Monitor BOTH.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `hikaricp_connections` | Gauge | Total connections in pool | Pool size; should be between min and max | `pool` |
| `hikaricp_connections_active` | Gauge | Active (in-use) connections | Sustained = max → pool exhaustion | `pool` |
| `hikaricp_connections_pending` | Gauge | **Threads waiting for connection** | **>0 sustained = connection starvation** — critical signal | `pool` |
| `hikaricp_connections_min` | Gauge | Configured minimum connections | Reference for pool sizing | `pool` |
| `hikaricp_connections_max` | Gauge | Configured maximum connections | Reference for pool sizing | `pool` |
| `hikaricp_connections_usage_seconds_count` | Counter | Count of connection usage samples | Throughput baseline | `pool` |
| `hikaricp_connections_usage_seconds_sum` | Counter | Cumulative connection hold time | Avg hold time = sum/count; rising = slow queries | `pool` |
| `hikaricp_connections_usage_seconds_max` | Gauge | Max connection hold time (recent window) | Spike = long-running transaction or query | `pool` |
| `hikaricp_connections_acquire_seconds_count` | Counter | Count of connection acquire samples | — | `pool` |
| `hikaricp_connections_acquire_seconds_sum` | Counter | Cumulative acquire wait time | Avg wait = sum/count; >10ms = pool pressure | `pool` |
| `hikaricp_connections_acquire_seconds_max` | Gauge | Max acquire time (recent window) | Spike with pending > 0 = pool exhaustion | `pool` |

**Pool values**: `pool="non-transactional"` (read-heavy, default max=20) and `pool="transactional"` (write, default max=20).

---

## 3. Event & Notification System — Alpine Metrics

Core processing pipeline metrics for SBOM analysis, vulnerability correlation, etc.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `alpine_events_published_total` | Counter | Total events published to internal bus | Event throughput; drop = ingestion stopped | `event`, `publisher` |
| `alpine_notifications_published_total` | Counter | Total notifications emitted | Notification activity; tracks ALL notifications (not just configured alerts) | `group`, `level`, `scope` |
| `alpine_event_processing_seconds_count` | Counter | Event processing observations | Processing throughput | `event`, `subscriber` |
| `alpine_event_processing_seconds_sum` | Counter | Cumulative event processing time | Avg processing time = sum/count | `event`, `subscriber` |
| `alpine_event_processing_seconds_max` | Gauge | Max event processing time (recent window) | Spike = single slow event blocking pipeline | `event`, `subscriber` |

**Key event classes** (appear in `event` label): `BomUploadEvent`, `VulnerabilityAnalysisEvent`, `RepositoryMetaEvent`, `PolicyEvaluationEvent`, `NistMirrorEvent`.

---

## 4. Executor / Thread Pool Metrics

Dependency-Track uses named executors for different workloads.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `executor_pool_max_threads` | Gauge | Max threads allowed in pool | Capacity ceiling | `name` |
| `executor_pool_core_threads` | Gauge | Core (base) threads in pool | Baseline capacity | `name` |
| `executor_pool_size_threads` | Gauge | Current threads active in pool | Utilization = size/max | `name` |
| `executor_active_threads` | Gauge | Threads currently executing tasks | **Maxed out = pool saturated** | `name` |
| `executor_completed_tasks_total` | Counter | Total completed tasks | Work throughput | `name` |
| `executor_queued_tasks` | Gauge | **Tasks waiting in queue** | **>0 sustained with active=max → processing backlog** | `name` |
| `executor_queue_remaining_tasks` | Gauge | Queue capacity remaining | Approaching 0 = queue will reject tasks | `name` |

**Key executor names** (`name` label values):
- `Alpine-EventService` — main worker pool (default max=40, configured via `alpine.worker.pool.size`)
- `Alpine-SingleThreadedEventService` — serialized events (max=1)
- `Alpine-NotificationService` — notification dispatch (max=4)
- `SnykAnalysisTask` — parallel Snyk API calls (max=10, if Snyk enabled)

---

## 5. Search Index (Lucene) Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `search_index_ram_used_bytes` | Gauge | Memory used by search index | High = index pressure on heap | `index` |
| `search_index_docs_ram_total_objects` | Gauge | Documents buffered in RAM (not yet flushed) | Growing = flush backlog | `index` |
| `search_index_docs_total_objects` | Gauge | Total docs in index (including pending) | Index size baseline | `index` |
| `search_index_operations_total` | Counter | Index operations (add/commit/delete) | Indexing throughput | `index`, `operation` |

**Index names** (in `index` label): `COMPONENT`, `LICENSE`, `PROJECT`, `SERVICE_COMPONENT`, `VULNERABILITY`, `VULNERABLESOFTWARE`.

---

## 6. Retry Resilience (Resilience4j)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `resilience4j_retry_calls_total` | Counter | Total retry call outcomes | Retry pressure on external services | `kind`, `name` |

**`kind` label values**: `successful_without_retry`, `successful_with_retry`, `failed_without_retry`, `failed_with_retry`.
**`name` label values**: `snyk-api` (when Snyk datasource enabled), others for NVD/OSV/GitHub.

High `failed_with_retry` rate = external datasource degraded/unreachable.

---

## 7. JVM & Process Metrics (Micrometer)

Standard Micrometer JVM metrics are exposed. Use the generic JVM dashboard patterns.

| Metric Prefix | What It Covers |
|---|---|
| `jvm_memory_*` | Heap/non-heap memory usage |
| `jvm_gc_*` | Garbage collection pauses and counts |
| `jvm_threads_*` | Thread states (runnable, blocked, waiting) |
| `jvm_buffer_*` | Direct/mapped buffer pool |
| `jvm_classes_*` | Loaded/unloaded classes |
| `process_cpu_*` | Process CPU usage |
| `process_uptime_seconds` | Uptime |
| `system_cpu_*` | System-wide CPU |

> These are standard Micrometer/Prometheus JVM metrics — not documented in detail here.
> Use any Micrometer JVM Grafana dashboard (e.g., ID 4701).

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| API slow / timeouts | `hikaricp_connections_pending`, `hikaricp_connections_usage_seconds_max`, `datanucleus_query_execution_time_ms_avg` |
| SBOM upload hangs | `executor_queued_tasks{name="Alpine-EventService"}`, `executor_active_threads{name="Alpine-EventService"}` |
| Vulnerability analysis stalled | `alpine_events_published_total{event=~".*VulnerabilityAnalysis.*"}` rate, `executor_queued_tasks{name="Alpine-SingleThreadedEventService"}` |
| DB connection exhaustion | `hikaricp_connections_active` = `hikaricp_connections_max`, `hikaricp_connections_pending > 0` |
| NVD/Snyk mirror failures | `resilience4j_retry_calls_total{kind="failed_with_retry"}` |
| OOM / heap pressure | `jvm_memory_used_bytes{area="heap"}` / `jvm_memory_max_bytes{area="heap"}`, `search_index_ram_used_bytes` |
| Notification delivery issues | `alpine_notifications_published_total` rate by `group`/`level` |
| Transaction rollbacks | `datanucleus_transactions_rolledback_total` rate |

### Key Alert Thresholds

```promql
# Connection pool exhaustion (pending threads)
hikaricp_connections_pending{pool="non-transactional"} > 0  # for > 1m

# Event service saturated (queue growing)
executor_queued_tasks{name="Alpine-EventService"} > 100

# External datasource failing after retries
rate(resilience4j_retry_calls_total{kind="failed_with_retry"}[5m]) > 0

# Long query execution average
datanucleus_query_execution_time_ms_avg > 500
```

---

## Complements

- `skills/security/dependency-track-integration` — REST API integration, project structure, SBOM upload, policy configuration
- `k8s-workload-metrics` — pod CPU/memory, restarts (container-level, not DT-specific)
- `backing-services-metrics` — PostgreSQL metrics for the backing database

---

## Sources

- [Dependency-Track Official Monitoring Docs (v4.14)](https://docs.dependencytrack.org/getting-started/monitoring/)
- [DependencyTrack/helm-charts — Chart v0.44.0](https://github.com/DependencyTrack/helm-charts/releases/tag/dependency-track-0.44.0)
- Deployed config: `k8s-setup/dependency-track/dependency-track/values.yaml.gotmpl` (serviceMonitor enabled, devops-core cluster)
- Metric names sourced from official docs example output (DataNucleus, HikariCP, Alpine, Executor, Search Index, Resilience4j namespaces)
