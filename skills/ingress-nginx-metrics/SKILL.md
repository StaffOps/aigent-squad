---
name: ingress-nginx-metrics
description: "Use when diagnosing ingress-nginx controller health — request RED metrics per host/ingress/controller instance, TLS certificate expiry, config reload failures, upstream latency, connection saturation, or NGINX process resource usage. Covers nginx_ingress_controller_requests, nginx_ingress_controller_request_duration_seconds, nginx_ingress_controller_response_duration_seconds, nginx_ingress_controller_ssl_expire_time_seconds, nginx_ingress_controller_config_last_reload_successful, nginx_ingress_controller_nginx_process_*, nginx_ingress_controller_build_info, plus go_*. Grounded on Helm chart i
keywords: [ingress-nginx, ingress, nginx, "ingress nginx", tls]
---
# Ingress-NGINX Controller Metrics

Prometheus metrics for the **Kubernetes community ingress-nginx controller** (kubernetes/ingress-nginx).

**Question answered**: "Is traffic flowing correctly through our ingress controllers? Which host/ingress has errors or high latency?"

---

## Deployment Topology (Multi-Instance)

This environment runs **up to 10 separate ingress-nginx controller instances** per cluster, each with its own IngressClass, NLB, and ServiceMonitor:

| Release Name | IngressClass | controllerValue | NLB Scheme | Purpose |
|---|---|---|---|---|
| `ingress-nginx-external` | `nginx-external` | `k8s.io/ingress-nginx-external` | internet-facing | Public traffic (default) |
| `ingress-nginx-internal` | `nginx-internal` | `k8s.io/ingress-nginx-internal` | internal | VPC-internal traffic (default) |
| `ingress-nginx-apps-external` | `nginx-apps-external` | `k8s.io/ingress-nginx-apps-external` | internet-facing | App-team public ingress |
| `ingress-nginx-apps-internal` | `nginx-apps-internal` | `k8s.io/ingress-nginx-apps-internal` | internal | App-team internal ingress |
| `ingress-nginx-acum-external` | `nginx-acum-external` | `k8s.io/ingress-nginx-acum-external` | internet-facing | ACUM team public |
| `ingress-nginx-acum-internal` | `nginx-acum-internal` | `k8s.io/ingress-nginx-acum-internal` | internal | ACUM team internal |
| `ingress-nginx-bm-internal` | `nginx-bm-internal` | `k8s.io/ingress-nginx-bm-internal` | internal | BM team internal |
| `ingress-nginx-dcp-internal` | `nginx-dcp-internal` | `k8s.io/ingress-nginx-dcp-internal` | internal | DCP team internal |
| `ingress-nginx-dpm-internal` | `nginx-dpm-internal` | `k8s.io/ingress-nginx-dpm-internal` | internal | DPM team internal |
| `ingress-nginx-mdt-internal` | `nginx-mdt-internal` | `k8s.io/ingress-nginx-mdt-internal` | internal | MDT team internal |

**Critical**: when querying metrics, ALWAYS filter by `controller_class` or `controller_namespace` + `controller_pod` to disambiguate which instance you're investigating.

All instances are deployed in namespace `nginx` on each cluster.

---

## Scrape Pipeline

```
Controller Pod :10254/metrics → ServiceMonitor → vmagent scrape → VictoriaMetrics
```

**How enabled**: `controller.metrics.enabled: true` + `controller.metrics.serviceMonitor.enabled: true` in each instance's values.yaml. PrometheusRules are also enabled via `controller.metrics.prometheusRule.enabled: true`.

**Clusters**: devops-core, applications-dev-nv, applications-prd-nv (all instances on dev/prd; only external+internal on core).

---

## Request RED Metrics (Primary)

The core traffic observability metrics. Apply the RED method: **R**ate, **E**rror rate, **D**uration.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `nginx_ingress_controller_requests` | Counter | Total HTTP requests processed | **Rate** = request throughput; filter by `status` for error rate | `controller_class`, `controller_namespace`, `controller_pod`, `namespace`, `ingress`, `service`, `host`, `path`, `method`, `status` |
| `nginx_ingress_controller_request_duration_seconds_bucket` | Histogram | Time from request received to response sent (full latency including upstream) | **Duration** = p50/p95/p99 latency per host/ingress; primary SLI | `controller_class`, `controller_namespace`, `controller_pod`, `namespace`, `ingress`, `service`, `host`, `path`, `method`, `status`, `le` |
| `nginx_ingress_controller_response_duration_seconds_bucket` | Histogram | Time from NGINX connecting to upstream until response body read | Upstream-only latency; compare with `request_duration` to isolate NGINX overhead vs backend slowness | `controller_class`, `controller_namespace`, `controller_pod`, `namespace`, `ingress`, `service`, `host`, `path`, `method`, `status`, `le` |
| `nginx_ingress_controller_request_size_bucket` | Histogram | Request body size in bytes | Detect large payloads causing memory/timeout issues | `controller_class`, `controller_namespace`, `controller_pod`, `namespace`, `ingress`, `service`, `host`, `path`, `method`, `status`, `le` |
| `nginx_ingress_controller_response_size_bucket` | Histogram | Response body size in bytes | Detect unexpectedly large responses (data leak, uncompressed) | `controller_class`, `controller_namespace`, `controller_pod`, `namespace`, `ingress`, `service`, `host`, `path`, `method`, `status`, `le` |

⚠️ **High-cardinality warning**: The `path` label can explode if path-based routing is fine-grained. In production queries, always aggregate by `host` + `ingress` first, then drill into `path` only when needed.

---

## Controller Health Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `nginx_ingress_controller_config_last_reload_successful` | Gauge (0/1) | Whether the last NGINX config reload succeeded | **0 = config broken** — invalid Ingress resource broke NGINX config generation. Traffic still served with stale config but new ingresses won't take effect. | `controller_class`, `controller_namespace`, `controller_pod` |
| `nginx_ingress_controller_config_last_reload_successful_timestamp_seconds` | Gauge | Unix timestamp of last successful reload | Staleness detection — if hours old, something is blocking reloads | `controller_class`, `controller_namespace`, `controller_pod` |
| `nginx_ingress_controller_success` | Counter | Successful NGINX config reloads | Rate = reload frequency; healthy is low and stable | `controller_class`, `controller_namespace`, `controller_pod` |
| `nginx_ingress_controller_config_hash` | Gauge | Hash of the running NGINX config | Detect config drift between replicas (all pods of same IngressClass should have same hash) | `controller_class`, `controller_namespace`, `controller_pod` |
| `nginx_ingress_controller_build_info` | Gauge (always 1) | Build metadata | Identify controller version and build across pods | `controller_class`, `controller_namespace`, `controller_pod`, `version`, `build`, `repository` |

---

## TLS / SSL Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `nginx_ingress_controller_ssl_expire_time_seconds` | Gauge | Unix timestamp when TLS certificate expires | **Alert when < 30 days** — certificate renewal failure detection | `host`, `namespace`, `secret_name`, `controller_class`, `controller_namespace`, `controller_pod` |
| `nginx_ingress_controller_ssl_certificate_info` | Gauge (always 1) | TLS certificate metadata | Track cert issuer, serial across all hosts | `host`, `namespace`, `secret_name`, `issuer_organization`, `issuer_common_name`, `serial_number` |

---

## NGINX Process Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `nginx_ingress_controller_nginx_process_connections` | Gauge | Current connections in each state | Saturation — `active` approaching `worker_connections` = connection exhaustion | `state` (`active`, `reading`, `writing`, `waiting`) |
| `nginx_ingress_controller_nginx_process_connections_total` | Counter | Total connections handled/accepted | Rate of new connections | `state` (`accepted`, `handled`) |
| `nginx_ingress_controller_nginx_process_requests_total` | Counter | Total requests processed by NGINX worker | Should closely track `nginx_ingress_controller_requests`; divergence indicates internal routing issues | — |
| `nginx_ingress_controller_nginx_process_read_bytes_total` | Counter | Bytes read from clients | Network I/O throughput (ingress direction) | — |
| `nginx_ingress_controller_nginx_process_write_bytes_total` | Counter | Bytes written to clients | Network I/O throughput (egress direction) | — |
| `nginx_ingress_controller_nginx_process_cpu_seconds_total` | Counter | CPU time consumed by NGINX process | CPU saturation of the NGINX workers (compare with container limits) | — |
| `nginx_ingress_controller_nginx_process_num_procs` | Gauge | Number of NGINX worker processes | Should equal `worker-processes` config (usually = CPU cores) | — |
| `nginx_ingress_controller_nginx_process_resident_memory_bytes` | Gauge | RSS of NGINX process | Memory saturation; approaching container limit = OOMKill risk | — |

---

## Go Runtime Metrics

The controller also exposes standard Go runtime metrics (prefix `go_*`). See **`go-apm-metrics`** skill for full reference — same collector (`client_golang`).

Key ones for controller health:
- `go_goroutines` — goroutine leak in controller logic
- `go_memstats_alloc_bytes` — controller memory growth
- `go_gc_duration_seconds` — GC pressure in the Go controller process

---

## Deployed PrometheusRules (Alert Definitions)

The following alerts are deployed via `nginx/prometheus.rules.yaml` on all instances:

| Alert | Expression | Severity | Meaning |
|---|---|---|---|
| `NginxHighHttp4xxErrorRate` | `sum(rate(nginx_http_requests_total{status=~"^4.."}[1m])) / sum(rate(nginx_http_requests_total[1m])) * 100 > 5` | critical | >5% client errors — misconfigured routes, missing services, or client abuse |
| `NginxHighHttp5xxErrorRate` | `sum(rate(nginx_http_requests_total{status=~"^5.."}[1m])) / sum(rate(nginx_http_requests_total[1m])) * 100 > 5` | critical | >5% server errors — upstream failures, timeouts |
| `NginxLatencyHigh` | `histogram_quantile(0.99, sum(rate(nginx_http_request_duration_seconds_bucket[2m])) by (host, node, le)) > 3` | warning | p99 latency > 3 seconds — upstream saturation or network |

> ⚠️ Note: the alert rules use `nginx_http_requests_total` and `nginx_http_request_duration_seconds_bucket` which are NGINX stub_status/VTS metrics names. In the standard kubernetes/ingress-nginx controller, the actual metric names are `nginx_ingress_controller_requests` and `nginx_ingress_controller_request_duration_seconds_bucket`. These rules may need updating if they reference VTS-style names that don't exist in the controller ≥0.25 (uses `nginx_ingress_controller_*` prefix).

---

## Troubleshooting Quick-Reference

| Symptom | First Query | Follow-Up |
|---------|-------------|-----------|
| 5xx spike on specific host | `sum by (host,status) (rate(nginx_ingress_controller_requests{status=~"5.."}[5m]))` | Identify which `ingress`/`service`; check upstream pod health |
| High latency per host | `histogram_quantile(0.99, sum by (host, le) (rate(nginx_ingress_controller_request_duration_seconds_bucket[5m])))` | Compare with `response_duration` to isolate NGINX vs upstream |
| Config reload broken | `nginx_ingress_controller_config_last_reload_successful == 0` | Check controller logs for invalid Ingress annotation/config snippet |
| TLS cert about to expire | `(nginx_ingress_controller_ssl_expire_time_seconds - time()) / 86400 < 30` | Check cert-manager Certificate resource status for that secret |
| Connection saturation | `nginx_ingress_controller_nginx_process_connections{state="active"}` approaching worker_connections (default 16384) | Scale replicas or tune `worker-connections` |
| Traffic dropped by one instance | `sum by (controller_class) (rate(nginx_ingress_controller_requests[5m]))` — one class has 0 | Check if NLB target group is healthy; pod might be NotReady |
| NGINX process OOMKilled | `nginx_ingress_controller_nginx_process_resident_memory_bytes` approaching container memory limit | Increase limits or investigate large response buffering |
| Config drift between replicas | `count(count by (controller_pod) (nginx_ingress_controller_config_hash{controller_class="k8s.io/ingress-nginx-internal"})) > 1` | Rolling restart in progress, or one pod failed to reload |

---

## MetricsQL Examples (Copy-Paste)

### Error rate per controller class (5m window)

```promql
sum by (controller_class) (
  rate(nginx_ingress_controller_requests{status=~"5.."}[5m])
)
/
sum by (controller_class) (
  rate(nginx_ingress_controller_requests[5m])
)
```

### Request rate per host (top 10)

```promql
topk(10,
  sum by (host) (
    rate(nginx_ingress_controller_requests[5m])
  )
)
```

### p99 latency per host

```promql
histogram_quantile(0.99,
  sum by (host, le) (
    rate(nginx_ingress_controller_request_duration_seconds_bucket[5m])
  )
)
```

### NGINX overhead (total duration minus upstream response)

```promql
histogram_quantile(0.95, sum by (host, le) (rate(nginx_ingress_controller_request_duration_seconds_bucket[5m])))
-
histogram_quantile(0.95, sum by (host, le) (rate(nginx_ingress_controller_response_duration_seconds_bucket[5m])))
```

### TLS certificates expiring within 14 days

```promql
(nginx_ingress_controller_ssl_expire_time_seconds - time()) / 86400 < 14
```

### Connection utilization (active / max)

```promql
nginx_ingress_controller_nginx_process_connections{state="active"}
/
16384  # default worker_connections — adjust if overridden
```

### Bytes throughput per controller

```promql
sum by (controller_class) (
  rate(nginx_ingress_controller_nginx_process_write_bytes_total[5m])
)
```

---

## High-Cardinality Label Warnings

| Label | Risk | Mitigation |
|-------|------|------------|
| `path` | **HIGH** — unbounded if path-based routing granular | Never `group by (path)` without `topk()` or filtering by specific `host`/`ingress` first |
| `host` | Medium — bounded by number of Ingress hosts (typically 50–200) | Safe for most queries; use `topk()` for cluster-wide views |
| `controller_pod` | Low — bounded by HPA maxReplicas (5–11 per instance) | Safe |
| `status` | Low — bounded HTTP status codes (~50) | Safe |
| `ingress` × `namespace` | Medium — all Ingress resources | Aggregate at `host` level for overview, drill to `ingress` for detail |

---

## Version Notes

- **Chart**: `ingress-nginx/ingress-nginx` **4.14.0**
- **appVersion**: controller **v1.14.0** (image: `registry.k8s.io/ingress-nginx/controller:v1.14.0`)
- Metric prefix `nginx_ingress_controller_*` has been stable since controller v0.25.0 (2019) — no prefix changes expected.
- `response_duration_seconds` was added in controller v0.30.0.
- `ssl_certificate_info` was added in controller v1.2.0.
- `config_hash` was added in controller v1.0.0.
- VTS-based metric names (`nginx_http_requests_total`, `nginx_http_request_duration_seconds_bucket`) are **NOT** emitted by this controller — those belong to the old VTS module (removed in 0.25). The deployed PrometheusRules may need updating if they reference VTS names.

---

## Complements

- **`go-apm-metrics`** — Go runtime metrics (`go_*`) exposed by the controller process
- **`k8s-workload-metrics`** — container-level CPU/memory for the controller pods (cAdvisor)
- **`istio-ambient-metrics`** — if mesh sidecar/ztunnel wraps ingress traffic (not typical for ingress-nginx)
- **`cert-manager-metrics`** — certificate issuance health (feeds `ssl_expire_time_seconds`)

---

## Sources

- [kubernetes/ingress-nginx monitoring docs](https://github.com/kubernetes/ingress-nginx/blob/main/docs/user-guide/monitoring.md)
- [ingress-nginx Prometheus metrics source code](https://github.com/kubernetes/ingress-nginx/tree/controller-v1.14.0/internal/ingress/metric/collectors)
- [Chart.yaml for 4.14.0](https://github.com/kubernetes/ingress-nginx/blob/helm-chart-4.14.0/charts/ingress-nginx/Chart.yaml)
- Deployed helmfile: `02-KUBE/00-CONFIG/k8s-setup/nginx/helmfile.yaml.gotmpl`
- Deployed PrometheusRules: `02-KUBE/00-CONFIG/k8s-setup/nginx/prometheus.rules.yaml`
