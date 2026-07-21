---
name: keycloak-metrics
description: "Use when diagnosing Keycloak identity provider health — authentication latency, login failures, JVM heap/GC pressure, database connection pool exhaustion, HTTP request throughput. Covers native Keycloak 26 Quarkus/Micrometer metrics (http_server_requests_seconds_*, keycloak_user_events_total, agroal_*, jvm_*, base_*) and legacy keycloak-metrics-spi names (keycloak_failed_login_attempts, keycloak_logins_total, keycloak_request_duration_*). CRITICAL: metrics are currently DISABLED in the deployed config (chart 24.3.2). Alert rules reference keycloak-metrics-spi names."
keywords: [keycloak, alert, gc, jvm]
---
# Keycloak Metrics — Environment-Anchored Reference

**Grounded on**: Bitnami Keycloak Helm chart **24.3.2** (appVersion **Keycloak 26.0.7**,
Quarkus-based) deployed to `core-devops` cluster, namespace `keycloak`.

## ⚠️ CRITICAL: Metrics are currently DISABLED in the deployed config

The deployed `values.yaml.gotmpl` has the metrics section **commented out**:

```yaml
#!TODO: Enable metrics
# metrics:
#   enabled: true
#   serviceMonitor:
#     enabled: true
```

**Consequence**: The Keycloak pods do NOT expose `/metrics` on the management port
(:9000). No Prometheus/vmagent scrape is collecting Keycloak metrics. The
`prometheus.rules.yaml` file exists with alerting rules, but those rules will
**never fire** because the underlying metrics are not being scraped.

**To enable**: Set `metrics.enabled: true` in Helm values. Keycloak 26 (Quarkus)
then exposes Micrometer metrics on `:9000/metrics` (management interface). The
Bitnami chart creates a Service port + ServiceMonitor when enabled.

---

## Scrape Pipeline (when enabled)

```
Keycloak Pod (:9000/metrics, Micrometer/OpenMetrics)
  → vmagent (ServiceMonitor scrape)
    → VictoriaMetrics (vminsert)
```

**Port**: 9000 (management interface, separate from application port 8080/8443).
**Path**: `/metrics`
**Format**: `application/openmetrics-text` (Prometheus-compatible).
**Enablement**: `--metrics-enabled=true` build-time option (set via Helm `metrics.enabled`).

---

## Metric Families Available (Keycloak 26 Native — Quarkus/Micrometer)

When `metrics-enabled=true`, Keycloak 26 exposes the following metric families natively
(confirmed from official Keycloak 26.x observability docs):

### 1. HTTP Server Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `http_server_requests_seconds_count` | Counter | Total HTTP requests processed | Request throughput; drop = upstream issue or pod down | `method`, `status`, `uri`, `outcome` |
| `http_server_requests_seconds_sum` | Counter | Total duration of all requests (seconds) | Average latency = sum/count; rising = performance degradation | `method`, `status`, `uri`, `outcome` |
| `http_server_requests_seconds_bucket` | Histogram | Latency distribution (only when `http-metrics-histograms-enabled=true`) | p95/p99 latency analysis, heatmaps | `method`, `status`, `uri`, `outcome`, `le` |
| `http_server_active_requests` | Gauge | Current in-flight requests | Saturation signal; sustained high = thread pool exhaustion | — |
| `http_server_bytes_written_sum` | Counter | Total response bytes sent | Bandwidth monitoring, egress cost | — |
| `http_server_bytes_written_count` | Counter | Total responses sent | Same as requests_count (response perspective) | — |
| `http_server_bytes_read_sum` | Counter | Total request bytes received | Ingress bandwidth | — |
| `http_server_bytes_read_count` | Counter | Total requests received | Request counting (ingestion perspective) | — |

### 2. Database Connection Pool (Agroal)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `agroal_active_count` | Gauge | DB connections in use (in transactions) | High = DB pressure; equals pool size = exhausted | `datasource="default"` |
| `agroal_available_count` | Gauge | Idle DB connections in pool | Zero = pool fully utilized, threads may be waiting | `datasource="default"` |
| `agroal_awaiting_count` | Gauge | Threads waiting for a DB connection | **KEY saturation signal** — non-zero = contention, latency spike | `datasource="default"` |

### 3. Keycloak Self-Provided Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `keycloak_user_events_total` | Counter | User events (login, logout, register, errors) | Failed login spikes = brute force; login rate = usage baseline | `realm`, `event`, `error`, `client_id`⁰, `idp`⁰ |
| `keycloak_credentials_password_hashing_validations_total` | Counter | Password hash validation attempts | High error rate = misconfigured hashing; high count = auth load | `realm`, `algorithm`, `hashing_strength`, `outcome` |

⁰ `client_id` and `idp` tags disabled by default (cardinality). Enable via `spi-events-listener-metrics-listener-events-user-enabled-tags`.

> **Note**: `keycloak_user_events_total` requires explicit enablement via
> `--spi-events-listener-metrics-listener-events-user-enabled=true` (Keycloak 26+).

### 4. JVM Metrics (Micrometer/Quarkus)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_memory_usage_after_gc_percent` | Gauge | Long-lived heap usage after GC (0–1) | Memory leak signal; >0.8 = OOM risk | `area`, `pool` |
| `jvm_threads_peak_threads` | Gauge | Peak thread count since JVM start | Thread pool exhaustion baseline | — |
| `base_gc_total` | Counter | Total GC collections | GC frequency; high rate = memory pressure | `name` (e.g., "G1 Young Generation") |
| `base_memory_maxHeap_bytes` | Gauge | Maximum heap size | Capacity ceiling | — |
| `system_load_average_1m` | Gauge | System 1-min load average | CPU saturation | — |
| `process_start_time_seconds` | Gauge | JVM start time (epoch) | Detect restarts | — |

### 5. Embedded Cache Metrics (Infinispan, if `cache-metrics-histograms-enabled=true`)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `vendor_cache_manager_default_cache_*_statistics_hits` | Gauge | Cache hits | Low hit ratio = cache too small, excessive DB load | `cache` |
| `vendor_cache_manager_default_cache_*_statistics_misses` | Gauge | Cache misses | Rising misses = cardinality growth or eviction pressure | `cache` |
| `vendor_cache_manager_default_cache_*_statistics_stores` | Gauge | Cache stores (writes) | Write amplification signal | `cache` |

---

## Legacy keycloak-metrics-spi Metrics (referenced in prometheus.rules.yaml)

The deployed `prometheus.rules.yaml` references metrics from the **aerogear/keycloak-metrics-spi**
community plugin (NOT native Keycloak 26 metrics). These have different names:

| Metric Name | Type | What It Measures | Notes |
|---|---|---|---|
| `keycloak_failed_login_attempts` | Counter | Failed login attempts per realm/provider | **SPI plugin metric** — not native KC 26. Requires keycloak-metrics-spi JAR deployed. |
| `keycloak_logins_total` | Counter | Successful logins per realm/provider/client | SPI plugin metric |
| `keycloak_registrations_total` | Counter | User registrations per realm/provider | SPI plugin metric |
| `keycloak_request_duration_bucket` | Histogram | Request latency distribution | SPI plugin metric (different from native `http_server_requests_seconds_*`) |
| `keycloak_request_duration_count` | Counter | Request count | SPI plugin metric |
| `jvm_memory_bytes_used` | Gauge | JVM memory used (by area) | Legacy SmallRye/Wildfly name; native KC 26 uses `jvm_memory_usage_after_gc_percent` |
| `jvm_memory_bytes_max` | Gauge | JVM memory max (by area) | Legacy name |
| `jvm_gc_collection_seconds_sum` | Counter | GC collection time | Legacy SmallRye name; native KC 26 uses `base_gc_total` |
| `jvm_threads_deadlocked` | Gauge | Deadlocked threads | Legacy name |

**Current state**: Since metrics are disabled AND the keycloak-metrics-spi JAR deployment
status is unknown, these rules are likely **non-functional** (no metrics to evaluate against).

---

## Deployed Alert Rules (prometheus.rules.yaml — currently non-functional)

| Alert | Expression | Severity | Issue Detected |
|---|---|---|---|
| `KeycloakJavaHeapThresholdExceeded` | `jvm_memory_bytes_used{area="heap"} / jvm_memory_bytes_max > 90%` | warning | OOM risk |
| `KeycloakJavaNonHeapThresholdExceeded` | `jvm_memory_bytes_used{area="nonheap"} / jvm_memory_bytes_max > 90%` | warning | Metaspace/codecache pressure |
| `KeycloakJavaGCTimePerMinuteScavenge` | `increase(jvm_gc_collection_seconds_sum{gc="PS Scavenge"}[1m]) > 54s` | warning | >90% time in young gen GC |
| `KeycloakJavaGCTimePerMinuteMarkSweep` | `increase(jvm_gc_collection_seconds_sum{gc="PS MarkSweep"}[1m]) > 54s` | warning | >90% time in full GC |
| `KeycloakJavaDeadlockedThreads` | `jvm_threads_deadlocked > 0` | warning | Thread deadlock |
| `KeycloakLoginFailedThresholdExceeded` | `rate(keycloak_failed_login_attempts[5m]) * 300 > 50` | warning | Brute force / credential stuffing |
| `KeycloakInstanceNotAvailable` | Pod readiness check | warning | Instance down |
| `KeycloakAPIRequestDuration90PercThresholdExceeded` | `keycloak_request_duration_bucket{le="1000.0"}` ratio < 90% | warning | Latency SLO breach (1s p90) |
| `KeycloakAPIRequestDuration99PercThresholdExceeded` | `keycloak_request_duration_bucket{le="10000.0"}` ratio < 99.5% | warning | Latency SLO breach (10s p99.5) |

> **Action needed**: Enable `metrics.enabled: true` in Helm values AND either:
> (a) install keycloak-metrics-spi for the legacy metric names in the rules, OR
> (b) rewrite the rules to use native Keycloak 26 metric names.

---

## Troubleshooting Quick-Reference

| Symptom | First Metrics to Check | What to Look For |
|---------|------------------------|------------------|
| Slow logins | `http_server_requests_seconds_sum/count` (uri=`/realms/*/protocol/openid-connect/token`) | Rising average latency |
| Login failures spike | `keycloak_user_events_total{event="login",error!=""}` | Brute force, credential stuffing |
| OOMKilled pods | `jvm_memory_usage_after_gc_percent`, `base_memory_maxHeap_bytes` | After-GC usage >0.8 = leak |
| DB connection exhaustion | `agroal_awaiting_count` | Any non-zero = threads blocked waiting for DB |
| Thread deadlock | `jvm_threads_deadlocked` (legacy) or JVM thread dump | >0 = immediate attention |
| Cache misses | `vendor_cache_manager_default_cache_*_statistics_misses` | Rising misses + rising DB load = cache too small |
| High GC pause | `base_gc_total` rate + per-collection type timing | G1 Old Gen collections = heap pressure |

### Enabling Metrics (Bitnami Chart)

```yaml
# values.yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
    namespace: monitoring
    labels:
      release: prometheus  # match your vmagent/prometheus ServiceMonitorSelector
```

This sets `KC_METRICS_ENABLED=true` at build time and creates a ServiceMonitor
targeting port 9000.

---

## Metric Name Discrepancy: Legacy SPI vs Native KC 26

| Concept | keycloak-metrics-spi (legacy) | Native Keycloak 26 (Quarkus/Micrometer) |
|---------|-------------------------------|------------------------------------------|
| Failed logins | `keycloak_failed_login_attempts{realm,provider}` | `keycloak_user_events_total{event="login",error!=""}` |
| Successful logins | `keycloak_logins_total{realm,provider,client_id}` | `keycloak_user_events_total{event="login",error=""}` |
| Request latency | `keycloak_request_duration_seconds_*` | `http_server_requests_seconds_*` |
| JVM heap used | `jvm_memory_bytes_used{area="heap"}` | `jvm_memory_usage_after_gc_percent{area="heap"}` |
| GC time | `jvm_gc_collection_seconds_sum{gc="PS Scavenge"}` | `base_gc_total{name="G1 Young Generation"}` |
| Threads deadlocked | `jvm_threads_deadlocked` | `jvm_threads_peak_threads` (no direct deadlock metric natively) |

When migrating rules: the GC collector names also changed — Keycloak 26 uses **G1**
by default (not PS Scavenge/MarkSweep). Rules referencing `gc="PS Scavenge"` will
never match on KC 26 without explicit GC algorithm override.

---

## Complements

- `k8s-workload-metrics` — pod-level CPU/memory/restarts for Keycloak pods (always available regardless of metrics enablement)
- `backing-services-metrics` — PostgreSQL metrics for the external RDS backing Keycloak
- `cert-manager-metrics` — TLS certificate health for the Keycloak ingress certs
- `ingress-nginx-metrics` / `traefik-metrics` — ingress layer latency (Keycloak uses traefik-internal)

## Sources

- [Keycloak 26 Observability — Gaining Insights with Metrics](https://www.keycloak.org/observability/configuration-metrics) (official)
- [Keycloak 26 — Troubleshooting using metrics](https://www.keycloak.org/observability/metrics-for-troubleshooting) (official)
- [Keycloak 26 — HTTP metrics](https://www.keycloak.org/observability/metrics-for-troubleshooting-http) (official)
- [Keycloak 26 — Database metrics](https://www.keycloak.org/observability/metrics-for-troubleshooting-database) (official)
- [Keycloak 26 — Self-provided metrics](https://www.keycloak.org/observability/metrics-for-troubleshooting-keycloak) (official)
- [aerogear/keycloak-metrics-spi](https://github.com/aerogear/keycloak-metrics-spi) (community plugin, legacy metric names)
- Deployed config: `k8s-setup/keycloak/keycloak/values.yaml.gotmpl` (metrics commented out)
- Deployed rules: `k8s-setup/keycloak/keycloak/prometheus.rules.yaml` (uses legacy SPI names)
- Bitnami Keycloak chart 24.3.2 → Keycloak appVersion ~26.0.7 (from [bitnami/charts issue #31039](https://github.com/bitnami/charts/issues/31039))
