---
name: crossplane-metrics
description: "Use when diagnosing Crossplane control-plane health — reconciliation failures, managed resource readiness drift, workqueue saturation, API server request pressure, or provider cloud API latency. Covers controller_runtime_reconcile_*, controller_runtime_active_workers, workqueue_*, rest_client_requests_total, crossplane_managed_resource_*, plus go_*. Grounded on Helm chart crossplane-stable/crossplane 1.19.0 with metrics.enabled=true and crossplane-contrib/provider-aws-* v1.21.1."
keywords: [crossplane]
---
# Crossplane Metrics

Prometheus metrics for **Crossplane core** (composition engine, package manager) and
**Crossplane providers** (managed resource controllers).

**Question answered**: "Are my Crossplane-managed resources reconciling successfully,
and is the control plane healthy?"

**Scope**: Crossplane pod + RBAC manager pod + provider pods, as scraped into
VictoriaMetrics by vmagent via Prometheus annotations.

---

## Scrape Pipeline

```
Crossplane pod (:8080/metrics)       ─┐
RBAC manager pod (:8080/metrics)      │──→ vmagent (scrape) ──→ VictoriaMetrics
Provider pods (:8080/metrics each)   ─┘
```

### How metrics are enabled

Helm chart value (deployed config confirms `metrics.enabled: true`):

```yaml
metrics:
  enabled: true
```

This adds Prometheus annotations to all Crossplane-managed pods:

```yaml
prometheus.io/path: /metrics
prometheus.io/port: "8080"
prometheus.io/scrape: "true"
```

Each **provider** (provider-aws-ec2, provider-aws-s3, provider-aws-sqs) runs as a
separate Deployment exposing its own `:8080/metrics` endpoint. vmagent discovers
all via annotation-based scraping.

### Deployed versions

| Component | Version |
|-----------|---------|
| Crossplane core chart | `crossplane-stable/crossplane` **1.19.0** |
| provider-aws-ec2 | `crossplane-contrib/provider-aws-ec2:v1.21.1` |
| provider-aws-s3 | `crossplane-contrib/provider-aws-s3:v1.21.1` |
| provider-aws-sqs | `crossplane-contrib/provider-aws-sqs:v1.21.1` |

> **Note on provider type**: The deployed providers are `crossplane-contrib/provider-aws-*`
> (native Go, NOT Upjet-based). Therefore `upjet_*` metrics are **NOT present**.
> The `crossplane_managed_resource_*` metrics ARE emitted by these providers
> (they use crossplane-runtime).

---

## Crossplane-Specific Provider Metrics

These metrics are emitted by **each provider pod** using crossplane-runtime. They
give visibility into managed resource lifecycle.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `crossplane_managed_resource_exists` | Gauge | Number of managed resources that exist | Baseline inventory; unexpected drop = deletion | `gvk` |
| `crossplane_managed_resource_ready` | Gauge | Number of managed resources in `Ready=True` | **KEY** — gap between `exists` and `ready` = unhealthy resources | `gvk` |
| `crossplane_managed_resource_synced` | Gauge | Number of managed resources in `Synced=True` | Gap between `exists` and `synced` = drift or reconcile failure | `gvk` |
| `crossplane_managed_resource_deletion_seconds` | Histogram | Time to delete a managed resource | Slow deletion = cloud API latency or finalizer stuck | `gvk`, `le` |
| `crossplane_managed_resource_first_time_to_readiness_seconds` | Histogram | Time from creation to first `Ready=True` | Tracks provisioning speed; p99 regression = cloud API degradation | `gvk`, `le` |
| `crossplane_managed_resource_first_time_to_reconcile_seconds` | Histogram | Time for controller to detect new resource | High values = controller overloaded or workqueue saturated | `gvk`, `le` |
| `crossplane_managed_resource_drift_seconds` | Histogram | Time since last successful reconcile when drift detected | Measures how long resources stayed out of sync before re-reconcile | `gvk`, `le` |

> ⚠️ **v1.19 note**: `crossplane_managed_resource_drift_seconds` was introduced
> in crossplane-runtime v1.16+. Confirm presence in live inventory for your
> provider version.

---

## Controller-Runtime Metrics (All Crossplane Pods)

Both Crossplane core and every provider emit these standard controller-runtime
metrics. These are the **primary operational health indicators**.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations per controller | Baseline reconcile throughput; drop = controller stuck | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Total reconciliation errors | **KEY** — rising rate = systematic failure (permissions, cloud API, schema) | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Time per reconciliation | p99 > 30s = slow cloud API calls or complex composition | `controller`, `le` |
| `controller_runtime_active_workers` | Gauge | Active reconcile workers per controller | Saturation signal: active == max_concurrent = bottleneck | `controller` |
| `controller_runtime_max_concurrent_reconciles` | Gauge | Max concurrent reconciles (configured parallelism) | Reference ceiling for `active_workers` comparison | `controller` |

### Webhook Metrics (Crossplane core)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_webhook_requests_total` | Counter | Total admission webhook requests | High error codes = validation/conversion webhook failures | `code` |
| `controller_runtime_webhook_latency_seconds` | Histogram | Admission request processing time | High p99 = slow composition validation blocking kubectl apply | `le` |
| `controller_runtime_webhook_requests_in_flight` | Gauge | Current in-flight admission requests | Saturation signal for webhook processing | — |

---

## Workqueue Metrics (All Crossplane Pods)

Standard controller-runtime workqueue metrics. Each controller has its own queue.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Items waiting in queue | **KEY** — rising depth = controller can't keep up with changes | `name` |
| `workqueue_adds_total` | Counter | Total items enqueued | Rate of incoming work; spike = mass change or watch storm | `name` |
| `workqueue_queue_duration_seconds` | Histogram | Time item waits in queue before processing | High p99 = processing backlog (reconcile too slow or parallelism too low) | `name`, `le` |
| `workqueue_work_duration_seconds` | Histogram | Time to process one item | Slow processing = heavy cloud API calls or composition render | `name`, `le` |
| `workqueue_retries_total` | Counter | Total retries | High retry rate = transient errors (throttling, timeouts) | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of longest-running processor | Stuck processor detection (>60s is suspicious) | `name` |
| `workqueue_unfinished_work_seconds` | Gauge | Cumulative seconds of unfinished work | Large value = stuck threads; investigate with longest_running | `name` |

---

## Kubernetes Client Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | HTTP requests to Kubernetes API server | Throttling detection (429s), auth failures (403), not-found (404) | `code`, `host`, `method` |

---

## Go Runtime Metrics

Standard `go_*` and `process_*` metrics from client_golang. See `go-apm-metrics`
skill for full reference.

| Metric Name | Type | Troubleshooting Use |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak in provider (monotonic rise) |
| `go_memstats_alloc_bytes` | Gauge | Memory pressure / OOM risk |
| `process_resident_memory_bytes` | Gauge | RSS — compare to container limits |
| `process_cpu_seconds_total` | Counter | CPU saturation |

---

## Metrics NOT Present in This Deployment

| Metric Prefix | Why Absent |
|---|---|
| `upjet_*` | Providers are native `crossplane-contrib`, not Upjet-based |
| `function_run_function_*` | Crossplane v2.x feature; v1.19 does not have function pipeline metrics |
| `circuit_breaker_*` | Crossplane v2.x feature; not present in v1.19 |
| `engine_controllers_*` / `engine_watches_*` | Crossplane v2.x feature |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | What to Look For |
|---------|------------------------|------------------|
| Resources stuck not-ready | `crossplane_managed_resource_ready` vs `_exists` | Gap between the two = failing resources |
| Reconciliation errors rising | `controller_runtime_reconcile_errors_total` by `controller` | Identify WHICH controller is failing |
| Slow resource provisioning | `crossplane_managed_resource_first_time_to_readiness_seconds` p99 | Cloud API degradation or throttling |
| Controller falling behind | `workqueue_depth` + `controller_runtime_active_workers` == `max_concurrent_reconciles` | Workers saturated; increase parallelism or fix slow reconciles |
| Queue item waiting too long | `workqueue_queue_duration_seconds` p99 > 30s | Backlog — check depth + active workers |
| Stuck reconcile | `workqueue_longest_running_processor_seconds` > 120s | Blocked cloud API call or finalizer deadlock |
| API server throttling | `rest_client_requests_total{code="429"}` | Crossplane hitting API server rate limits — reduce watch scope |
| Provider OOM | `process_resident_memory_bytes` vs container limit | Too many CRDs/watches; consider splitting providers |
| Webhook failures | `controller_runtime_webhook_requests_total{code=~"4..|5.."}` | Schema validation or conversion webhook broken |

### Key PromQL Queries

```promql
# Reconcile error rate per controller (5m window)
sum by (controller) (rate(controller_runtime_reconcile_errors_total{namespace="crossplane"}[5m]))

# Resources not ready (gap)
crossplane_managed_resource_exists - crossplane_managed_resource_ready

# Resources not synced (drift)
crossplane_managed_resource_exists - crossplane_managed_resource_synced

# Workqueue depth (all queues, crossplane namespace)
workqueue_depth{namespace="crossplane"}

# Worker saturation ratio per controller
controller_runtime_active_workers / controller_runtime_max_concurrent_reconciles

# Reconcile latency p99
histogram_quantile(0.99, sum by (controller, le) (rate(controller_runtime_reconcile_time_seconds_bucket{namespace="crossplane"}[5m])))

# API server error rate from Crossplane
sum by (code) (rate(rest_client_requests_total{namespace="crossplane", code=~"4..|5.."}[5m]))
```

---

## Provider-Specific Notes

Each provider runs as its own Deployment with separate metrics endpoint:

| Provider | Pod label | Key controllers (in `controller` label) |
|----------|-----------|----------------------------------------|
| provider-aws-ec2 v1.21.1 | `pkg.crossplane.io/revision=provider-aws-ec2-*` | `managed/instance.ec2.aws.crossplane.io`, `managed/vpc.ec2.aws.crossplane.io`, etc. |
| provider-aws-s3 v1.21.1 | `pkg.crossplane.io/revision=provider-aws-s3-*` | `managed/bucket.s3.aws.crossplane.io` |
| provider-aws-sqs v1.21.1 | `pkg.crossplane.io/revision=provider-aws-sqs-*` | `managed/queue.sqs.aws.crossplane.io` |

Filter by `namespace="crossplane"` and pod label to isolate per-provider metrics.

---

## Complements

- **go-apm-metrics** — Full Go runtime metrics reference (goroutines, GC, scheduler)
- **k8s-workload-metrics** — Container-level resource metrics (CPU/mem usage vs requests)
- **karpenter-metrics** — If Crossplane providers trigger node provisioning via managed resources

## Sources

- [Crossplane Official Metrics Documentation](https://docs.crossplane.io/v2.0-preview/guides/metrics/) (covers controller-runtime + crossplane-runtime metrics; v2 extras noted)
- [controller-runtime metrics](https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/metrics) — standard metric names
- Deployed Helm chart: `crossplane-stable/crossplane` version **1.19.0**
- Deployed providers: `crossplane-contrib/provider-aws-{ec2,s3,sqs}:v1.21.1`
- Deployed config: `metrics.enabled: true` (confirmed in `crossplane/values.yaml.gotmpl`)
