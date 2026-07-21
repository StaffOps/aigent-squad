---
name: metrics-server-metrics
description: "Use when diagnosing Kubernetes metrics-server health — kubelet scrape failures, stale metrics, storage readiness, API extension server saturation, or HPA/VPA data gaps. Covers metrics_server_kubelet_request_*, metrics_server_manager_tick_duration_seconds, metrics_server_storage_points, metrics_server_api_metric_freshness_seconds, plus apiserver_request_total and apiserver_request_duration_seconds (extension API server). Grounded on Helm chart metrics-server/metrics-server 3.12.2 (appVersion v0.7.2)."
keywords: [server, hpa, vpa]
---
# Kubernetes Metrics Server Self-Metrics

Self-telemetry from the **Kubernetes metrics-server** — the component that scrapes
kubelet `/metrics/resource` on every node and exposes the Metrics API
(`metrics.k8s.io/v1beta1`) consumed by `kubectl top`, HPA, and VPA.

**Question answered**: "Is metrics-server collecting fresh data from all nodes, or
is HPA scaling blind because metrics are stale/missing?"

**Pipeline**: metrics-server `:4443/metrics` (HTTPS) → vmagent ServiceMonitor scrape → VictoriaMetrics.

**Deployed version**: Helm chart `metrics-server/metrics-server` **3.12.2**
(appVersion **v0.7.2**), from `https://kubernetes-sigs.github.io/metrics-server/`.
Deployed to `kube-system` namespace across all clusters via helmfile.

**Configuration** (from deployed values):
- `metrics.enabled: true` + `serviceMonitor.enabled: true`
- 1 replica, no addon-resizer
- Resources: 100m CPU / 256Mi memory (requests = limits)

---

## 1. Kubelet Scraper Metrics

Metrics-server scrapes kubelet `/metrics/resource` on every node each tick cycle
(default 60s). These metrics track that scraper's health.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `metrics_server_kubelet_request_total` | Counter | Total requests sent to kubelet API | Rate of successful vs failed scrapes; `success="false"` rising = nodes unreachable | `success` |
| `metrics_server_kubelet_request_duration_seconds` | Histogram | Latency of individual kubelet scrape requests | p99 > 10s = kubelet overloaded or network issue; correlate with specific `node` label | `node`, `le` |
| `metrics_server_kubelet_last_request_time_seconds` | Gauge | Unix timestamp of last request to each node's kubelet | Stale value (now - value > 2× resolution) = node being skipped; detect missed scrapes | `node` |

### Key queries

```promql
# Scrape failure rate (should be 0)
rate(metrics_server_kubelet_request_total{success="false"}[5m])

# Kubelet request latency p99
histogram_quantile(0.99, rate(metrics_server_kubelet_request_duration_seconds_bucket[5m]))

# Nodes not scraped in last 2 minutes (resolution=60s)
time() - metrics_server_kubelet_last_request_time_seconds > 120
```

---

## 2. Manager (Tick Cycle) Metrics

The manager runs one full scrape-and-store cycle per `--metric-resolution` interval
(default 60s). This histogram covers the total wall-clock time of each cycle.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `metrics_server_manager_tick_duration_seconds` | Histogram | Total time spent collecting from all kubelets + storing results per tick | Tick exceeding resolution (>60s) = liveness probe fails, metrics go stale, HPA becomes blind | `le` |

### Key queries

```promql
# Average tick duration (should be well under 60s)
rate(metrics_server_manager_tick_duration_seconds_sum[5m])
  / rate(metrics_server_manager_tick_duration_seconds_count[5m])

# Percentage of ticks exceeding 30s (early warning)
rate(metrics_server_manager_tick_duration_seconds_bucket{le="30"}[5m])
  / rate(metrics_server_manager_tick_duration_seconds_count[5m])
```

---

## 3. Storage Metrics

In-memory storage holds the last two metric points per node/container to compute
CPU rate (delta). This gauge shows how many points are ready to serve.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `metrics_server_storage_points` | Gauge | Number of metric points stored and ready to serve | `type="node"`: should equal node count; `type="container"`: should be > 0. Zero after startup = not yet ready (first 2 ticks needed) | `type` |

### Key queries

```promql
# Node coverage (should match cluster node count)
metrics_server_storage_points{type="node"}

# Container points stored (0 = not ready, HPA will get errors)
metrics_server_storage_points{type="container"}
```

---

## 4. API Freshness Metrics

When the Metrics API serves a request (`kubectl top`, HPA), it records how old
the returned metric is relative to now. Fresh = good; stale = autoscaling delayed.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `metrics_server_api_metric_freshness_seconds` | Histogram | Age of metrics at the time they are served via the API | p99 > 2× resolution = consumers getting stale data; HPA decisions based on old info | `le` |

### Key queries

```promql
# API freshness p99 (should be < 120s for default 60s resolution)
histogram_quantile(0.99, rate(metrics_server_api_metric_freshness_seconds_bucket[5m]))

# Median freshness
histogram_quantile(0.50, rate(metrics_server_api_metric_freshness_seconds_bucket[5m]))
```

---

## 5. Extension API Server Metrics (apiserver_*)

Metrics-server registers as a Kubernetes extension API server (aggregated API).
It exposes standard `apiserver_*` metrics from `k8s.io/apiserver`:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `apiserver_request_total` | Counter | Total API requests handled by the metrics-server extension API | High error rate (`code=~"5.."`) = metrics-server API failing; HPA gets errors | `verb`, `resource`, `code` |
| `apiserver_request_duration_seconds` | Histogram | Latency of API requests (GET NodeMetrics, GET PodMetrics) | High latency = storage contention or slow list from informer cache | `verb`, `resource`, `le` |
| `apiserver_current_inflight_requests` | Gauge | In-flight API requests by priority level | Saturation signal for the extension API server | `request_kind` |

### Key queries

```promql
# Error rate on Metrics API
rate(apiserver_request_total{job="metrics-server", code=~"5.."}[5m])

# API latency p99 for pod metrics
histogram_quantile(0.99,
  rate(apiserver_request_duration_seconds_bucket{job="metrics-server", resource="pods"}[5m]))
```

---

## 6. Go Runtime Metrics (go_*)

Standard `client_golang` runtime metrics. See `skills/apm-metrics/go-apm-metrics`
for the full catalog. Key ones for metrics-server:

| Metric Name | Type | Relevance to metrics-server |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak = stuck kubelet connections (1 goroutine per node per tick) |
| `go_memstats_alloc_bytes` | Gauge | Memory pressure; metrics-server stores all node+pod points in-memory |
| `process_resident_memory_bytes` | Gauge | Actual RSS; compare with 256Mi limit to detect OOM risk |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Likely Cause |
|---|---|---|
| `kubectl top nodes` returns "error: metrics not available" | `metrics_server_storage_points{type="node"}` = 0 | metrics-server just started (needs 2 ticks) OR all kubelet scrapes failing |
| HPA not scaling | `metrics_server_api_metric_freshness_seconds` p99 + `apiserver_request_total{code=~"5.."}` | Stale metrics or API errors |
| `kubectl top` shows partial nodes | `metrics_server_kubelet_request_total{success="false"}` + `metrics_server_kubelet_last_request_time_seconds` per node | Specific nodes unreachable (network policy, kubelet down) |
| metrics-server liveness probe fails | `metrics_server_manager_tick_duration_seconds` > 1.5× resolution | Tick taking too long — too many nodes, kubelet slow, CPU throttled |
| HPA shows "unable to get metrics" | `apiserver_request_total{code="404"}` + `metrics_server_storage_points{type="container"}` | Pod metrics not yet in storage (short-lived pods, container restart) |
| metrics-server OOMKilled | `process_resident_memory_bytes` vs limit + `metrics_server_storage_points` | Cluster too large for 256Mi limit; scale vertically or enable addon-resizer |
| Intermittent stale metrics | `metrics_server_kubelet_request_duration_seconds` p99 per node | Specific slow kubelets dragging down the entire tick |

---

## Complements

- `skills/apm-metrics/k8s-workload-metrics` — kubelet/cadvisor/kube-state-metrics (the SOURCE data that metrics-server consumes from kubelet, NOT metrics-server self-telemetry)
- `skills/apm-metrics/go-apm-metrics` — full Go runtime metrics catalog (`go_*`, `process_*`)
- `skills/apm-metrics/keda-metrics` — KEDA uses Metrics API as one scaling source
- `skills/apm-metrics/karpenter-metrics` — Karpenter scheduling decisions depend on resource requests which HPA (powered by metrics-server) adjusts

---

## Sources

- Helm chart: `metrics-server/metrics-server` v3.12.2 — [GitHub](https://github.com/kubernetes-sigs/metrics-server/tree/metrics-server-helm-chart-3.12.2/charts/metrics-server)
- App source: metrics-server v0.7.2 — [GitHub](https://github.com/kubernetes-sigs/metrics-server/tree/v0.7.2)
- Metric definitions verified from source:
  - `pkg/scraper/scraper.go` → `metrics_server_kubelet_request_total`, `metrics_server_kubelet_request_duration_seconds`, `metrics_server_kubelet_last_request_time_seconds`
  - `pkg/server/server.go` → `metrics_server_manager_tick_duration_seconds`
  - `pkg/storage/node.go` + `pkg/storage/pod.go` → `metrics_server_storage_points`
  - `pkg/api/node.go` + `pkg/api/pod.go` → `metrics_server_api_metric_freshness_seconds`
  - Extension API server metrics from `k8s.io/apiserver` → `apiserver_*`
- Deployed config: `02-KUBE/00-CONFIG/k8s-setup/metrics-server/metrics-server/values.yaml.gotmpl`
