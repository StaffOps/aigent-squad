---
name: keda-metrics
description: >   Use when diagnosing KEDA autoscaling health — scaler failures, metric fetch   latency, ScaledObject/ScaledJob errors, paused objects, or scaling loop   saturation. Covers keda_scaler_*, keda_scaled_object_*, keda_scaled_job_*,   keda_resource_registered_total, keda_trigger_registered_total,   keda_internal_scale_loop_latency_seconds, keda_internal_metricsservice_grpc_*,   plus controller-runtime and go_* runtime metrics. Grounded on Helm chart   kedacore/keda 2.18.0 (appVersion v2.18.0).
keywords: [keda]
---
# KEDA Operator & Metrics Server Self-Metrics

Prometheus metrics emitted by the **KEDA Operator** and the **KEDA Metrics Server**
(metrics-apiserver) for monitoring event-driven autoscaling health.

**Question answered**: "Are my ScaledObjects scaling correctly, or are scalers
silently failing / timing out?"

**Scope**: KEDA Operator scaling loop metrics + Metrics Server gRPC client
metrics, as scraped into VictoriaMetrics by vmagent via ServiceMonitor.

---

## Scrape Pipeline

```
KEDA Operator (:8080/metrics) ──────────┐
KEDA Metrics Server (:8080/metrics) ────┤──→ vmagent (ServiceMonitor) ──→ VictoriaMetrics
KEDA Webhooks (:8080/metrics) ──────────┘
```

**Enabled via Helm values** (deployed config):
```yaml
prometheus:
  operator:
    enabled: true
    serviceMonitor:
      enabled: true
    podMonitor:
      enabled: true
  metricServer:
    enabled: true
    serviceMonitor:
      enabled: true
    podMonitor:
      enabled: true
  webhooks:
    enabled: true
    serviceMonitor:
      enabled: true
```

All three components expose `/metrics` on port 8080. ServiceMonitors are created
by the chart for Prometheus-operator (vmagent) auto-discovery.

---

## Deployed Version

| Component | Chart | Version | App Version |
|-----------|-------|---------|-------------|
| KEDA | `kedacore/keda` | 2.18.0 | v2.18.0 |

> ⚠️ **Naming history**: Prior to KEDA v2.10, metrics lived on the Metrics Server
> with prefix `keda_metrics_adapter_*`. Since v2.10+ the Operator is the primary
> source and uses the `keda_*` prefix. The local `prometheus.rules.yaml` still
> references the **legacy** name `keda_metrics_adapter_scaler_errors` — this alert
> rule should be migrated to `keda_scaler_detail_errors_total` to match the
> deployed version.

---

## 1. Operator — Scaler Health

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_scaler_active` | Gauge | Whether a scaler is active (1) or inactive (0) | Detect scalers stuck inactive — workload won't scale from zero | `scaledObject`, `scaler`, `namespace`, `metric` |
| `keda_scaler_metrics_value` | Gauge | Current metric value used by HPA for target average | Validate the value KEDA feeds to HPA matches expectations | `scaledObject`, `scaler`, `namespace`, `metric` |
| `keda_scaler_metrics_latency_seconds` | Gauge | Latency (seconds) of fetching metric from scaler source | High latency = slow scaling decisions; may hit polling timeout | `scaledObject`, `scaler`, `namespace`, `metric` |
| `keda_scaler_detail_errors_total` | Counter | Errors encountered per scaler | Non-zero rate = scaler can't reach source (Prometheus, SQS, Kafka, etc.) | `scaledObject`, `scaler`, `namespace`, `metric` |
| `keda_scaled_object_paused` | Gauge | Whether a ScaledObject is paused (1) or active (0) | Detect objects accidentally left paused — no scaling occurring | `scaledObject`, `namespace` |

## 2. Operator — ScaledObject / ScaledJob Errors

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_scaled_object_errors_total` | Counter | Errors for a ScaledObject (reconcile failures) | Rising rate = KEDA can't manage this ScaledObject (spec error, auth failure) | `scaledObject`, `namespace` |
| `keda_scaled_job_errors_total` | Counter | Errors for a ScaledJob | Same as above but for job-based scalers | `scaledJob`, `namespace` |

## 3. Operator — Resource & Trigger Inventory

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_resource_registered_total` | Gauge | Total KEDA CRs per namespace per type | Inventory: how many ScaledObjects/Jobs/TriggerAuths exist | `namespace`, `resource_type` |
| `keda_trigger_registered_total` | Gauge | Total triggers per trigger type | Inventory: which trigger types are most used (prometheus, aws-sqs, kafka, etc.) | `trigger_type` |

## 4. Operator — Internal Scaling Loop

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_internal_scale_loop_latency_seconds` | Histogram | Deviation between expected and actual scaling loop execution | High values = operator overloaded / too many scalers per loop | `type` (scaledobject \| scaledjob) |
| `keda_build_info` | Gauge (info) | Static build metadata (version, git commit, Go version) | Sentinel metric: if absent, operator is not running / not scraped | `version`, `git_commit`, `goversion` |

## 5. Operator — CloudEvents

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_cloudeventsource_events_emitted_total` | Counter | CloudEvents emitted to configured sinks | Validate event delivery to external systems | `eventsink`, `state` |
| `keda_cloudeventsource_events_queued` | Gauge | Events waiting in emit queue | Growing queue = sink unreachable / slow | — |

## 6. Operator — Internal gRPC Metrics Service (Server-Side)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_internal_metricsservice_grpc_server_started_total` | Counter | RPCs started on the operator's internal gRPC server | Baseline throughput of metrics requests from the metrics-server | `grpc_type`, `grpc_service`, `grpc_method` |
| `keda_internal_metricsservice_grpc_server_handled_total` | Counter | RPCs completed (success or failure) | Compare with `started` to detect stuck RPCs | `grpc_type`, `grpc_service`, `grpc_method`, `grpc_code` |
| `keda_internal_metricsservice_grpc_server_handling_seconds` | Histogram | Response latency for gRPC handled by operator | p99 > 1s = operator can't serve metrics fast enough for HPA | `grpc_type`, `grpc_service`, `grpc_method`, `le` |
| `keda_internal_metricsservice_grpc_server_msg_received_total` | Counter | Stream messages received | — | `grpc_type`, `grpc_service`, `grpc_method` |
| `keda_internal_metricsservice_grpc_server_msg_sent_total` | Counter | Stream messages sent | — | `grpc_type`, `grpc_service`, `grpc_method` |

## 7. Metrics Server — Internal gRPC (Client-Side)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_internal_metricsservice_grpc_client_started_total` | Counter | RPCs started by metrics-server to operator | Client-side view of metrics fetching | `grpc_type`, `grpc_service`, `grpc_method` |
| `keda_internal_metricsservice_grpc_client_handled_total` | Counter | RPCs completed by the client | `grpc_code != OK` = communication failure with operator | `grpc_type`, `grpc_service`, `grpc_method`, `grpc_code` |
| `keda_internal_metricsservice_grpc_client_handling_seconds` | Histogram | Client-perceived latency for gRPC calls to operator | End-to-end latency including network | `grpc_type`, `grpc_service`, `grpc_method`, `le` |
| `keda_internal_metricsservice_grpc_client_msg_received_total` | Counter | Stream messages received by client | — | `grpc_type`, `grpc_service`, `grpc_method` |
| `keda_internal_metricsservice_grpc_client_msg_sent_total` | Counter | Stream messages sent by client | — | `grpc_type`, `grpc_service`, `grpc_method` |

## 8. Admission Webhooks

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `keda_webhook_scaled_object_validation_total` | Counter | ScaledObject validation attempts | Baseline webhook activity | — |
| `keda_webhook_scaled_object_validation_errors` | Gauge | Validation errors count | Non-zero = users submitting invalid ScaledObject specs | — |

## 9. Controller-Runtime / Go Runtime (Standard)

Both operator and metrics-server expose standard `controller-runtime` and
`client_golang` metrics. See the `go-apm-metrics` skill for the full Go runtime
catalog. Key ones for KEDA:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations by controller | `result=error` rising = controller failing to reconcile CRs |
| `controller_runtime_reconcile_errors_total` | Counter | Reconciliation errors | Non-zero rate = operator has issues processing KEDA CRDs |
| `controller_runtime_reconcile_time_seconds` | Histogram | Reconciliation duration | p99 > 10s = operator overloaded or external call slow |
| `workqueue_adds_total` | Counter | Items added to work queue | Spike = burst of CR changes |
| `workqueue_depth` | Gauge | Current work queue depth | Sustained > 0 = operator can't keep up |
| `workqueue_longest_running_processor_seconds` | Gauge | Longest active item processing time | Stuck item = investigate that CR |

---

## Troubleshooting Quick-Reference

| Symptom | First Query | Likely Cause | Next Step |
|---------|-------------|--------------|-----------|
| ScaledObject not scaling | `keda_scaler_active{scaledObject="X"} == 0` | Scaler inactive — metric below threshold or source unreachable | Check `keda_scaler_detail_errors_total` for that scaler |
| Scaler errors rising | `rate(keda_scaler_detail_errors_total[5m]) > 0` | Can't reach trigger source (creds expired, endpoint down) | Check operator logs for source-specific error (auth, timeout, DNS) |
| ScaledObject errors | `rate(keda_scaled_object_errors_total{scaledObject="X"}[5m]) > 0` | Reconciliation failure (bad spec, missing TriggerAuth) | `kubectl describe scaledobject X` + operator logs |
| HPA not receiving metrics | `keda_internal_metricsservice_grpc_client_handled_total{grpc_code!="OK"}` | gRPC comm failure between metrics-server and operator | Check operator pod health, network policies, cert-manager certs |
| Scaling decisions delayed | `histogram_quantile(0.99, rate(keda_internal_scale_loop_latency_seconds_bucket[5m]))` > 5s | Too many scalers, slow source queries | Reduce pollingInterval, optimize scaler queries |
| ScaledObject accidentally paused | `keda_scaled_object_paused == 1` | Manual pause or admission webhook auto-pause on error | `kubectl annotate scaledobject X autoscaling.keda.sh/paused-replicas-` |
| Metrics value looks wrong | `keda_scaler_metrics_value{scaledObject="X"}` | Scaler query returns unexpected value | Test the trigger source query manually (e.g., PromQL, SQS count) |
| Operator overwhelmed | `workqueue_depth > 10` sustained | Too many ScaledObjects or slow reconciliation | Scale operator replicas, check `controller_runtime_reconcile_time_seconds` p99 |

---

## MetricsQL Examples (Copy-Paste)

### Scaler error rate by ScaledObject (last 5m)

```promql
sum by (scaledObject, scaler) (
  rate(keda_scaler_detail_errors_total[5m])
) > 0
```

### ScaledObject error rate by namespace

```promql
sum by (namespace, scaledObject) (
  rate(keda_scaled_object_errors_total[5m])
) > 0
```

### Scaling loop latency p99

```promql
histogram_quantile(0.99,
  sum by (le) (rate(keda_internal_scale_loop_latency_seconds_bucket[5m]))
)
```

### gRPC error rate between metrics-server and operator

```promql
sum(rate(keda_internal_metricsservice_grpc_client_handled_total{grpc_code!="OK"}[5m]))
/
sum(rate(keda_internal_metricsservice_grpc_client_handled_total[5m]))
```

### Inactive scalers (workloads that won't scale from zero)

```promql
keda_scaler_active == 0
```

### Metric fetch latency by scaler (top 10 slowest)

```promql
topk(10, keda_scaler_metrics_latency_seconds)
```

---

## Legacy Metric Name Migration

The local `prometheus.rules.yaml` uses the **deprecated** metric name from the
KEDA Metrics Adapter era:

```yaml
# DEPRECATED (pre-v2.10, metrics-adapter source)
expr: sum by (scaledObject, scaler) (rate(keda_metrics_adapter_scaler_errors[2m])) > 0
```

**Should be migrated to**:
```yaml
# CURRENT (v2.10+, operator source)
expr: sum by (scaledObject, scaler) (rate(keda_scaler_detail_errors_total[2m])) > 0
```

The `keda_metrics_adapter_*` metrics may still be emitted by the metrics-server
component for backward compatibility but are officially deprecated since KEDA v2.9.
The Operator is now the authoritative source for all scaler metrics.

### Full naming migration reference

| Legacy (metrics-adapter) | Current (operator, v2.10+) |
|---|---|
| `keda_metrics_adapter_scaler_errors` | `keda_scaler_detail_errors_total` |
| `keda_metrics_adapter_scaled_object_errors` | `keda_scaled_object_errors_total` |
| `keda_metrics_adapter_scaler_metrics_value` | `keda_scaler_metrics_value` |
| `keda_metrics_adapter_scaler_error_totals` | `keda_scaler_detail_errors_total` |
| `keda_metrics_adapter_scaled_object_error_totals` | `keda_scaled_object_errors_total` |

---

## Version Notes

- **v2.18.0** adds `keda_scaler_metrics_latency_seconds` as the primary metric
  fetch latency indicator on the operator.
- **v2.18.0** does NOT expose `keda_scaler_errors_total` (the "aggregate" counter
  referenced in some community docs) — use `keda_scaler_detail_errors_total` which
  breaks down errors per-scaler.
- `keda_resource_registered_total` and `keda_trigger_registered_total` replaced
  the earlier `keda_resource_totals` / `keda_trigger_totals` naming in v2.12+.
- `keda_internal_scale_loop_latency_seconds` was added in v2.14 as a histogram.
- CloudEvent metrics (`keda_cloudeventsource_*`) are only populated if a
  `CloudEventSource` CR is deployed.

---

## Related Skills

- `go-apm-metrics` — Go runtime metrics (goroutines, GC, scheduler) for the
  KEDA operator and metrics-server Go processes
- `k8s-workload-metrics` — container CPU/memory for KEDA pods themselves
- `cert-manager-metrics` — KEDA uses cert-manager for webhook certificates
  (configured in the deployed values)

---

## Sources

- [KEDA Official Docs — Integrate with Prometheus (v2.18)](https://keda.sh/docs/2.18/integrations/prometheus)
- [GitHub Issue #3919 — Consolidate all Prometheus Metrics in KEDA Operator](https://github.com/kedacore/keda/issues/3919)
- [GitHub Issue #3972 — Metrics Server metrics deprecated](https://github.com/kedacore/keda/issues/3972)
- Deployed Helm values: `k8s-setup/keda/keda/values.yaml.gotmpl` (chart kedacore/keda 2.18.0)
- Deployed prometheus.rules: `k8s-setup/keda/keda/prometheus.rules.yaml`
