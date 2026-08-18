---
name: kyverno-metrics
description: "Use when diagnosing Kyverno policy engine health \u2014 admission webhook latency, policy execution failures, controller reconciliation pressure, API server query volume, cleanup errors. Covers kyverno_policy_results, kyverno_admission_requests_total, kyverno_admission_review_duration_seconds, kyverno_policy_execution_duration_seconds, kyverno_controller_reconcile_total, kyverno_client_queries_total, kyverno_cleanup_controller_*, kyverno_http_requests_*, plus go_*. Grounded on Helm chart kyverno/kyverno 3.6.2 (appVersion v1.13.x), official docs https://release-1-13-0.kyverno.io/docs/monitoring/ and"
keywords: [kyverno]
---
# Kyverno Policy Engine Metrics

Prometheus metrics for the **Kyverno** Kubernetes-native policy engine — admission controller,
background controller, cleanup controller, and reports controller.

**Question answered**: "Are policies being enforced correctly? Is admission latency healthy?
Are controllers keeping up with reconciliation? Is the API server being hammered?"

**Grounded on**: Helm chart `kyverno/kyverno` **v3.6.2** (appVersion **v1.13.x**), deployed
in namespace `kyverno` across all clusters (core-devops, dev, prd).

---

## Scrape Pipeline

```
Kyverno controllers (:8000/metrics)
  ├── admission-controller (2 replicas)
  ├── background-controller (2 replicas)
  ├── cleanup-controller (2 replicas)
  └── reports-controller (2 replicas)
         │
         ▼
   ServiceMonitor (enabled per controller)
         │
         ▼
   vmagent scrape → VictoriaMetrics
```

**How metrics are enabled**: `metricsConfig.create: true` in Helm values creates
the `kyverno-metrics` ConfigMap. Each controller has `serviceMonitor.enabled: true`
and `metering.disabled: false`. Metrics served on port **8000** at `/metrics`.

**Metric customization**: the `kyverno-metrics` ConfigMap supports per-metric
config — disable metrics, drop label dimensions, change histogram buckets.
See `metricsExposure` section in values.

---

## 1. Policy & Rule Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_policy_rule_info_total` | Gauge | Active policy rules in the cluster (value=1 per rule) | Count total policies/rules; detect policy readiness issues (`status_ready="false"`) | `policy_name`, `policy_namespace`, `policy_type`, `policy_validation_mode`, `policy_background_mode`, `rule_name`, `rule_type`, `status_ready` |
| `kyverno_policy_results` | Counter | Policy rule executions (pass/fail) per admission or background scan | **Primary enforcement signal** — rate of failures = policy violations; spike in `rule_result="FAIL"` + `policy_validation_mode="enforce"` = blocked deployments | `policy_name`, `policy_namespace`, `policy_type`, `policy_validation_mode`, `policy_background_mode`, `resource_kind`, `resource_namespace`, `resource_request_operation`, `rule_name`, `rule_result`, `rule_type`, `rule_execution_cause` |
| `kyverno_policy_execution_duration_seconds` | Histogram | Latency of individual rule execution | Identify slow rules; p99 > 1s = blocking admission; correlate with `resource_kind` | `policy_name`, `policy_namespace`, `policy_type`, `policy_validation_mode`, `policy_background_mode`, `resource_kind`, `resource_namespace`, `resource_request_operation`, `rule_name`, `rule_result`, `rule_type`, `rule_execution_cause` |
| `kyverno_policy_changes_total` | Counter | Policy lifecycle events (create/update/delete) | Track policy churn; unexpected deletes = potential security gap | `policy_name`, `policy_namespace`, `policy_type`, `policy_validation_mode`, `policy_background_mode`, `policy_change_type` |

> **Note on `kyverno_policy_results`**: In Prometheus scrape convention, this counter
> appears as `kyverno_policy_results_total` in VictoriaMetrics (Prometheus adds `_total`
> suffix to counters automatically). Query with either form.

---

## 2. Admission Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_admission_requests_total` | Counter | Total admission requests received by Kyverno webhooks | Throughput baseline; drop = webhook misconfigured or apiserver not routing | `request_allowed`, `request_webhook`, `resource_kind`, `resource_namespace`, `resource_request_operation` |
| `kyverno_admission_review_duration_seconds` | Histogram | End-to-end latency of admission review processing | **Critical latency signal** — p99 > 3s risks apiserver timeout (30s default); correlate with `resource_kind` to find hot paths | `request_allowed`, `request_webhook`, `resource_kind`, `resource_namespace`, `resource_request_operation` |

---

## 3. HTTP Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_http_requests_total` | Counter | HTTP requests to Kyverno webhook endpoints | Total request volume; detect if apiserver is hammering Kyverno | `http_method`, `http_url` |
| `kyverno_http_requests_duration_seconds` | Histogram | HTTP request processing latency | Lower-level than admission review; includes non-webhook HTTP (healthz, metrics) | `http_method`, `http_url` |

---

## 4. Controller Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_controller_reconcile_total` | Counter | Total reconciliations performed per controller | Rate = controller activity; spike = event storm or resource churn | `controller_name` |
| `kyverno_controller_requeue_total` | Counter | Items requeued for retry | Rising rate = conflicts or transient failures; check `num_requeues` for repeat offenders | `controller_name`, `num_requeues` |
| `kyverno_controller_drop_total` | Counter | Items permanently dropped (max retries exceeded) | **Data loss signal** — any non-zero rate = policy not being enforced for that resource | `controller_name` |

---

## 5. Cleanup Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_cleanup_controller_deletedobjects_total` | Counter | Objects deleted by cleanup policies | Track cleanup throughput per policy | `policy_name`, `policy_namespace`, `policy_type`, `resource_kind`, `resource_namespace` |
| `kyverno_cleanup_controller_errors_total` | Counter | Errors during cleanup operations | Non-zero = cleanup failing (RBAC? resource protected? finalizer?) | `policy_name`, `policy_namespace`, `policy_type`, `resource_kind`, `resource_namespace` |
| `kyverno_ttl_controller_deletedobjects` | Counter | Objects deleted by TTL-based cleanup | Monitor auto-cleanup of time-limited resources | `resource_group`, `resource_version`, `resource_resource`, `resource_namespace` |
| `kyverno_ttl_controller_errors` | Counter | TTL cleanup errors | Non-zero = TTL cleanup broken for a resource type | `resource_group`, `resource_version`, `resource_resource`, `resource_namespace` |

---

## 6. API Client Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_client_queries_total` | Counter | Kubernetes API queries made by Kyverno | **API pressure signal** — high rate of `list`/`watch` = potential apiserver saturation; track by `client_type` and `operation` | `client_type`, `operation`, `resource_kind`, `resource_namespace` |

`client_type` values: `dynamic`, `kubeclient`, `kyverno`, `policyreport`.
`operation` values: `create`, `get`, `list`, `update`, `update_status`, `delete`, `delete_collection`, `watch`, `patch`.

---

## 7. Informational

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kyverno_info` | Gauge | Constant 1 with version label | Verify running version; detect mixed versions during rolling updates | `version` |

---

## 8. Go Runtime Metrics

All Kyverno controllers expose standard `go_*` metrics from `client_golang`. See
skill `go-apm-metrics` for full reference. Key ones for Kyverno:

| Metric Name | Troubleshooting Use |
|---|---|
| `go_goroutines` | Goroutine leak in controller loops |
| `go_memstats_alloc_bytes` | Memory pressure (correlate with OOM restarts) |
| `go_gc_duration_seconds` | GC pauses impacting admission latency |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Query Example |
|---------|------------------------|---------------|
| Deployments blocked / rejected | `kyverno_policy_results{rule_result="FAIL",policy_validation_mode="enforce"}` | `sum(rate(kyverno_policy_results{rule_result="FAIL",policy_validation_mode="enforce"}[5m])) by (policy_name, rule_name)` |
| Slow pod creation (apiserver latency) | `kyverno_admission_review_duration_seconds` p99 | `histogram_quantile(0.99, sum(rate(kyverno_admission_review_duration_seconds_bucket[5m])) by (le, resource_kind))` |
| Webhook timeout errors in apiserver | `kyverno_admission_requests_total{request_allowed="false"}` + admission review p99 | Check if p99 approaches 30s (apiserver default timeout) |
| Controller falling behind | `kyverno_controller_requeue_total` rate + `kyverno_controller_drop_total` | `rate(kyverno_controller_drop_total[5m]) > 0` = items permanently lost |
| API server pressure from Kyverno | `kyverno_client_queries_total` by operation | `sum(rate(kyverno_client_queries_total[5m])) by (operation, client_type)` |
| Cleanup not working | `kyverno_cleanup_controller_errors_total` | `rate(kyverno_cleanup_controller_errors_total[5m]) > 0` |
| Policy rule slow (single rule) | `kyverno_policy_execution_duration_seconds` | `histogram_quantile(0.99, sum(rate(kyverno_policy_execution_duration_seconds_bucket[5m])) by (le, policy_name, rule_name))` |
| Unknown Kyverno version running | `kyverno_info` | `kyverno_info` → check `version` label |
| Policy not being applied (missing) | `kyverno_policy_rule_info_total{status_ready="false"}` | Any result = policy not serving admission |

---

## Cardinality Notes

⚠️ **High-cardinality risk**: `kyverno_policy_results` and `kyverno_policy_execution_duration_seconds`
include `resource_namespace` and `resource_kind` labels. In clusters with many namespaces and
diverse resource types, series count can grow significantly.

**Mitigation** (via `kyverno-metrics` ConfigMap `metricsExposure`):
```yaml
metricsExposure:
  kyverno_policy_execution_duration_seconds:
    disabledLabelDimensions: ["resource_kind", "resource_namespace", "resource_request_operation"]
  kyverno_admission_requests:
    disabledLabelDimensions: ["resource_namespace", "resource_kind", "resource_request_operation"]
```

---

## Complements

- `go-apm-metrics` — Go runtime metrics (goroutines, GC, memory) emitted by all Kyverno controllers
- `k8s-workload-metrics` — Pod-level resource consumption (CPU/memory) of Kyverno pods
- `argocd-metrics` — ArgoCD sync health (Kyverno policies deployed via GitOps)

---

## Sources

- [Kyverno Metrics Reference (official)](https://kyverno.io/docs/reference/metrics/)
- [Kyverno v1.13 Monitoring Guide](https://release-1-13-0.kyverno.io/docs/monitoring/)
- Deployed chart: `kyverno/kyverno` v3.6.2 — `k8s-setup/kyverno/helmfile.yaml.gotmpl`
- Values: `k8s-setup/kyverno/kyverno/values.yaml.gotmpl` (ServiceMonitor enabled, metering enabled)
