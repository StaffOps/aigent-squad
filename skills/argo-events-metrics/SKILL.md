---
name: argo-events-metrics
description: >   Use when troubleshooting Argo Events event delivery, sensor trigger execution,   or controller reconciliation health. Covers argo_events_* (EventSource + Sensor   user metrics), controller_runtime_* (reconciliation), workqueue_* (queue depth/   latency), rest_client_* (K8s API calls), and go_* (runtime) emitted by the   controller-manager on port 7777. Grounded on Argo Events v1.9.10 (Helm chart   argo/argo-events 2.4.22).
keywords: [argo-events, argo, events, "argo events"]
---
# Argo Events Metrics Catalog

Prometheus metrics emitted by the **Argo Events** controllers and user workloads
(EventSource pods, Sensor pods), as deployed in this environment.

**Deployed version**: Argo Events **v1.9.10** via Helm chart `argo/argo-events`
**2.4.22** (namespace `argo`, `devops-core` cluster).

**Question answered**: "Are events flowing from sources through the EventBus to
sensor actions, or are they being lost/failing?"

---

## Scrape Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│ Controller metrics (port 7777)                                   │
│   controller-manager pod (eventsource-ctrl, sensor-ctrl,        │
│   eventbus-ctrl) → /metrics                                      │
│   Metrics: controller_runtime_*, workqueue_*, rest_client_*,    │
│            go_*                                                   │
└─────────────────────────────────────────────────────────────────┘
        │ ServiceMonitor (controller.metrics.serviceMonitor.enabled: true)
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ User metrics (per EventSource/Sensor pod)                        │
│   Each generated pod (label: controller in                       │
│   eventsource-controller,sensor-controller,eventbus-controller) │
│   exposes /metrics on its HTTP port                              │
│   Metrics: argo_events_*                                         │
└─────────────────────────────────────────────────────────────────┘
        │ Pod-discovery scrape (relabel by controller label)
        ▼
    vmagent  →  VictoriaMetrics (MetricsQL / PromQL)
```

### How Metrics Are Enabled

- **Controller metrics**: enabled via `controller.metrics.enabled: true` +
  `controller.metrics.serviceMonitor.enabled: true` in the Helm values.
  Port 7777 is the default controller metrics port.
- **User metrics**: each EventSource/Sensor pod exposes metrics automatically.
  Discovered via Kubernetes pod SD using label
  `controller in (eventsource-controller, sensor-controller, eventbus-controller)`.
- **EventBus (JetStream)**: NATS JetStream pods run a metrics sidecar
  (nats-exporter) on a separate port. Those expose `nats_*` / `jetstream_*`
  metrics — documented in the NATS exporter project, NOT covered in this skill.
  See [nats-io/prometheus-nats-exporter](https://github.com/nats-io/prometheus-nats-exporter).

---

## 1. EventSource Metrics (argo_events_*)

These metrics are emitted by each **EventSource pod** and report on event
generation and delivery to the EventBus.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_events_event_service_running_total` | Gauge | Number of configured event services actively running in this EventSource pod | Saturation signal — if lower than configured events, some sources failed to start | `event_source_name`, `event_name` |
| `argo_events_events_sent_total` | Counter | Events successfully sent to the EventBus | Traffic signal — rate = event throughput; drop = upstream source issue or EventBus unreachable | `event_source_name`, `event_name` |
| `argo_events_events_sent_failed_total` | Counter | Events that failed to send to EventBus | Error signal — non-zero rate = EventBus connectivity/capacity problem | `event_source_name`, `event_name` |
| `argo_events_events_processing_failed_total` | Counter | Events that failed processing for any reason (superset of sent_failed) | Broader error signal — includes transformation/filter failures before send | `event_source_name`, `event_name` |
| `argo_events_event_processing_duration_milliseconds` | Histogram | End-to-end processing time: receive event → send to EventBus (ms) | Latency signal — p99 spikes indicate EventBus backpressure or slow transformations | `event_source_name`, `event_name`, `le` |

---

## 2. Sensor Metrics (argo_events_*)

These metrics are emitted by each **Sensor pod** and report on trigger execution.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `argo_events_action_triggered_total` | Counter | Actions (triggers) successfully fired | Traffic signal — rate = how many workflows/resources are being created | `sensor_name`, `trigger_name` |
| `argo_events_action_failed_total` | Counter | Actions that failed to execute | Error signal — non-zero rate = trigger target unreachable or RBAC issue | `sensor_name`, `trigger_name` |
| `argo_events_action_retries_failed_total` | Counter | Actions that failed after ALL retries exhausted (also incremented if no retryStrategy is defined) | Critical error — event was received but action permanently failed | `sensor_name`, `trigger_name` |
| `argo_events_action_duration_milliseconds` | Histogram | Time to execute the trigger action (ms) | Latency signal — slow triggers can indicate downstream API saturation | `sensor_name`, `trigger_name`, `le` |

---

## 3. Controller Metrics (controller_runtime_*)

Exposed on port **7777** by the `controller-manager` pod. Standard
controller-runtime metrics for reconciliation loops (EventSource, Sensor,
EventBus controllers).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations per controller | Reconciliation throughput; sudden drop = controller stuck | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Reconciliation errors per controller | Non-zero rate = controller failing to converge resources | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Duration of each reconciliation | p99 > 5s = heavy objects or API server latency | `controller`, `le` |
| `controller_runtime_max_concurrent_reconciles` | Gauge | Max concurrent reconciles configured | Capacity ceiling — compare with `active_workers` | `controller` |
| `controller_runtime_active_workers` | Gauge | Currently busy reconcile workers | Saturation — if == max_concurrent → queue will grow | `controller` |

---

## 4. Workqueue Metrics (workqueue_*)

Client-go workqueue metrics from the controller-manager, per controller queue.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Current items waiting in the queue | Growing depth = reconciliation can't keep up | `name` |
| `workqueue_adds_total` | Counter | Total items added to queue | Rate = incoming reconciliation demand | `name` |
| `workqueue_queue_duration_seconds` | Histogram | Time an item waits in queue before processing | High p99 = queue saturation (items stale before processed) | `name`, `le` |
| `workqueue_work_duration_seconds` | Histogram | Time spent processing each item | High p99 = slow reconciliation logic | `name`, `le` |
| `workqueue_unfinished_work_seconds` | Gauge | Accumulated seconds of in-progress work not yet observed | Large + growing = stuck reconciliation threads | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of the longest-running processor | Single stuck item detection | `name` |
| `workqueue_retries_total` | Counter | Total queue retries | High rate = repeated reconciliation failures | `name` |

---

## 5. Kubernetes API Client Metrics (rest_client_*)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | HTTP requests to the Kubernetes API server | Rate by `code` — 429 = throttled, 5xx = API server issues | `code`, `method`, `host` |

---

## 6. Go Runtime Metrics (go_*)

Standard `client_golang` process metrics. See `go-apm-metrics` skill for the
full catalog. Key ones for Argo Events troubleshooting:

| Metric Name | Type | What It Measures | Troubleshooting Use |
|---|---|---|---|
| `go_goroutines` | Gauge | Live goroutines | Leak detection (monotonic rise) |
| `go_memstats_alloc_bytes` | Gauge | Heap bytes currently allocated | Memory pressure / OOM risk |
| `process_resident_memory_bytes` | Gauge | RSS of the process | Compare with container memory limit |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Next Steps |
|---------|------------------------|------------|
| Events not reaching Sensors | `rate(argo_events_events_sent_failed_total[5m])`, `argo_events_event_service_running_total` | Check EventBus health (NATS/JetStream pod logs), network policies |
| Sensor triggers not firing | `rate(argo_events_action_triggered_total[5m])` == 0, `argo_events_action_failed_total` | Check Sensor pod logs, RBAC for trigger target |
| Triggers failing permanently | `rate(argo_events_action_retries_failed_total[5m])` | Check trigger target availability, Sensor RBAC, resource quotas |
| Slow event processing | `histogram_quantile(0.99, rate(argo_events_event_processing_duration_milliseconds_bucket[5m]))` | Check EventBus latency, NATS JetStream consumer ack backlog |
| Controller not reconciling | `workqueue_depth` growing, `controller_runtime_active_workers` == `max_concurrent` | Scale controller replicas, check API server throttling (`rest_client_requests_total{code="429"}`) |
| Controller stuck | `workqueue_longest_running_processor_seconds` > 60, `workqueue_unfinished_work_seconds` growing | Check controller logs for blocked API calls, finalizer loops |
| EventSource pods not starting | `argo_events_event_service_running_total` < expected | Check controller reconcile errors: `controller_runtime_reconcile_errors_total` |
| API server pressure from controller | `rate(rest_client_requests_total{code="429"}[5m])` > 0 | Reduce concurrent reconciles, add rate limiting, check informer cache |

---

## MetricsQL Examples (Copy-Paste)

### Event delivery failure rate (last 5m)

```promql
sum by (event_source_name) (
  rate(argo_events_events_sent_failed_total[5m])
)
/
sum by (event_source_name) (
  rate(argo_events_events_sent_total[5m])
)
```

### Action (trigger) failure rate by sensor

```promql
sum by (sensor_name, trigger_name) (
  rate(argo_events_action_failed_total[5m])
)
/
sum by (sensor_name, trigger_name) (
  rate(argo_events_action_triggered_total[5m])
)
```

### Event processing latency p99

```promql
histogram_quantile(0.99,
  sum by (le, event_source_name) (
    rate(argo_events_event_processing_duration_milliseconds_bucket[5m])
  )
)
```

### Controller workqueue saturation

```promql
workqueue_depth{name=~"eventsource.*|sensor.*|eventbus.*"}
```

### Reconciliation error rate per controller

```promql
sum by (controller) (
  rate(controller_runtime_reconcile_errors_total[5m])
)
/
sum by (controller) (
  rate(controller_runtime_reconcile_total[5m])
)
```

---

## Golden Signals (per official Argo Events docs)

| Signal | Metrics |
|--------|---------|
| **Latency** | `argo_events_event_processing_duration_milliseconds`, `argo_events_action_duration_milliseconds` |
| **Traffic** | `argo_events_events_sent_total`, `argo_events_action_triggered_total` |
| **Errors** | `argo_events_events_processing_failed_total`, `argo_events_events_sent_failed_total`, `argo_events_action_failed_total`, `argo_events_action_retries_failed_total` |
| **Saturation** | `argo_events_event_service_running_total`, `workqueue_depth`, `controller_runtime_active_workers` |

---

## EventBus (JetStream) Note

The EventBus pods (NATS JetStream v2.10.10, deployed via `EventBus` CRD in
per-team namespaces like `dpm-events`) run a **metrics sidecar container**
that exposes NATS-native metrics (`nats_*`, `jetstream_*`). These are scraped
separately and are NOT `argo_events_*` prefixed. For NATS/JetStream metrics,
see the [prometheus-nats-exporter](https://github.com/nats-io/prometheus-nats-exporter)
documentation. Key ones relevant to Argo Events:

- `nats_jetstream_server_total_messages` — total messages in JetStream
- `nats_jetstream_server_total_bytes` — storage used
- `nats_jetstream_consumer_num_pending` — unconsumed messages (consumer lag)

---

## High-Cardinality Label Warnings

| Metric | Label | Risk | Mitigation |
|--------|-------|------|------------|
| `argo_events_events_sent_total` | `event_source_name` × `event_name` | Moderate — bounded by configured EventSources | Safe in practice; monitor if >100 unique event names |
| `argo_events_action_triggered_total` | `sensor_name` × `trigger_name` | Moderate — bounded by configured Sensors | Same |
| `workqueue_depth` | `name` | Low (3-5 controller queues) | Safe |
| `rest_client_requests_total` | `host` | Low (1-2 API server endpoints) | Safe |

---

## Version Notes

- Metrics confirmed from official Argo Events documentation:
  [argoproj.github.io/argo-events/metrics/](https://argoproj.github.io/argo-events/metrics/)
- Chart version: `argo/argo-events` **2.4.22** → appVersion **v1.9.10**
- Controller-runtime metrics from the kubebuilder/controller-runtime library
  used by the Argo Events controller-manager (Go-based operator).
- `argo_events_action_retries_failed_total` and `argo_events_action_duration_milliseconds`:
  added in Argo Events ≥v1.7; confirmed present in v1.9.x docs.
- Go runtime metrics (`go_*`, `process_*`) are standard `client_golang` collectors;
  see `go-apm-metrics` skill for the full reference.

---

## Complements

- `go-apm-metrics` — full Go runtime metrics catalog (`go_goroutines`,
  `go_memstats_*`, `go_gc_*`, `go_sched_*`)
- `collector-internal-metrics` — OTel Collector health (if events flow through
  OTel pipelines before reaching sensors)
- `k8s-workload-metrics` — container-level CPU/memory for EventSource/Sensor pods

---

## Sources

- [Argo Events — Prometheus Metrics (official docs)](https://argoproj.github.io/argo-events/metrics/)
- [Argo Events v1.9.10 release](https://github.com/argoproj/argo-events/releases/tag/v1.9.10)
- [Helm chart argo/argo-events 2.4.22 Chart.yaml](https://github.com/argoproj/argo-helm/blob/master/charts/argo-events/Chart.yaml)
- [controller-runtime default metrics reference](https://www.kubebuilder.io/reference/metrics-reference)
- [prometheus-nats-exporter (EventBus JetStream metrics)](https://github.com/nats-io/prometheus-nats-exporter)
- Deployed values: `02-KUBE/00-CONFIG/k8s-setup/argo/argo-events/values.yaml.gotmpl`
