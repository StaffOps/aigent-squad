---
name: kiali-metrics
description: >   Use when diagnosing Kiali server health — API latency, API failures, graph   generation performance, Prometheus/tracing query duration, cache efficiency,   validation processing time, and Kubernetes client count. Covers kiali_api_*,   kiali_graph_*, kiali_prometheus_*, kiali_checker_*, kiali_cache_*,   kiali_tracing_*, kiali_kubernetes_clients. Grounded on Helm chart   kiali/kiali-server 2.17.0 (appVersion v2.17.0). For MESH traffic metrics   (istio_requests_total, etc.) see istio-ambient-metrics skill instead.
keywords: [kiali, prometheus, mesh]
---
# Kiali Server & Operator Self-Metrics

Self-telemetry emitted by the **Kiali server** (mesh observability UI) and the
**kiali-operator** (Ansible-based reconciler).

**Question answered**: "Is Kiali itself healthy — are its API calls, graph
renders, and backend queries performing within acceptable bounds?"

**Scope**: Kiali's OWN internal metrics exposed on `:9090/metrics`. This skill
does NOT cover the Istio/Envoy mesh traffic metrics that Kiali *consumes* — for
those, see `istio-ambient-metrics`.

> **Grounded on**: Helm chart `kiali/kiali-server` **2.17.0** + `kiali/kiali-operator`
> **2.17.0** (appVersion **v2.17.0**). Source:
> [`kiali/kiali@v2.17 prometheus/internalmetrics/internal_metrics.go`](https://github.com/kiali/kiali/blob/v2.17/prometheus/internalmetrics/internal_metrics.go).

---

## Scrape Pipeline

```
Kiali Server Pod (:9090/metrics)  →  vmagent scrape  →  VictoriaMetrics
Kiali Operator Pod (:8080/metrics) →  vmagent scrape  →  VictoriaMetrics
```

**How enabled**:
- Server: `server.observability.metrics.enabled: true` + `server.observability.metrics.port: 9090`
  in the kiali-server CR/values (confirmed in deployed values).
- Operator: `metrics.enabled: true` in the kiali-operator Helm values.

Both expose a standard `/metrics` endpoint scraped by vmagent via
`VMServiceScrape` or pod annotations.

---

## Kiali Server Metrics

### API Performance (RED)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_api_processing_duration_seconds` | Histogram | Time to execute a REST API route request | **Primary latency signal** — high p99 per route = slow backend queries or graph generation | `route` |
| `kiali_api_failures_total` | Counter | Total failures encountered by API handlers | **Error rate** — non-zero rate on a route = broken functionality visible to users | `route` |

### Graph Generation Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_graph_generation_duration_seconds` | Histogram | Time to generate a service/workload/app graph | Slow graph renders; compare across graph types | `graph_kind`, `graph_type`, `with_service_nodes` |
| `kiali_graph_appender_duration_seconds` | Histogram | Time for each graph appender stage | Identify which appender is the bottleneck (security, health, istio, response time) | `appender` |
| `kiali_graph_marshal_duration_seconds` | Histogram | Time to marshal graph JSON response | Large graphs causing serialization delays | `graph_kind`, `graph_type`, `with_service_nodes` |
| `kiali_graph_nodes` | Gauge | Number of nodes in a generated graph | Complexity signal — very high node count causes UI/backend performance degradation | `graph_kind`, `graph_type`, `with_service_nodes` |

### Backend Query Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_prometheus_processing_duration_seconds` | Histogram | Time for Kiali to execute a Prometheus/VictoriaMetrics query | Slow VM queries impacting Kiali UX; correlate with vmselect latency | `query_group` |
| `kiali_tracing_processing_duration_seconds` | Histogram | Time for Kiali to execute a tracing backend (Tempo) query | Slow trace lookups; correlate with Tempo query latency | `query_group` |

### Validation Processing

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_checker_processing_duration_seconds` | Histogram | Time for a validation checker to execute | Identify slow Istio config validators | `checker` |
| `kiali_validation_processing_duration_seconds` | Histogram | Time for full validation of a namespace/service | Slow "validations" tab; wide namespaces = slow | `namespace`, `service` |
| `kiali_single_validation_processing_duration_seconds` | Histogram | Time to validate a single Istio object | Pinpoint which object type causes validation delays | `namespace`, `type`, `name` |

### Cache Efficiency

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_cache_requests_total` | Counter | Total cache requests | Baseline cache traffic | `name` |
| `kiali_cache_hits_total` | Counter | Total cache hits | Hit ratio = `hits / requests`; low ratio = excessive backend queries | `name` |

### Kubernetes Client Health

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kiali_kubernetes_clients` | Gauge | Number of active Kubernetes API clients | Client leak detection (monotonic rise without reason) | — |

---

## Kiali Operator Metrics

The kiali-operator is Ansible-based (via operator-sdk). When `metrics.enabled: true`,
it exposes controller-runtime metrics on `:8080/metrics`:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliation attempts | Reconcile rate; errors indicate CR spec issues | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Failed reconciliations | Non-zero = operator cannot converge Kiali CR | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Reconciliation duration | Slow reconcile = Ansible playbook performance issue | `controller` |
| `workqueue_depth` | Gauge | Items waiting in workqueue | Growing = reconciler falling behind | `name` |
| `workqueue_adds_total` | Counter | Total items added to workqueue | Baseline reconcile demand | `name` |

Plus standard Go runtime metrics (`go_*`, `process_*`) — see `go-apm-metrics` skill.

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---------|------------------------|
| Kiali UI slow to load graphs | `kiali_graph_generation_duration_seconds` (p99), `kiali_graph_appender_duration_seconds` by appender |
| "Error fetching data" in UI | `kiali_api_failures_total` by route, then `kiali_prometheus_processing_duration_seconds` |
| Validation tab never finishes | `kiali_validation_processing_duration_seconds` by namespace |
| Traces tab slow | `kiali_tracing_processing_duration_seconds` (p99) — correlate with Tempo query latency |
| Kiali hitting VictoriaMetrics hard | `kiali_prometheus_processing_duration_seconds` rate + `kiali_cache_hits_total / kiali_cache_requests_total` (low hit rate = no caching benefit) |
| Kiali pod memory growing | `kiali_kubernetes_clients` (leak?), `kiali_graph_nodes` (large graphs held in memory) |
| Operator not applying CR changes | `controller_runtime_reconcile_errors_total`, `workqueue_depth` |

### Useful Queries

```promql
# API error rate per route (last 5m)
sum(rate(kiali_api_failures_total[5m])) by (route)

# Graph generation p99 latency
histogram_quantile(0.99, sum(rate(kiali_graph_generation_duration_seconds_bucket[5m])) by (le, graph_type))

# Cache hit ratio
sum(rate(kiali_cache_hits_total[5m])) / sum(rate(kiali_cache_requests_total[5m]))

# Prometheus query latency p95
histogram_quantile(0.95, sum(rate(kiali_prometheus_processing_duration_seconds_bucket[5m])) by (le, query_group))

# Operator reconcile error rate
sum(rate(controller_runtime_reconcile_errors_total[5m])) by (controller)
```

---

## Important Notes

- **Kiali does NOT generate mesh traffic metrics.** It READS `istio_requests_total`,
  `istio_request_duration_milliseconds`, etc. from VictoriaMetrics. For those metrics,
  see `istio-ambient-metrics`.
- The `route` label on `kiali_api_*` metrics is bounded (finite REST API routes) — safe
  for `group by`.
- The `namespace`+`type`+`name` labels on `kiali_single_validation_processing_duration_seconds`
  can be high-cardinality in large meshes. Use with `topk()`.

---

## Complements

- `istio-ambient-metrics` — mesh traffic metrics that Kiali consumes and displays
- `go-apm-metrics` — Go runtime metrics (goroutines, GC, memory) emitted by both Kiali pods
- `victoriametrics-troubleshooting` — if Kiali's Prometheus queries are slow, investigate VM backend
- `loki-tempo-self-metrics` — if tracing queries are slow, investigate Tempo backend

## Sources

- [Kiali v2.17 internal metrics source](https://github.com/kiali/kiali/blob/v2.17/prometheus/internalmetrics/internal_metrics.go) — definitive metric definitions
- [Kiali Helm chart 2.17.0](https://kiali.org/helm-charts) — deployed chart
- Deployed values: `k8s-setup/kiali/kiali-server/values.yaml.gotmpl` (metrics enabled on :9090)
- Deployed values: `k8s-setup/kiali/kiali-operator/values.yaml.gotmpl` (metrics enabled)
