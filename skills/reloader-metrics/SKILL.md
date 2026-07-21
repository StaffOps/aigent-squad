---
name: reloader-metrics
description: "Use when monitoring Stakater Reloader operational health — reload success/failure rates, per-namespace breakdown, RBAC problems causing patch failures. Covers reloader_reload_executed_total, reloader_reload_executed_total_by_namespace, plus standard Go runtime (go_*) metrics. Grounded on Helm chart stakater/reloader v2.1.5 (appVersion v1.4.5)."
keywords: [reloader, go, rbac]
---
# Stakater Reloader Metrics

Operational metrics for the **Stakater Reloader** controller — a Kubernetes controller
that watches ConfigMap/Secret changes and triggers rolling restarts of dependent workloads.

**Question answered**: "Is Reloader successfully restarting workloads when configs change,
or silently failing?"

**Scope**: Reloader's own `/metrics` endpoint on port 9090. This is a deliberately small
metric set — Reloader is a simple controller with only 2 application-specific metrics
(plus standard Go runtime metrics from `client_golang`).

---

## Scrape Pipeline

```
Reloader pod (:9090/metrics) → vmagent (via PodMonitor) → VictoriaMetrics
```

**How metrics are enabled** (from deployed `values.yaml.gotmpl`):

```yaml
reloader:
  enableMetricsByNamespace: true   # enables per-namespace breakdown metric
  serviceMonitor:
    enabled: true
  podMonitor:
    enabled: true
```

**Chart**: `stakater/reloader` v2.1.5  
**App version**: v1.4.5  
**Namespace**: `kube-system`  
**Replicas**: 2 (HA mode with `enableHA: true`)

---

## 1. Reloader Application Metrics

The entire application-specific metric surface consists of **two counters**. This is
intentional — Reloader does one thing (watch + patch) and reports success/failure.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `reloader_reload_executed_total` | Counter | Total reload attempts (workload patches) | **Primary health signal.** `success="false"` rising = RBAC problem or API server issues. Rate of `success="true"` = reload activity baseline. | `success` (`"true"` / `"false"`) |
| `reloader_reload_executed_total_by_namespace` | Counter | Same counter broken down by namespace | Identify which namespace has failing reloads; correlate with specific team's workloads. High cardinality in large clusters — enabled in this deployment via `enableMetricsByNamespace: true`. | `success`, `namespace` |

### Label values

| Label | Values | Notes |
|---|---|---|
| `success` | `"true"`, `"false"` | String, not boolean — quote in PromQL filters |
| `namespace` | Any workload namespace | Only on `_by_namespace` variant; unbounded cardinality |

---

## 2. Go Runtime Metrics (client_golang)

Reloader is a Go binary using `client_golang` — the standard `go_*` and `process_*`
metrics are exposed. For the **full reference** of these metrics see the
`go-apm-metrics` skill. Key ones for Reloader troubleshooting:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutine count | Leak detection if monotonically rising (Reloader watches many resources) | — |
| `go_memstats_alloc_bytes` | Gauge | Current heap allocation | Memory pressure; Reloader with many watched resources may grow | — |
| `go_memstats_sys_bytes` | Gauge | Total memory obtained from OS | OOM risk assessment (compare vs resource limits: 256Mi) | — |
| `process_resident_memory_bytes` | Gauge | RSS of the process | Compare against K8s memory limit to assess OOM proximity | — |
| `process_cpu_seconds_total` | Counter | Total CPU time consumed | Unexpected CPU spike = hot loop in reconciliation | — |

---

## 3. Controller-Runtime / Kubernetes Client Metrics

Reloader uses the Kubernetes client-go library which may expose `rest_client_*` and
`workqueue_*` metrics depending on the build. These are **not guaranteed present** in
v1.4.5 (Reloader uses a custom controller loop, not controller-runtime). If present:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | K8s API requests made by Reloader | High 4xx/5xx rate = RBAC or API server problem | `method`, `code` |
| `rest_client_request_duration_seconds_bucket` | Histogram | Latency of K8s API calls | Slow API = slow reload patching | `verb`, `url` |

> ⚠️ **Honesty note**: `rest_client_*` presence depends on whether the Reloader binary
> registers the client-go metrics collector. Confirm with a live scrape before building
> dashboards on these.

---

## Troubleshooting Quick Reference

| Symptom | First Metric to Check | What to Look For |
|---|---|---|
| Config/Secret changed but workload didn't restart | `reloader_reload_executed_total{success="false"}` | Non-zero rate → RBAC issue (Reloader can't PATCH the workload) |
| Reloader not detecting changes at all | `reloader_reload_executed_total` (both success values) | Zero rate for both → Reloader not watching (namespace filter, annotation missing, or pod down) |
| Specific namespace not getting reloads | `reloader_reload_executed_total_by_namespace{namespace="X"}` | Zero for target ns → check `ignoreNamespaces` config (`*-crons,*-qa` excluded) |
| Reloader pod OOMKilled | `process_resident_memory_bytes` / `go_memstats_sys_bytes` | Approaching 256Mi limit → increase memory limit or reduce watched scope |
| High API server load from Reloader | `rest_client_requests_total` (if present) | High request rate → too many resources watched, or too-frequent reconciliation |
| Reloader up but no `up` target in Prometheus | — | Check PodMonitor exists in `kube-system` and vmagent is selecting it |

---

## Key Configuration Affecting Metrics (Deployed)

| Setting | Value | Impact on Metrics |
|---|---|---|
| `enableMetricsByNamespace` | `true` | `_by_namespace` metric is emitted (cardinality = number of active namespaces) |
| `ignoreNamespaces` | `*-crons,*-qa` | These namespaces will never appear in metrics (no reloads attempted) |
| `reloadStrategy` | `annotations` | Only annotated workloads are reloaded — unannotated changes won't produce metric events |
| `enableHA` | `true` | Leader election active — only leader pod emits reload metrics; follower is idle |
| `watchGlobally` | `true` | All namespaces watched (except ignored ones) |
| `replicas` | `2` | With HA, metrics come from leader only; `sum()` across pods is safe |

---

## Example PromQL Queries

```promql
# Reload failure rate (alert candidate)
sum(rate(reloader_reload_executed_total{success="false"}[5m])) > 0

# Total successful reloads per hour
sum(increase(reloader_reload_executed_total{success="true"}[1h]))

# Top namespaces by reload activity
sort_desc(
  sum by (namespace) (
    increase(reloader_reload_executed_total_by_namespace{success="true"}[1h])
  )
)

# Failure ratio (should be 0)
sum(rate(reloader_reload_executed_total{success="false"}[5m]))
/
sum(rate(reloader_reload_executed_total[5m]))
```

---

## Complements

- `go-apm-metrics` — full Go runtime metrics reference (`go_*`, `process_*`)
- `k8s-workload-metrics` — pod-level resource usage (CPU/memory limits vs actual)
- `collector-internal-metrics` — if OTel Collector scrapes Reloader (pipeline health)

## Sources

- [Stakater Reloader — Monitor Reloader](https://docs.stakater.com/reloader/latest/how-to-guides/monitor-reloader.html) (official docs)
- [GitHub stakater/Reloader chart-v2.1.5](https://github.com/stakater/Reloader/tree/chart-v2.1.5) (Chart.yaml: appVersion v1.4.5)
- Deployed helmfile: `02-KUBE/00-CONFIG/k8s-setup/stakater/helmfile.yaml.gotmpl` (version 2.1.5)
- Deployed values: `02-KUBE/00-CONFIG/k8s-setup/stakater/reloader/values.yaml.gotmpl`
