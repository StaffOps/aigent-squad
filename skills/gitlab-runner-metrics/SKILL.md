---
name: gitlab-runner-metrics
description: "Use when diagnosing GitLab Runner manager health \u2014 job execution capacity, queue saturation, API communication failures, autoscaling machine states, or runner concurrency limits. Covers gitlab_runner_jobs, gitlab_runner_job_duration_seconds, gitlab_runner_jobs_total, gitlab_runner_errors_total, gitlab_runner_api_request_statuses_total, gitlab_runner_concurrent, gitlab_runner_limit, gitlab_runner_request_concurrency, gitlab_runner_acceptable_job_queuing_duration_exceeded_total, gitlab_runner_version_info, plus go_* and process_*. Grounded on Helm chart gitlab/gitlab-runner 0.84.1 (appVersion ~v"
keywords: [gitlab-runner, gitlab, runner, "gitlab runner"]
---
# GitLab Runner Manager Metrics

Runner manager process metrics for the **CI/CD executor fleet** — job throughput,
capacity saturation, API health, and queue timing.

**Question answered**: "Are runners processing jobs efficiently, or are they at
capacity / losing contact with GitLab?"

**Scope**: GitLab Runner manager self-telemetry exposed at `:9252/metrics`, as
scraped into VictoriaMetrics by vmagent via ServiceMonitor.

---

## Deployment Context

| Attribute | Value |
|-----------|-------|
| **Helm chart** | `gitlab/gitlab-runner` v0.84.1 |
| **App version** | GitLab Runner ~v17.10 |
| **Releases** | `bigdatacorp` (3 replicas, amd64), `bigdatacorp-graviton` (2 replicas, arm64) |
| **Namespace** | `gitlab-runner` |
| **Cluster** | `devops-core` |
| **Executor** | Kubernetes (pods spawned per job) |
| **Metrics enabled** | ✅ `metrics.enabled: true` |
| **ServiceMonitor** | ✅ `metrics.serviceMonitor.enabled: true` |
| **Metrics port** | 9252 (default) |

---

## Scrape Pipeline

```
GitLab Runner manager pod (:9252/metrics)
  → ServiceMonitor (prometheus-operator CRD)
    → vmagent scrape
      → VictoriaMetrics (vminsert → vmstorage)
```

Metrics are enabled via Helm values:
```yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
```

Each runner manager pod exposes runner-specific + Go runtime metrics on port 9252.
With 3+2 = 5 total manager pods, expect 5 scrape targets in the `gitlab-runner`
namespace.

---

## Runner Business Metrics

### Job Execution

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `gitlab_runner_jobs` | Gauge | Number of jobs currently being executed, by state | **Primary saturation signal** — if consistently equals `concurrent`, runners are at capacity | `state` (running, idle), `stage`, `executor_stage` |
| `gitlab_runner_jobs_total` | Counter | Total jobs executed (cumulative) | Rate = job throughput; drop in rate = GitLab communication or capacity problem | `runner` |
| `gitlab_runner_job_duration_seconds` | Histogram | Distribution of completed job durations | Identify slow jobs; p99 increase = executor or infrastructure degradation | `runner`, `le` |
| `gitlab_runner_job_queue_duration_seconds` | Histogram | Time jobs spend waiting in queue before execution begins | Queue time increase = runners at capacity, need more replicas or higher `concurrent` | `runner`, `le` |
| `gitlab_runner_job_stage_duration_seconds` | Histogram | Duration of individual job stages (HIGH CARDINALITY — requires `FF_EXPORT_HIGH_CARDINALITY_METRICS`) | Pinpoint which stage is slow (prepare_executor, get_sources, step_script, etc.) | `stage`, `runner`, `le` |
| `gitlab_runner_job_execution_mode_total` | Counter | Jobs executed by mode (steps vs traditional) and executor | Track adoption of step-based execution | `mode`, `executor` |

### Capacity & Configuration

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `gitlab_runner_concurrent` | Gauge | Current `concurrent` config value (max parallel jobs per manager) | Compare with `gitlab_runner_jobs{state="running"}` to measure headroom | — |
| `gitlab_runner_limit` | Gauge | Current `limit` config value (per-runner token limit; 0 = unlimited) | Verify expected limits are applied | — |
| `gitlab_runner_request_concurrency` | Gauge | Current number of concurrent requests polling for new jobs | High value approaching capacity = polling pressure on GitLab API | — |
| `gitlab_runner_request_concurrency_exceeded_total` | Counter | Times request concurrency limit was exceeded | Non-zero rate = runner can't poll fast enough, jobs may wait longer | — |
| `gitlab_runner_acceptable_job_queuing_duration_exceeded_total` | Counter | Jobs that exceeded the configured acceptable queuing duration threshold | Non-zero = SLO violation for job wait time; scale up runners | — |

### API Communication

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `gitlab_runner_api_request_statuses_total` | Counter | Total API requests to GitLab, by endpoint and HTTP status | Rate of 4xx/5xx = communication failure; 401/403 = token issues; 5xx = GitLab overloaded | `runner`, `endpoint`, `status` |
| `gitlab_runner_errors_total` | Counter | Caught errors in runner process, by severity | Increasing rate of `error` level = investigate logs immediately | `level` (warning, error) |

### Job Router (v17.8+)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `gitlab_runner_job_router_circuit_breaker_state` | Gauge | Job Router circuit breaker state (0=closed, 1=open, 2=half-open) | If open (1) = Job Router unavailable, falling back to polling | — |
| `gitlab_runner_job_router_circuit_breaker_trips_total` | Counter | Times circuit breaker tripped open | Increasing trips = persistent Job Router connectivity issues | — |
| `gitlab_runner_job_router_fallbacks_total` | Counter | Requests that fell back from Job Router to direct polling | High rate by `reason` helps diagnose router health | `reason` (no_discovery, breaker_open, dial_failed, breaker_tripped, router_disabled) |
| `gitlab_runner_job_router_get_job_duration_seconds` | Histogram | Job Router GetJob request duration | Router latency; compare with polling-based job acquisition | `le` |
| `gitlab_runner_job_router_discovery_cache_events_total` | Counter | Discovery cache lookups | High miss rate = frequent router re-discovery | `result` (hit, miss) |

### Version & Build Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `gitlab_runner_version_info` | Gauge (constant 1) | Build metadata for the runner binary | Confirm all managers run same version after upgrade; detect mixed fleet | `name`, `version`, `revision`, `os`, `architecture` |

---

## Go Runtime Metrics (subset — see go-apm-metrics for full reference)

Runner manager is a Go process; key runtime metrics:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutines | Leak detection (monotonic rise without job increase) |
| `go_memstats_alloc_bytes` | Gauge | Heap bytes allocated and in use | Memory pressure on runner manager pod |
| `process_resident_memory_bytes` | Gauge | RSS of the process | Compare with container memory limit |
| `process_cpu_seconds_total` | Counter | CPU time consumed | Rate = CPU saturation of manager process |
| `process_open_fds` | Gauge | Open file descriptors | Approaching `process_max_fds` = FD exhaustion |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Likely Cause |
|---------|------------------------|--------------|
| Jobs queued too long | `gitlab_runner_job_queue_duration_seconds` p99, `gitlab_runner_jobs` vs `gitlab_runner_concurrent` | Runners at capacity — increase replicas or `concurrent` |
| Job throughput dropped | `rate(gitlab_runner_jobs_total)`, `gitlab_runner_api_request_statuses_total{status=~"5.."}` | GitLab API errors or runner connectivity loss |
| Runner errors spiking | `rate(gitlab_runner_errors_total{level="error"})` | Check runner logs; common: auth token expired, RBAC, S3 cache access |
| API 401/403 responses | `gitlab_runner_api_request_statuses_total{status=~"40[13]"}` | Runner token invalid/expired; re-register runner |
| Runner not picking up jobs | `gitlab_runner_request_concurrency`, `gitlab_runner_request_concurrency_exceeded_total` | Polling saturated; check network to GitLab |
| Mixed runner versions after upgrade | `gitlab_runner_version_info` grouped by `version` | Rolling update incomplete; check Deployment rollout |
| Graviton jobs failing | Filter by pod label (`bigdatacorp-graviton`); check `gitlab_runner_errors_total` | helper_image mismatch, arm64-incompatible build steps |
| SLO breach on queue time | `gitlab_runner_acceptable_job_queuing_duration_exceeded_total` | Scale up runner fleet or increase `concurrent` |

---

## Key PromQL / MetricsQL Queries

```promql
# Capacity utilization per runner release (percentage)
gitlab_runner_jobs{state="running"} / gitlab_runner_concurrent * 100

# Job throughput (jobs/min) across all runners
sum(rate(gitlab_runner_jobs_total[5m])) * 60

# API error rate
sum(rate(gitlab_runner_api_request_statuses_total{status=~"5.."}[5m]))
  / sum(rate(gitlab_runner_api_request_statuses_total[5m]))

# p95 job duration
histogram_quantile(0.95, sum(rate(gitlab_runner_job_duration_seconds_bucket[10m])) by (le))

# p95 queue wait time
histogram_quantile(0.95, sum(rate(gitlab_runner_job_queue_duration_seconds_bucket[10m])) by (le))

# Error rate by level
sum by (level) (rate(gitlab_runner_errors_total[5m]))

# Version consistency check
count by (version) (gitlab_runner_version_info)
```

---

## Multi-Instance Considerations

This environment runs **two runner releases** on the same cluster:

| Release | Architecture | Replicas | Node selector |
|---------|-------------|----------|---------------|
| `bigdatacorp` | amd64 | 3 | `purpose=runners`, zone=us-east-1c, arch=amd64 |
| `bigdatacorp-graviton` | arm64 | 2 | `purpose=runners`, zone=us-east-1c, arch=arm64 |

Both use `concurrent: 10`. Total cluster capacity: 5 managers × 10 = **50 parallel jobs**.

When querying, filter by Kubernetes labels (`release`, `app.kubernetes.io/instance`)
or runner name to separate architectures. The `runner` label in metrics identifies
the registered runner token (different per release).

---

## Autoscaling Metrics (NOT applicable here)

The following metrics exist but are **NOT relevant** to this deployment because
it uses the Kubernetes executor (not Docker Machine autoscaling):

- `gitlab_runner_autoscaling_machine_creation_duration_seconds`
- `gitlab_runner_autoscaling_machine_states`

These appear only when using the Docker Machine or Instance executor with autoscaling.

---

## Complements

- **go-apm-metrics** — full Go runtime metrics reference (goroutines, GC, scheduler)
- **k8s-workload-metrics** — container-level CPU/memory/restarts for runner manager pods
- **argocd-metrics** — if runners are deployed via ArgoCD (this env uses helmfile directly)

---

## Sources

- [GitLab Runner Monitoring](https://docs.gitlab.com/runner/monitoring/) — official metrics reference
- [Fleet Scaling — Monitoring Runners](https://docs.gitlab.com/runner/fleet_scaling/#monitoring-runners) — full metric table
- [High Cardinality Metrics](https://docs.gitlab.com/runner/fleet_scaling/#high-cardinality-metrics) — `job_stage_duration_seconds` opt-in
- Deployed config: `k8s-setup/gitlab-runner/helmfile.yaml.gotmpl` (chart version 0.84.1)
- Deployed values: `bigdatacorp/values.yaml.gotmpl`, `bigdatacorp-graviton/values.yaml.gotmpl`
