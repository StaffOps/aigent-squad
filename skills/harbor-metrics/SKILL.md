---
name: harbor-metrics
description: "Use when diagnosing Harbor container registry health — image push/pull latency, registry storage performance, job queue saturation, quota exhaustion, component availability, or project artifact counts. Covers harbor_core_http_*, harbor_project_*, harbor_artifact_pulled, harbor_up, harbor_task_*, harbor_jobservice_*, registry_http_*, registry_storage_*. Grounded on Helm chart harbor/harbor 1.17.1 (appVersion v2.13.1), official docs https://goharbor.io/docs/2.13.0/administration/metrics/."
keywords: [harbor]
---
# Harbor Container Registry Metrics

Prometheus metrics for the **Harbor** container registry platform (all components).

**Deployed version**: Helm chart `harbor/harbor` **v1.17.1** → Harbor **v2.13.1**.

**Metrics status**: ✅ **ENABLED** in deployed config (`metrics.enabled: true`, `metrics.serviceMonitor.enabled: true`). Harbor exporter component explicitly deployed (1 replica).

---

## Scrape Pipeline

Harbor exposes metrics from **four components**, each scraped independently via ServiceMonitor:

```
harbor-exporter  (:8001/metrics)             → vmagent → VictoriaMetrics   [business/DB metrics]
harbor-core      (:8001/metrics?comp=core)    → vmagent → VictoriaMetrics   [API request RED]
harbor-registry  (:8001/metrics?comp=registry)→ vmagent → VictoriaMetrics   [Distribution storage]
harbor-jobservice(:8001/metrics?comp=jobservice)→ vmagent → VictoriaMetrics [job processing]
```

The Helm chart creates a `ServiceMonitor` resource (since `metrics.serviceMonitor.enabled: true`) that instructs vmagent/Prometheus to scrape all four endpoints.

**How metrics are enabled**: Set `metrics.enabled: true` in Helm values. This configures all Harbor components to expose `/metrics` on the metrics port (default 8001). The `exporter` component is deployed separately and pulls business metrics from the Harbor database (projects, quotas, artifacts, health).

---

## 1. Harbor Exporter Metrics (Business / DB-Sourced)

These metrics come from the **harbor-exporter** component, which queries the Harbor database periodically (configured `cacheDuration: 23` seconds in this deployment).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `harbor_project_total` | Gauge | Total number of projects (public + private) | Capacity planning; unexpected drop = deletion event | `public` |
| `harbor_project_repo_total` | Gauge | Number of repositories per project | Identify largest projects; growth tracking | `public`, `project_name` |
| `harbor_project_member_total` | Gauge | Number of members per project | Access audit; unexpected member count changes | `project_name` |
| `harbor_project_quota_usage_byte` | Gauge | Current storage usage of a project (bytes) | Quota exhaustion detection; correlate with push failures | `project_name` |
| `harbor_project_quota_byte` | Gauge | Quota limit set for a project (bytes) | Compare with usage to find projects near limit | `project_name` |
| `harbor_artifact_pulled` | Gauge | Number of image pull operations per project | Identify hot images; validate proxy cache effectiveness | `project_name` |
| `harbor_project_artifact_total` | Gauge | Total artifacts by type per project | Growth tracking; validate GC effectiveness | `artifact_type`, `project_name`, `public` |
| `harbor_health` | Gauge | Overall Harbor health status (1=healthy, 0=unhealthy) | Alerting: fire when `harbor_health == 0` | — |
| `harbor_system_info` | Gauge | Harbor instance metadata (always 1, labels carry info) | Version audit; auth mode verification | `auth_mode`, `harbor_version`, `self_registration` |
| `harbor_up` | Gauge | Per-component running status (1=up, 0=down) | Detect which component is down | `component` (core, database, jobservice, portal, redis, registry, registryctl, trivy) |
| `harbor_task_queue_size` | Gauge | Number of pending tasks in queue per job type | Job queue saturation; GC/replication/scan backlog | `instance`, `job`, `type` |
| `harbor_task_queue_latency` | Gauge | Age of the oldest pending task in queue (seconds) | Stale queue detection; processing stall | `instance`, `job`, `type` |
| `harbor_task_scheduled_total` | Gauge | Number of scheduled (future) tasks | Validate scheduled GC/scan jobs exist | `instance`, `job` |
| `harbor_task_concurrency` | Gauge | Current concurrent tasks per pool | Worker saturation; compare with max workers | `instance`, `job`, `pool`, `type` |

---

## 2. Harbor Core Metrics (API RED)

These metrics come from the **harbor-core** component and measure the Harbor REST API performance.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `harbor_core_http_inflight_requests` | Gauge | Currently in-flight API requests | Core saturation; sudden spike = client storm or slow backend | `operation` |
| `harbor_core_http_request_duration_seconds` | Summary | Request latency per operation | P50/P99 latency by endpoint; identify slow API operations | `method`, `operation`, `quantile` |
| `harbor_core_http_request_total` | Counter | Total request count per method/operation | Error rate (filter by 4xx/5xx via operation); traffic patterns | `method`, `operation` |

> **Note**: The `operation` label maps to `operationId` from the Harbor OpenAPI spec (e.g., `listProjects`, `getArtifact`, `createProject`). Legacy endpoints without an operationId show `unknown`.

---

## 3. Registry Metrics (Distribution/Distribution)

These metrics come from the **Docker Distribution** (registry v2) embedded in Harbor, measuring blob/manifest storage I/O.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `registry_http_in_flight_requests` | Gauge | Currently in-flight registry HTTP requests | Registry saturation during mass push/pull | `handler` |
| `registry_http_request_duration_seconds` | Histogram | Registry request latency | Slow pull/push diagnosis; S3 backend latency | `handler`, `method`, `le` |
| `registry_http_request_size_bytes` | Histogram | Request payload size | Detect unusually large layer pushes | `handler`, `le` |
| `registry_http_requests_total` | Counter | Total registry HTTP requests by status | Error rate (5xx from S3 backend); traffic volume | `code`, `handler`, `method` |
| `registry_http_response_size_bytes` | Histogram | Response payload size | Bandwidth estimation; large manifest pulls | `handler`, `le` |
| `registry_storage_action_seconds` | Histogram | Time for storage backend operations | S3 latency diagnosis; identify slow operations | `action`, `driver`, `le` |
| `registry_storage_cache_total` | Gauge | Storage cache request count | Cache effectiveness (compare hits vs total) | `type` |

---

## 4. Harbor Jobservice Metrics

These metrics come from the **harbor-jobservice** component, which processes async tasks (scan, GC, replication, retention).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `harbor_jobservice_info` | Gauge | Jobservice instance metadata (always 1) | Verify worker pool size and node count | `instance`, `job`, `node`, `pool`, `workers` |
| `harbor_jobservice_task_total` | Counter | Total processed tasks by type and status | Task failure rate; identify failing job types | `instance`, `job`, `status`, `type` |
| `harbor_jobservice_task_process_time_seconds` | Summary | Task processing duration | Slow task detection; GC/scan duration baseline | `instance`, `job`, `quantile`, `status`, `type` |

---

## 5. Go Runtime Metrics

All four Harbor components expose standard `go_*` and `process_*` metrics from `client_golang`. See **go-apm-metrics** skill for full reference. Key ones for Harbor:

| Metric Name | Troubleshooting Use |
|---|---|
| `go_goroutines` | Goroutine leak in core/registry under load |
| `go_memstats_alloc_bytes` | Memory growth / GC pressure |
| `process_resident_memory_bytes` | Actual RSS; compare with k8s limits |

---

## Troubleshooting Quick-Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Image push fails with quota error | `harbor_project_quota_usage_byte` vs `harbor_project_quota_byte` for the project |
| Slow image pulls | `registry_http_request_duration_seconds` (p99), `registry_storage_action_seconds` (S3 latency) |
| Harbor portal shows "unhealthy" | `harbor_health` == 0, then `harbor_up` per component to find the failing one |
| Vulnerability scan backlog | `harbor_task_queue_size{type="scan"}`, `harbor_task_queue_latency{type="scan"}` |
| GC not freeing space | `harbor_task_queue_size{type="gc"}`, `harbor_jobservice_task_total{type="gc",status="error"}` |
| Replication jobs failing | `harbor_jobservice_task_total{type="replication",status="error"}` rate |
| Core API latency spike | `harbor_core_http_request_duration_seconds{quantile="0.99"}`, `harbor_core_http_inflight_requests` |
| Registry 5xx errors | `registry_http_requests_total{code=~"5.."}` rate; check S3 connectivity |
| Job worker saturation | `harbor_task_concurrency` vs max workers in `harbor_jobservice_info{workers=...}` |
| Component down | `harbor_up{component="<name>"} == 0` — alerts on specific component failure |

### Key Alert Expressions

```promql
# Harbor overall health
harbor_health == 0

# Component down
harbor_up == 0

# Quota >90% used
(harbor_project_quota_usage_byte / harbor_project_quota_byte) > 0.9

# Job queue stale (>10min latency)
harbor_task_queue_latency > 600

# Registry S3 errors
rate(registry_http_requests_total{code=~"5.."}[5m]) > 0

# Core API error rate >5%
sum(rate(harbor_core_http_request_total{operation=~".*",method=~".*"}[5m])) by (operation)
# (filter by 4xx/5xx via Loki correlation — core does not expose status_code label on this metric)
```

---

## Deployment-Specific Notes

| Setting | Value | Impact |
|---------|-------|--------|
| Exporter `cacheDuration` | 23s | DB-sourced metrics refresh every 23s (not real-time) |
| Exporter `cacheCleanInterval` | 14400s (4h) | Cache eviction cycle |
| Core replicas | 2 | Metrics aggregated across replicas by ServiceMonitor |
| Registry replicas | 2 | Same — aggregate or filter by `pod` label |
| Metrics port | 8001 (chart default) | All components share the same port |
| Storage backend | S3 (`bdc-eks-prd-harbor-nv`) | `registry_storage_action_seconds{driver="s3"}` |
| Database | External RDS PostgreSQL | `harbor_up{component="database"}` monitors RDS reachability |
| Redis | Internal (in-cluster) | `harbor_up{component="redis"}` monitors internal Redis |
| Trivy | Enabled (1 replica) | Scan tasks visible in jobservice metrics |

---

## Complements

- **go-apm-metrics** — Go runtime metrics (`go_*`, `process_*`) present on all Harbor components
- **k8s-workload-metrics** — Pod-level container resource metrics (CPU, memory, restarts)
- **backing-services-metrics** — PostgreSQL and Redis metrics for Harbor's backing stores
- **traefik-metrics** — Ingress-level metrics for the Harbor external endpoint

---

## Sources

- [Harbor v2.13.0 — Access Metrics (official docs)](https://goharbor.io/docs/2.13.0/administration/metrics/)
- [Harbor Helm chart v1.17.1](https://github.com/goharbor/harbor-helm/releases/tag/v1.17.2) (1.17.x series → Harbor v2.13.x)
- [Distribution/Distribution metrics source](https://github.com/distribution/distribution/blob/main/notifications/metrics.go)
- Deployed config: `02-KUBE/00-CONFIG/k8s-setup/harbor/harbor/values.yaml.gotmpl`
