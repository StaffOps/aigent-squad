---
name: sonarqube-metrics
description: "Use when diagnosing SonarQube health \u2014 Compute Engine queue saturation, Elasticsearch disk pressure, database connection pool exhaustion, Web/CE JVM process health, or Tomcat thread saturation. Covers sonarqube_* (Web API), SonarQube_* + Tomcat_* + process_* (JMX exporter). Grounded on Helm chart sonarqube/sonarqube version 2025.4.2 (SonarQube Server 2025.4 LTA), official docs https://docs.sonarsource.com/sonarqube-server/2025.4/server-installation/on-kubernetes-or-openshift/set-up-monitoring/prometheus-metrics. WARNING: JMX exporter is DISABLED and PodMonitor is NOT deployed in the current co"
keywords: [sonarqube, ce, jvm, jmx, lta]
---
# SonarQube Metrics

Prometheus metrics exposed by SonarQube Server on Kubernetes.

**Grounded on**: Helm chart `sonarqube/sonarqube` version **2025.4.2** (SonarQube Server 2025.4 LTA), deployed to `sonarqube` namespace on `devops-core` cluster.

---

## Current Scrape Status: ⚠️ NOT SCRAPED

**Critical honesty**: As of the deployed configuration, SonarQube metrics are **NOT being scraped into VictoriaMetrics**:

| Component | Chart Default | Deployed Override | Status |
|-----------|--------------|-------------------|--------|
| `prometheusExporter.enabled` | `false` | not set | ❌ JMX exporter sidecar NOT deployed |
| `prometheusMonitoring.podMonitor.enabled` | `false` | not set | ❌ No PodMonitor for Web API endpoint |
| `monitoringPasscode` | not set | ✅ set (from AWS Secrets Manager) | ✅ Web API endpoint IS accessible |
| VMServiceScrape / manual scrape config | — | not present in `sonarqube-raw` | ❌ No scrape target in vmagent |

**To enable scraping**, either:
1. Set `prometheusMonitoring.podMonitor.enabled: true` in values → creates PodMonitor for `/api/monitoring/metrics` (Bearer-token auth via passcode)
2. Set `prometheusExporter.enabled: true` → deploys JMX Prometheus Java agent sidecar on ports 8000 (Web) and 8001 (CE)
3. Create a VMServiceScrape/VMPodScrape manually in the `sonarqube-raw` release

---

## Scrape Pipeline (when enabled)

SonarQube exposes metrics via **two distinct mechanisms**:

```
Mechanism 1 — Web API (all deployments):
  SonarQube pod :9000/api/monitoring/metrics (Bearer token auth)
    → PodMonitor (created by chart when prometheusMonitoring.podMonitor.enabled=true)
    → vmagent scrape
    → VictoriaMetrics

Mechanism 2 — JMX Exporter (Kubernetes only, requires prometheusExporter.enabled=true):
  SonarQube pod :8000 (Web JVM metrics)
  SonarQube pod :8001 (Compute Engine JVM metrics)
    → PodMonitor (separate ports)
    → vmagent scrape
    → VictoriaMetrics
```

**Authentication**: The Web API endpoint requires `Authorization: Bearer <monitoringPasscode>` header. The Helm chart configures the PodMonitor with this token automatically when both `monitoringPasscode` and `prometheusMonitoring.podMonitor.enabled` are set.

---

## Web API Metrics (`sonarqube_*`)

Available at `/api/monitoring/metrics` on all deployment types. These use Prometheus `snake_case` naming.

### Core Health

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_health_web_status` | Gauge | Web process up (1) or down (0) | Basic liveness; 0 = SonarQube Web server process is down | — |
| `sonarqube_health_compute_engine_status` | Gauge | CE process up (1) or down (0) | 0 = analysis results NOT being processed; queue will grow indefinitely | — |
| `sonarqube_health_elasticsearch_status` | Gauge | Embedded ES up (1) or down (0) | 0 = search/indexing broken; UI unusable | — |
| `sonarqube_web_uptime_minutes` | Gauge | Minutes since SonarQube started | Detect recent restarts; low value after expected uptime = crash-loop | — |

### Compute Engine (Analysis Queue)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_compute_engine_pending_tasks_total` | Gauge | Tasks pending in CE queue | Growing value = queue saturation; CE can't keep up with analysis submissions | — |
| `sonarqube_compute_engine_tasks_running_duration_seconds` | Summary | Duration of running CE tasks | High p99 = slow analysis; may indicate DB or ES bottleneck | `task_type`, `project_key` |
| `sonarqube_compute_engine_system_tasks_running_duration_seconds` | Summary | Duration of system CE tasks | Slow system tasks (e.g. migration, index) block project analysis | `task_type` |

### Elasticsearch (Embedded)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_elasticsearch_disk_space_free_bytes` | Gauge | Free disk on ES data node | Low = risk of ES going read-only; SonarQube will become degraded | `node_name` |
| `sonarqube_elasticsearch_disk_space_total_bytes` | Gauge | Total disk on ES data node | Context for free bytes ratio | `node_name` |

### DevOps Platform Integrations

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_health_integration_github_status` | Gauge | GitHub integration health (1=green) | 0 = PR decoration, branch analysis import broken | — |
| `sonarqube_health_integration_gitlab_status` | Gauge | GitLab integration health (1=green) | 0 = MR decoration broken | — |
| `sonarqube_health_integration_bitbucket_status` | Gauge | Bitbucket integration health (1=green) | 0 = PR decoration broken | — |
| `sonarqube_health_integration_azuredevops_status` | Gauge | Azure DevOps integration health (1=green) | 0 = PR decoration broken | — |

### License (Developer/Enterprise editions)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_license_days_before_expiration_total` | Gauge | Days until license expires | Alert when <30 days; 0 = expired, features locked | — |
| `sonarqube_license_number_of_lines_analyzed_total` | Gauge | Lines of code currently analyzed | Capacity planning; approaching limit = need license upgrade | — |
| `sonarqube_license_number_of_lines_remaining_total` | Gauge | Lines remaining before license cap | Low value = risk of hitting limit on next analysis | — |

### Miscellaneous

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `sonarqube_number_of_connected_sonarlint_clients` | Gauge | Connected SonarQube for IDE clients | Usage tracking; spike may correlate with API load | — |

---

## JMX Exporter Metrics (when `prometheusExporter.enabled: true`)

These require the JMX Prometheus Java agent sidecar. Exposed on ports **8000** (Web process) and **8001** (CE process). Use `instance` or `port` label to distinguish Web vs CE.

> ⚠️ JMX metrics use `SonarQube_PascalCase` and `Tomcat_PascalCase` naming (matching JMX MBean object names). All appear as `Untyped` in Prometheus — treat as Gauges.

### Database Connection Pool (Web — port 8000)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `SonarQube_Database_PoolActiveConnections` | Untyped | Active DB connections (Web) | High = DB pressure from Web requests |
| `SonarQube_Database_PoolIdleConnections` | Untyped | Idle DB connections (Web) | 0 idle + high active = pool exhaustion |
| `SonarQube_Database_PoolMaxConnections` | Untyped | Max pool size (Web) | Context for active ratio |
| `SonarQube_Database_PoolTotalConnections` | Untyped | Total connections (active + idle) | Should be ≤ max; equal to max = saturated |

### Database Connection Pool (CE — port 8001)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `SonarQube_ComputeEngineDatabaseConnection_PoolActiveConnections` | Untyped | Active DB connections (CE) | High during analysis = expected; sustained high without tasks = leak |
| `SonarQube_ComputeEngineDatabaseConnection_PoolIdleConnections` | Untyped | Idle DB connections (CE) | 0 during heavy analysis = pool pressure |
| `SonarQube_ComputeEngineDatabaseConnection_PoolMaxConnections` | Untyped | Max pool size (CE) | If active == max → tasks will queue on DB access |
| `SonarQube_ComputeEngineDatabaseConnection_PoolTotalConnections` | Untyped | Total connections (CE) | Saturation indicator |

### Compute Engine Tasks (CE — port 8001)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `SonarQube_ComputeEngineTasks_PendingCount` | Untyped | Pending tasks | Growing = CE can't keep up; check workers, DB, resources |
| `SonarQube_ComputeEngineTasks_InProgressCount` | Untyped | Currently processing | Should be ≤ WorkerCount |
| `SonarQube_ComputeEngineTasks_ErrorCount` | Untyped | Tasks failed since startup | Rising = analysis failures (check CE logs) |
| `SonarQube_ComputeEngineTasks_SuccessCount` | Untyped | Tasks completed since startup | Rate = throughput; flat while pending grows = stuck |
| `SonarQube_ComputeEngineTasks_LongestTimePending` | Untyped | Age (ms) of oldest pending task | High value = user-visible delay for analysis results |
| `SonarQube_ComputeEngineTasks_WorkerCount` | Untyped | Configured CE workers | Baseline for parallelism |
| `SonarQube_ComputeEngineTasks_ProcessingTime` | Untyped | Total processing time (ms) since startup | Average = ProcessingTime / SuccessCount |

### JVM Process Metrics (both ports)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `process_cpu_seconds_total` | Counter | CPU time consumed | Rate = CPU utilization of the JVM |
| `process_resident_memory_bytes` | Gauge | RSS memory | Compare to container limit; approaching limit = OOM risk |
| `process_open_fds` | Gauge | Open file descriptors | High = connection/file leak |
| `process_max_fds` | Gauge | Max FDs allowed | Context for open_fds ratio |

### Tomcat Thread Pool (Web — port 8000)

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `Tomcat_ThreadPool_currentThreadsBusy` | Untyped | Busy request-processing threads | High ratio to maxThreads = request saturation |
| `Tomcat_ThreadPool_currentThreadCount` | Untyped | Current thread count | Threads created on demand up to max |
| `Tomcat_ThreadPool_maxThreads` | Untyped | Max threads configured | Ceiling for concurrency |
| `Tomcat_ThreadPool_connectionCount` | Untyped | Active connections | Correlate with busy threads |
| `Tomcat_GlobalRequestProcessor_requestCount` | Untyped | Total requests processed | Rate = RPS |
| `Tomcat_GlobalRequestProcessor_errorCount` | Untyped | Total HTTP errors | Rate spike = application-level failures |
| `Tomcat_GlobalRequestProcessor_processingTime` | Untyped | Total request processing time (ms) | Average latency = processingTime / requestCount |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Analysis results delayed | `sonarqube_compute_engine_pending_tasks_total` (Web API) or `SonarQube_ComputeEngineTasks_PendingCount` (JMX) |
| Analysis failing | `SonarQube_ComputeEngineTasks_ErrorCount` + CE pod logs |
| SonarQube UI slow | `Tomcat_ThreadPool_currentThreadsBusy` vs `maxThreads`, `SonarQube_Database_PoolActiveConnections` |
| SonarQube down | `sonarqube_health_web_status`, `sonarqube_health_elasticsearch_status` |
| ES disk pressure | `sonarqube_elasticsearch_disk_space_free_bytes` / `total_bytes` ratio |
| DB connection exhaustion | `PoolActiveConnections` == `PoolMaxConnections` (Web or CE) |
| License approaching limit | `sonarqube_license_number_of_lines_remaining_total` < 10% of total |
| GitHub/GitLab PR decoration broken | `sonarqube_health_integration_github_status` == 0 |
| OOM risk | `process_resident_memory_bytes` vs container memory limit |
| Recent crash | `sonarqube_web_uptime_minutes` unexpectedly low |

---

## Enabling Metrics Scraping (Actionable)

To start collecting SonarQube metrics into VictoriaMetrics, add to `sonarqube/sonarqube/values.yaml.gotmpl`:

```yaml
# Option A: Web API metrics only (lightweight, no sidecar)
prometheusMonitoring:
  podMonitor:
    enabled: true
    interval: 30s

# Option B: Full JMX metrics (adds sidecar container to pod)
prometheusExporter:
  enabled: true

# Option C: Both (recommended for full visibility)
prometheusExporter:
  enabled: true
prometheusMonitoring:
  podMonitor:
    enabled: true
    interval: 30s
```

**Note**: `monitoringPasscode` is already set — the authentication requirement is satisfied.
The PodMonitor will be picked up by vmagent's PodMonitor CRD discovery (vm-operator).

---

## Complements

- `k8s-workload-metrics` — container CPU/memory/restarts for the SonarQube pod (available NOW, regardless of scrape config)
- `backing-services-metrics` — PostgreSQL health for the external RDS (`eks-postgres`) backing SonarQube

---

## Sources

- [Official metric list (2025.4)](https://docs.sonarsource.com/sonarqube-server/2025.4/server-installation/on-kubernetes-or-openshift/set-up-monitoring/prometheus-metrics)
- [Prometheus setup guide (2025.4)](https://docs.sonarsource.com/sonarqube-server/2025.4/server-installation/on-kubernetes-or-openshift/set-up-monitoring/prometheus)
- [Monitoring introduction (2025.4)](https://docs.sonarsource.com/sonarqube-server/2025.4/server-installation/on-kubernetes-or-openshift/set-up-monitoring/introduction)
- [Helm chart values.yaml (master)](https://github.com/SonarSource/helm-chart-sonarqube/blob/master/charts/sonarqube/values.yaml)
- Deployed config: `/home/karlipegomes/Documents/<ORG>/02-KUBE/00-CONFIG/k8s-setup/sonarqube/sonarqube/values.yaml.gotmpl`
