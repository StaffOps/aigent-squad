---
name: argo-rollouts-metrics
description: "Use when diagnosing Argo Rollouts controller health, progressive delivery failures, reconciliation latency, analysis run outcomes, or notification delivery issues. Covers rollout_info*, rollout_phase, rollout_reconcile*, analysis_run_*, experiment_*, notification_send*, controller_clientset_k8s_request_total, workqueue_*, and go_* runtime metrics. Grounded on Helm chart argo/argo-rollouts 2.40.9 (appVersion v1.9.0)."
keywords: [argo-rollouts, argo, rollouts, "argo rollouts"]
---
# Argo Rollouts Controller Metrics

Self-telemetry from the **Argo Rollouts controller** — progressive delivery
(canary, blue-green, experiments, analysis runs) for Kubernetes.

**Question answered**: "Are rollouts progressing correctly? Is the controller
healthy? Are analysis runs succeeding? Are notifications being delivered?"

**Grounded on**: Helm chart `argo/argo-rollouts` **2.40.9** → app version
**v1.9.0**. Metric names confirmed from official docs
([controller-metrics.md](https://github.com/argoproj/argo-rollouts/blob/master/docs/features/controller-metrics.md))
and source code
([controller/metrics/metrics.go](https://github.com/argoproj/argo-rollouts/blob/master/controller/metrics/metrics.go)).

---

## Scrape Pipeline

```
Argo Rollouts Controller (:8090/metrics)
  → vmagent ServiceMonitor scrape (enabled in Helm values: controller.metrics.serviceMonitor.enabled=true)
  → VictoriaMetrics (MetricsQL/PromQL)
```

**Deployment**: 2 replicas (`controller.replicas: 2`) in namespace `argo`,
with Gateway API traffic router plugin (`argoproj-labs/gatewayAPI`).

**Metrics endpoint**: port `8090`, path `/metrics`.

**Enabled via** (values.yaml.gotmpl):
```yaml
controller:
  metrics:
    enabled: true
    serviceMonitor:
      enabled: true
```

---

## 1. Rollout Object Metrics

### Rollout Info (Gauge collectors)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rollout_info` | Gauge (1) | Existence and metadata of a Rollout resource | Enumerate all rollouts; filter by strategy/phase; detect orphaned rollouts | `name`, `namespace`, `strategy` (canary/blueGreen), `phase`, `weight` |
| `rollout_info_replicas_available` | Gauge | Available (ready + routable) replicas for a Rollout | Detect under-scaled rollouts; availability < desired = problem | `name`, `namespace` |
| `rollout_info_replicas_unavailable` | Gauge | Unavailable replicas for a Rollout | Non-zero during rollout is expected; sustained non-zero post-rollout = stuck | `name`, `namespace` |
| `rollout_info_replicas_desired` | Gauge | Desired replica count (from spec or HPA) | Compare with available to detect scheduling failures | `name`, `namespace` |
| `rollout_info_replicas_updated` | Gauge | Replicas running the new revision (canary/stable) | Track canary progression; updated < desired = rollout in progress or stuck | `name`, `namespace` |
| `rollout_phase` | Gauge (1) | **DEPRECATED — use `rollout_info`**. Rollout phase state | Legacy dashboards; value=1 when phase matches label | `name`, `namespace`, `phase` (Progressing/Healthy/Degraded/Paused) |

### Rollout Reconciliation Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rollout_reconcile_bucket` | Histogram (bucket) | Distribution of reconciliation duration for Rollouts | Detect slow reconciliation loops (p99 > 5s = controller saturation) | `namespace`, `name`, `le` |
| `rollout_reconcile_count` | Counter | Total reconciliation attempts per Rollout | Rate = reconciliation frequency; high rate = thrashing | `namespace`, `name` |
| `rollout_reconcile_sum` | Counter (seconds) | Cumulative reconciliation time per Rollout | Average = sum/count; useful for per-rollout cost analysis | `namespace`, `name` |
| `rollout_reconcile_error` | Counter | Reconciliation errors per Rollout | Non-zero rate = controller failing to converge a rollout | `namespace`, `name` |

---

## 2. Analysis Run Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `analysis_run_info` | Gauge (1) | Existence and metadata of an AnalysisRun | Enumerate active analysis runs; detect stuck/old runs | `name`, `namespace`, `phase` (Pending/Running/Successful/Failed/Error/Inconclusive) |
| `analysis_run_phase` | Gauge (1) | Current phase of the AnalysisRun | Detect Failed/Error runs blocking rollout progression | `name`, `namespace`, `phase` |
| `analysis_run_metric_phase` | Gauge | Duration/state of a specific metric within an AnalysisRun | Identify which metric provider is slow or failing | `name`, `namespace`, `metric`, `phase` |
| `analysis_run_metric_type` | Gauge (1) | Type of metric provider used in the AnalysisRun | Audit which providers are in use (prometheus/web/job/etc.) | `name`, `namespace`, `metric`, `type` |
| `analysis_run_reconcile_bucket` | Histogram (bucket) | Distribution of AnalysisRun reconciliation duration | Detect analysis controller saturation | `namespace`, `name`, `le` |
| `analysis_run_reconcile_count` | Counter | Total AnalysisRun reconciliations | Rate of analysis processing | `namespace`, `name` |
| `analysis_run_reconcile_sum` | Counter (seconds) | Cumulative AnalysisRun reconciliation time | Average cost per analysis reconcile | `namespace`, `name` |
| `analysis_run_reconcile_error` | Counter | AnalysisRun reconciliation errors | Provider timeout, RBAC issues, metric query failures | `namespace`, `name` |

---

## 3. Experiment Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `experiment_info` | Gauge (1) | Existence and metadata of an Experiment | Enumerate experiments; detect orphaned/stuck experiments | `name`, `namespace`, `phase` |
| `experiment_phase` | Gauge (1) | Current phase of the Experiment | Detect stuck experiments blocking rollout | `name`, `namespace`, `phase` (Pending/Running/Successful/Failed/Error) |
| `experiment_reconcile_bucket` | Histogram (bucket) | Distribution of Experiment reconciliation duration | Experiment controller saturation | `namespace`, `name`, `le` |
| `experiment_reconcile_count` | Counter | Total Experiment reconciliations | Rate of experiment processing | `namespace`, `name` |
| `experiment_reconcile_sum` | Counter (seconds) | Cumulative Experiment reconciliation time | Average cost per experiment reconcile | `namespace`, `name` |
| `experiment_reconcile_error` | Counter | Experiment reconciliation errors | Template failures, scheduling issues | `namespace`, `name` |

---

## 4. Notification Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `notification_send_success_total` | Counter | Successful notification deliveries | Baseline rate; drop = delivery path broken | `name`, `namespace`, `type` |
| `notification_send_error_total` | Counter | Failed notification deliveries | Non-zero = Slack/webhook/email integration broken | `name`, `namespace`, `type` |
| `notification_send_bucket` | Histogram (bucket) | Distribution of notification send duration | Detect slow notification backends (Slack API throttle) | `namespace`, `name`, `le` |
| `notification_send_count` | Counter | Total notification send attempts | Rate of notification activity | `namespace`, `name` |
| `notification_send_sum` | Counter (seconds) | Cumulative notification send time | Average send latency = sum/count | `namespace`, `name` |

---

## 5. Controller Health Metrics

### Kubernetes API Client

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_clientset_k8s_request_total` | Counter | Total K8s API requests made by the controller during reconciliation | High rate = controller making too many API calls (scaling/efficiency issue); correlate with API server latency | `kind`, `verb`, `status_code` |

### Work Queue (client-go workqueue)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_adds_total` | Counter | Total items added to the work queue | Rate = incoming reconciliation demand; spike = mass rollout activity or thrashing | `name` (queue: rollouts/experiments/analysisruns) |
| `workqueue_depth` | Gauge | Current queue depth (items waiting to be processed) | Sustained >0 = controller can't keep up; scaling or performance issue | `name` |
| `workqueue_queue_duration_seconds_bucket` | Histogram | Time items wait in queue before processing | p99 > 10s = controller overloaded; items waiting too long | `name`, `le` |
| `workqueue_work_duration_seconds_bucket` | Histogram | Time spent processing a single queue item | p99 > 5s = slow reconciliation; profile the specific rollout | `name`, `le` |
| `workqueue_unfinished_work_seconds` | Gauge | Seconds of work pending that hasn't completed | Rising = stuck goroutines or blocked reconciliation | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of the longest-running processor | >60s = likely stuck reconciliation | `name` |
| `workqueue_retries_total` | Counter | Total retry attempts in the queue | High rate = recurring failures (CRD conflicts, RBAC, API errors) | `name` |

### Build Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `build_info` | Gauge (1) | Argo Rollouts build metadata | Confirm deployed version matches expected | `version`, `goversion`, `goarch`, `commit` |

---

## 6. Go Runtime Metrics (client_golang)

The controller exposes standard Go runtime metrics via `prometheus/client_golang`
DefaultGatherer. See the `go-apm-metrics` skill for the complete reference.

Key metrics for this controller:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutines in the controller process | Leak detection (monotonic rise = goroutine leak) |
| `go_memstats_alloc_bytes` | Gauge | Current heap allocation | Memory pressure; correlate with OOMKill |
| `go_memstats_heap_inuse_bytes` | Gauge | Heap memory in use | Memory trend; compare with container limits |
| `go_gc_duration_seconds` | Summary | GC pause duration | High p99 = latency spikes during reconciliation |
| `process_resident_memory_bytes` | Gauge | RSS of the controller process | Compare with `resources.limits.memory` (256Mi) |
| `process_cpu_seconds_total` | Counter | CPU time consumed | Rate = CPU utilization; compare with `resources.limits.cpu` (200m) |

---

## 7. Rollout Events (counter)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rollout_events_total` | Counter | Total Kubernetes Events emitted by the rollouts controller | High rate = noisy rollout churn; correlate with specific event reasons | `type` (Normal/Warning), `reason` |

---

## Troubleshooting Quick-Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Rollout stuck in Progressing | `rollout_info{phase="Progressing"}` duration, `rollout_reconcile_error`, `analysis_run_info{phase="Running"}` (blocking analysis?) |
| Canary not advancing weight | `rollout_info_replicas_updated` vs `desired`, `analysis_run_info{phase!="Successful"}`, `rollout_reconcile_error` |
| Analysis Run failing | `analysis_run_info{phase="Failed"}`, `analysis_run_metric_phase`, `analysis_run_reconcile_error` |
| Controller slow/lagging | `workqueue_depth` (sustained >0), `workqueue_queue_duration_seconds` (p99), `rollout_reconcile_bucket` (p99 > 5s) |
| Controller OOMKilled | `process_resident_memory_bytes` trend vs 256Mi limit, `go_memstats_heap_inuse_bytes`, `go_goroutines` (leak?) |
| Notifications not delivered | `notification_send_error_total` rate, `notification_send_bucket` (p99 latency) |
| High API server load from controller | `controller_clientset_k8s_request_total` rate by `verb`/`kind`, `workqueue_retries_total` (excessive retries) |
| Rollout Degraded phase | `rollout_info{phase="Degraded"}`, `rollout_reconcile_error`, `rollout_info_replicas_available` < `desired` |
| Experiment stuck | `experiment_info{phase="Running"}` with old timestamps, `experiment_reconcile_error` |
| Queue processing stuck | `workqueue_longest_running_processor_seconds` > 60s, `workqueue_unfinished_work_seconds` rising |

---

## Deployment Context (<ORG> k8s-setup)

- **Chart**: `argo/argo-rollouts` version `2.40.9` (appVersion `v1.9.0`)
- **Namespace**: `argo`
- **Replicas**: 2 (controller)
- **Resources**: 200m CPU / 256Mi memory (requests=limits)
- **Traffic router plugin**: `argoproj-labs/gatewayAPI` (file-based plugin mount)
- **Dashboard**: enabled (separate deployment)
- **Deployed AnalysisTemplates** (via `argo-rollouts-raw`):
  - `success-rate` — HTTP success rate threshold
  - `error-rate` — HTTP error rate threshold
  - `p90-latency` / `p95-latency` / `p99-latency` — percentile latency gates
  - `avg-req-duration` — average request duration
  - `pod-restarts` — K8s pod restart count gate
  - `smoke-test` — HTTP smoke test (Job-based)

---

## Complements

- **`go-apm-metrics`** — full Go runtime metrics reference (goroutines, GC, scheduler, memory classes)
- **`collector-internal-metrics`** — if rollout analysis queries go through OTel Collector pipelines
- **`k8s-workload-metrics`** — container_* and kube_* metrics for the controller pods themselves
- **`argocd-patterns`** (skill) — ArgoCD sync that triggers Rollouts

---

## Sources

- [Argo Rollouts Controller Metrics (official docs)](https://github.com/argoproj/argo-rollouts/blob/master/docs/features/controller-metrics.md)
- [controller/metrics/metrics.go (source)](https://github.com/argoproj/argo-rollouts/blob/master/controller/metrics/metrics.go)
- Helm chart: `argo/argo-rollouts` 2.40.9 → appVersion v1.9.0
- Deployed values: `02-KUBE/00-CONFIG/k8s-setup/argo/argo-rollouts/values.yaml.gotmpl`
