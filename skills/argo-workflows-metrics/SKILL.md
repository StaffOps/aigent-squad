---
name: argo-workflows-metrics
description: "Use when diagnosing Argo Workflows controller health, workflow execution backlogs, workqueue saturation, K8s API pressure, or pod scheduling failures. Covers argo_workflows_gauge, argo_workflows_queue_*, argo_workflows_operation_duration_seconds, argo_workflows_error_count, argo_workflows_k8s_request_*, argo_workflows_pods_gauge, argo_workflows_is_leader, argo_workflows_workers_busy_count, and go_* runtime metrics from the workflow-controller."
keywords: [argo-workflows, argo, workflows, "argo workflows"]
---
# Argo Workflows Controller Metrics

Prometheus metrics emitted by the **Argo Workflows workflow-controller** for
monitoring controller health, workflow lifecycle, workqueue pressure, and
Kubernetes API interactions.

**Deployed version**: Helm chart `argo/argo-workflows` **v1.0.13** → appVersion
**v4.0.5** (argo-helm repo: `argoproj.github.io/argo-helm`).

> **Metric prefix**: In Argo Workflows v4.0+, all controller metrics are exposed
> at `:9090/metrics` on the `workflow-controller-metrics` service. The Prometheus
> scrape format prepends `argo_workflows_` to all metric names documented in the
> official docs. Custom user-defined workflow metrics also receive this prefix.

---

## Scrape Pipeline

```
workflow-controller pod (:9090/metrics)
  → ServiceMonitor (controller.serviceMonitor.enabled: true)
    → vmagent scrape
      → VictoriaMetrics
```

**Enabled via**: `controller.metricsConfig.enabled: true` + `controller.serviceMonitor.enabled: true`
in the helmfile values (both confirmed set in deployed config).

The controller runs with **2 replicas** and leader election. Only the leader
emits workflow-state metrics; both replicas expose process/go metrics.

---

## Workflow Lifecycle Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_gauge` | Gauge | Number of workflows in each phase currently in the cluster | Workflow backlog — `Running` growing without `Succeeded` = stuck workflows | `phase` (Pending, Running, Succeeded, Failed, Error) |
| `argo_workflows_total_count` | Counter | Workflows that have entered each phase (lifecycle tracking) | Throughput — rate of workflows completing vs failing over time | `phase`, `namespace` |
| `argo_workflows_pods_gauge` | Gauge | Workflow-created pods currently in the cluster by phase | Actual work being done — can diverge from workflow phase if pods are pending | `phase` |
| `argo_workflows_pods_total_count` | Counter | Total pods that have entered each phase | Pod throughput; high Failed rate = infra or image issues | `phase`, `namespace` |
| `argo_workflows_workflow_condition` | Gauge | Workflows with specific conditions (e.g., PodRunning) | Know how many workflows have actively running pods vs waiting | `type`, `status` |

---

## Workqueue Metrics (Controller Saturation)

These metrics come from the client-go workqueue and reveal whether the controller
is keeping up with cluster state changes.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_queue_depth_gauge` | Gauge | Current depth of each work queue | Growing depth = controller falling behind; correlate with CPU/memory | `queue_name` |
| `argo_workflows_queue_adds_count` | Counter | Additions to each work queue | Rate shows how busy each queue area is | `queue_name` |
| `argo_workflows_queue_latency` | Histogram | Time events wait in queue BEFORE processing | Queue latency > 60s = significant scheduling delay | `queue_name`, `le` |
| `argo_workflows_queue_duration` | Histogram | Time events take TO BE processed | Processing time per item; high p99 = complex workflows or API slowness | `queue_name`, `le` |
| `argo_workflows_queue_retries` | Counter | Times a message has been retried | High retry rate = transient failures or resource contention | `queue_name` |
| `argo_workflows_queue_longest_running` | Gauge | Seconds the longest-running processor has been running | Stuck processor detection; >300s = likely deadlock or API timeout | `queue_name` |
| `argo_workflows_queue_unfinished_work` | Gauge | Items that have not been processed yet | Backlog indicator; should trend toward 0 | `queue_name` |

**Queue names**: `workflow_queue`, `pod_cleanup_queue`, `cron_wf_queue`,
`workflow_ttl_queue`, `workflow_archive_queue`.

---

## Controller Performance & Operations

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_operation_duration_seconds` | Histogram | Duration of a single workflow reconciliation loop | Controller performance — p99 > 10s = complex workflows or API latency | (none) |
| `argo_workflows_workers_busy_count` | Gauge | Queue workers currently busy | Saturation signal — all workers busy = new work queued | `worker_type` |
| `argo_workflows_is_leader` | Gauge | 1 if this instance is the leader, 0 otherwise | Leader election health — both 0 = split-brain / no leader | (none) |
| `argo_workflows_error_count` | Counter | Specific controller errors by cause | Detect CronWorkflow submission errors, panics, spec errors | `cause` |
| `argo_workflows_log_messages` | Counter | Log messages emitted by level | Error log rate spike = controller struggling | `level` (error, warn, info) |

**`error_count` cause values**: `OperationPanic`, `CronWorkflowSubmissionError`, `CronWorkflowSpecError`.

---

## Kubernetes API Interaction

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_k8s_request_total` | Counter | API requests sent to Kubernetes API | Rate by verb/kind shows API pressure pattern | `kind`, `verb`, `status_code` |
| `argo_workflows_k8s_request_duration` | Histogram | Duration of Kubernetes API requests | Slow API calls (p99 > 5s) bottleneck the controller | `kind`, `verb`, `status_code`, `le` |
| `argo_workflows_client_rate_limiter_latency` | Histogram | Time waiting on client-side rate limiter | Non-zero = client QPS/burst settings throttling the controller | (none), `le` |

---

## Pod Lifecycle & Failures

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_pod_missing` | Counter | Pods not seen (deleted externally, e.g., by K8s) | High count under load = node pressure or preemption | `node_phase`, `recently_started` |
| `argo_workflows_pod_pending_count` | Counter | Pods that started pending by reason | Identify scheduling bottlenecks (Unschedulable, ImagePull) | `reason`, `namespace` |
| `argo_workflows_pod_restarts_total` | Counter | Pods auto-restarted by the failed pod restart feature | Infra failures before main container starts | `reason`, `condition`, `namespace` |

**`pod_restarts_total` reasons**: `Evicted`, `NodeShutdown`, `NodeAffinity`, `UnexpectedAdmissionError`.

---

## CronWorkflow Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_cronworkflows_triggered_total` | Counter | Times a CronWorkflow has been triggered | Confirm scheduled workflows are firing on time | `name` ⚠️, `namespace` |
| `argo_workflows_cronworkflows_concurrencypolicy_triggered` | Counter | Times concurrencyPolicy limited a CronWorkflow | Frequent triggers = crons overlapping / too slow | `name` ⚠️, `namespace`, `concurrency_policy` |

---

## WorkflowTemplate Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_workflowtemplate_triggered_total` | Counter | Workflows using workflowTemplateRef entering each phase | Template usage tracking | `name` ⚠️, `namespace`, `cluster_scope`, `phase` |
| `argo_workflows_workflowtemplate_runtime` | Histogram | Runtime of workflows using workflowTemplateRef | Performance tracking per template | `name` ⚠️, `namespace`, `cluster_scope`, `le` |

> ⚠️ Labels marked with ⚠️ have potentially HIGH CARDINALITY. Consider disabling
> or relabeling in scrape config if you have many unique CronWorkflow/Template names.

---

## Build & Version Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_workflows_version` | Gauge (info) | Build metadata for the controller | Confirm deployed version matches expected | `version`, `platform`, `go_version`, `build_date`, `compiler`, `git_commit`, `git_tree_state`, `git_tag` |

---

## Go Runtime Metrics (workflow-controller process)

The controller is a Go binary using `client_golang` Prometheus collectors.
Standard `go_*` and `process_*` metrics are emitted alongside `argo_workflows_*`.

See **`go-apm-metrics`** skill for the full Go runtime catalog. Key ones for the
workflow-controller:

| Metric Name | Type | Troubleshooting Use |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak in controller (monotonic rise) |
| `go_memstats_alloc_bytes` | Gauge | Memory pressure — correlate with OOMKills |
| `go_gc_duration_seconds` | Summary | GC pause impact on reconciliation latency |
| `process_resident_memory_bytes` | Gauge | Actual RSS — compare with container limits |
| `process_cpu_seconds_total` | Counter | CPU saturation of the controller process |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---|---|
| Workflows stuck in Running | `argo_workflows_gauge{phase="Running"}`, `argo_workflows_pods_gauge{phase="Pending"}`, `argo_workflows_queue_depth_gauge{queue_name="workflow_queue"}` |
| CronWorkflows not firing | `argo_workflows_cronworkflows_triggered_total` (rate=0?), `argo_workflows_is_leader` (=0 on all replicas?), `argo_workflows_error_count{cause="CronWorkflowSubmissionError"}` |
| Controller reconciliation slow | `argo_workflows_operation_duration_seconds` (p99), `argo_workflows_k8s_request_duration` (p99), `argo_workflows_client_rate_limiter_latency` |
| Queue backlog growing | `argo_workflows_queue_depth_gauge`, `argo_workflows_workers_busy_count`, `argo_workflows_queue_latency` (p99) |
| High K8s API error rate | `argo_workflows_k8s_request_total` by `status_code` (429/5xx), `argo_workflows_client_rate_limiter_latency` |
| Pods being evicted/restarted | `argo_workflows_pod_restarts_total` by `reason`, `argo_workflows_pod_missing`, `argo_workflows_pod_pending_count` |
| No leader / split-brain | `argo_workflows_is_leader` = 0 on ALL replicas |
| Controller OOMKilled | `process_resident_memory_bytes`, `go_memstats_alloc_bytes`, `argo_workflows_queue_depth_gauge` (memory grows with backlog) |
| High error rate | `argo_workflows_error_count` by `cause`, `argo_workflows_log_messages{level="error"}` |

---

## Complements

- **`go-apm-metrics`** — Full Go runtime metrics catalog (goroutines, GC, memory classes, scheduler)
- **`collector-internal-metrics`** — If OTel Collector scrapes these metrics (pipeline health)
- **`k8s-workload-metrics`** — Pod-level resource consumption (container_cpu, container_memory) for controller pods
- **`victoriametrics-troubleshooting`** — If scrape or storage issues affect metric availability

---

## Sources

- [Argo Workflows Official Metrics Documentation (main branch)](https://github.com/argoproj/argo-workflows/blob/main/docs/metrics.md) — confirms all metric names for v4.0.x
- [Argo Workflows Telemetry Configuration](https://argo-workflows.readthedocs.io/en/latest/workflow-telemetry/) — prefix `argo_workflows_` prepended to all metric names
- [Helm chart `argo/argo-workflows` v1.0.13 Chart.yaml](https://github.com/argoproj/argo-helm/blob/argo-workflows-1.0.13/charts/argo-workflows/Chart.yaml) — appVersion: v4.0.5
- [Deployed helmfile config](../../02-KUBE/00-CONFIG/k8s-setup/argo/helmfile.yaml.gotmpl) — version: 1.0.13, metricsConfig.enabled: true, serviceMonitor.enabled: true
- [Grafana Dashboard #21393](https://grafana.com/grafana/dashboards/21393-argo-workflows-metrics-3-6/) — community dashboard for Argo Workflows metrics
