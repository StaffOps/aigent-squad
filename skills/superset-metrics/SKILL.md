---
name: superset-metrics
description: "Use when assessing Apache Superset observability. The deployed chart (superset/superset 0.14.0) does NOT expose first-class Prometheus metrics \u2014 no statsd-exporter sidecar, no Flask /metrics endpoint, no ServiceMonitor/PodMonitor. For workload health use k8s-workload-metrics; for backing Postgres/Redis use backing-services-metrics; for Python runtime (if OTel-instrumented) use python-apm-metrics."
keywords: [superset, postgres, prometheus, python, redis]
---
# Apache Superset — Prometheus Metrics Status

## Deployed Version

| Field | Value |
|-------|-------|
| Helm chart | `superset/superset` (from `http://apache.github.io/superset/`) |
| Chart version | **0.14.0** |
| Namespace | `superset` |
| Cluster | `devops-core` |

---

## CRITICAL: No First-Class Prometheus Metrics in This Deployment

The deployed Superset chart **does NOT expose Prometheus metrics**. Specifically:

- ❌ **No `statsd-exporter` sidecar** — the chart supports configuring `STATS_LOGGER = StatsdStatsLogger(...)` in `superset_config.py` to emit StatsD metrics, but this requires a `statsd-exporter` sidecar container to translate StatsD → Prometheus format. **Not deployed.**
- ❌ **No Flask `/metrics` endpoint** — Superset does not natively expose a Prometheus-compatible `/metrics` HTTP endpoint (unlike e.g. Airflow with `prometheus_client`).
- ❌ **No ServiceMonitor / PodMonitor** — the deployed values contain no Prometheus scrape annotations or monitoring CRDs.
- ❌ **No OTel SDK instrumentation** — the bootstrap script installs DB drivers and OAuth libs only; no `opentelemetry-*` packages or `setup_telemetry()` call.
- ❌ **No gunicorn Prometheus exporter** — the chart uses the default gunicorn setup without `--statsd-host` or `prometheus_multiproc_dir`.

### What Superset *Could* Expose (Not Currently Enabled)

Superset's `STATS_LOGGER` (if configured with a statsd-exporter sidecar) would emit metrics like:

| Metric Pattern (StatsD → Prometheus) | What It Would Measure |
|--------------------------------------|-----------------------|
| `superset_sql_lab_*` | SQL Lab query execution counts/duration |
| `superset_explore_*` | Explore query execution |
| `superset_cache_*` | Cache hit/miss rates |
| `superset_dashboard_*` | Dashboard load events |
| `superset_error_*` | Application error counts |

**These do NOT exist in VictoriaMetrics.** They are listed only to document what enabling `STATS_LOGGER` + statsd-exporter would provide.

---

## How to Monitor Superset Today

Given the lack of application-level metrics, use the following existing skills:

### 1. Pod/Container Resource Health → `k8s-workload-metrics`

Standard Kubernetes metrics from kubelet/cAdvisor:

| Metric | Use |
|--------|-----|
| `container_memory_working_set_bytes{namespace="superset"}` | Memory pressure / OOMKill risk |
| `container_cpu_usage_seconds_total{namespace="superset"}` | CPU saturation |
| `kube_pod_container_status_restarts_total{namespace="superset"}` | Crash loop detection |
| `kube_pod_status_phase{namespace="superset"}` | Pod lifecycle |

### 2. Backing PostgreSQL Health → `backing-services-metrics`

Superset connects to `eks-postgres.bdc.internal` (database: `superset`):

| Metric | Use |
|--------|-----|
| `pg_stat_activity_count{datname="superset"}` | Connection count (exhaustion risk) |
| `pg_stat_database_tup_fetched{datname="superset"}` | Query throughput |
| `pg_stat_database_deadlocks{datname="superset"}` | Lock contention |

### 3. Backing Redis Health → `backing-services-metrics`

Superset uses an in-cluster Redis (Bitnami, `superset-redis-headless:6379`) for caching (db=1) and Celery broker (db=0):

| Metric | Use |
|--------|-----|
| `redis_connected_clients` | Client pressure |
| `redis_memory_used_bytes` | Memory saturation |
| `redis_commands_processed_total` | Throughput baseline |
| `redis_rejected_connections_total` | Connection exhaustion |

### 4. Python Runtime (if OTel instrumented in future) → `python-apm-metrics`

If OTel SDK is added later, `python-apm-metrics` covers the standard Flask/Python runtime metrics emitted by OTel instrumentations.

---

## Enabling Metrics (Future Work)

To enable Prometheus metrics for this Superset deployment, two approaches:

### Option A: StatsD Exporter Sidecar (Recommended for Superset)

1. Add `statsd-exporter` sidecar container to the Superset deployment
2. Configure `superset_config.py`:
   ```python
   from superset.stats_logger import StatsdStatsLogger
   STATS_LOGGER = StatsdStatsLogger(host='localhost', port=8125, prefix='superset')
   ```
3. Add ServiceMonitor targeting the statsd-exporter port (typically 9102)

### Option B: OTel Instrumentation

1. Add `opentelemetry-instrumentation-flask`, `opentelemetry-instrumentation-sqlalchemy` to bootstrap
2. Configure `setup_telemetry()` or manual OTel SDK init
3. Metrics flow via OTel Collector → VictoriaMetrics

---

## Troubleshooting Without Application Metrics

| Symptom | What to Check |
|---------|---------------|
| Superset unresponsive | `kube_pod_status_phase`, container restarts, memory usage |
| Slow dashboards | PostgreSQL `pg_stat_activity`, Redis `redis_commands_duration_seconds_*` |
| Celery tasks failing | Worker pod logs (`kubectl logs -n superset -l app=superset-worker`), Redis connectivity |
| OOMKilled | `container_memory_working_set_bytes` vs limit (3072Mi configured) |
| Pod not scheduling | Events (`kubectl get events -n superset`), resource requests (600m CPU, 3Gi mem) |

---

## Complements

- `k8s-workload-metrics` — container resource metrics (CPU, memory, restarts)
- `backing-services-metrics` — PostgreSQL and Redis health
- `python-apm-metrics` — Python OTel runtime/HTTP/DB metrics (if instrumented)
- `keycloak-metrics` — Superset authenticates via Keycloak OAuth

## Sources

- Deployed config: `02-KUBE/00-CONFIG/k8s-setup/superset/superset/values.yaml.gotmpl`
- Chart repository: `http://apache.github.io/superset/` version 0.14.0
- Apache Superset docs: https://superset.apache.org/docs/configuration/configuring-superset
- Superset Event Logging (STATS_LOGGER): https://superset.apache.org/admin-docs/configuration/event-logging
