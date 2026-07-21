---
name: traefik-metrics
description: "Use when diagnosing Traefik ingress health \u2014 request routing errors, backend server failures, TLS certificate expiry, configuration reload issues, or latency at entrypoint/router/service layers. Covers traefik_entrypoint_*, traefik_router_*, traefik_service_*, traefik_config_*, traefik_open_connections, traefik_tls_certs_not_after, plus go_* runtime. Grounded on Helm chart traefik/traefik 40.2.0 (appVersion Traefik v3.6.x)."
keywords: [traefik, ingress, tls]
---
# Traefik Ingress Controller Metrics

Prometheus metrics emitted by **Traefik Proxy v3** running as an internal ingress
controller (IngressClass `traefik-internal`) across all workload clusters.

**Question answered**: "Is traffic flowing through Traefik correctly, or being
lost/delayed at the ingress layer?"

**Scope**: Traefik's built-in Prometheus metrics endpoint (`:8080/metrics` via the
`metrics` entrypoint). Covers entrypoint → router → service RED metrics, global
connection/config/TLS health, and Go runtime. Does NOT cover service mesh metrics
(see `istio-ambient-metrics`) or Go runtime deep-dive (see `go-apm-metrics`).

---

## Scrape Pipeline

```
Traefik Pod (:8080/metrics, entryPoint=metrics)
  → ServiceMonitor (metrics.prometheus.serviceMonitor.enabled: true)
    → vmagent scrape
      → VictoriaMetrics cluster
```

**How enabled**: In Helm values (`traefik-internal/values.yaml.gotmpl`):
```yaml
metrics:
  prometheus:
    entryPoint: metrics
    service:
      enabled: true
    serviceMonitor:
      enabled: true
    prometheusRule:
      enabled: true
```

**Deployment**: 3–5 replicas (HPA), namespace `traefik`, all clusters.

---

## Deployed Version

| Component | Version |
|-----------|---------|
| Helm chart | `traefik/traefik` **40.2.0** |
| Traefik Proxy | **v3.6.x** (appVersion) |
| Metric prefix | `traefik_` (v3 naming — NOT the v2 `traefik_backend_*` style) |

> ⚠️ The deployed `prometheus.rules.yaml` still references legacy `traefik_backend_*`
> metrics (v2 names). These rules will NOT fire on v3 — only the `traefik_service_*`
> rules are functional. Consider updating the PrometheusRule.

---

## 1. Global Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `traefik_config_reloads_total` | Counter | Total count of dynamic configuration reloads | Rate spike = frequent provider changes (IngressRoute churn, CRD flapping) | — |
| `traefik_config_last_reload_success` | Gauge | Unix timestamp of last successful config reload | If stale while `reloads_total` grows → reloads are FAILING (misconfigured routes) | — |
| `traefik_open_connections` | Gauge | Current open connections across all entrypoints | Saturation signal; sustained high = connection exhaustion risk | `entrypoint`, `protocol` |
| `traefik_tls_certs_not_after` | Gauge | Expiration date (Unix timestamp) of loaded TLS certificates | Alert when `value - time() < 7d` — imminent cert expiry | `cn`, `sans`, `serial` |

---

## 2. EntryPoint Metrics (Ingress Edge)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `traefik_entrypoint_requests_total` | Counter | Total HTTP requests received by an entrypoint | Baseline traffic volume; sudden drop = upstream (NLB) or DNS issue | `code`, `method`, `protocol`, `entrypoint` |
| `traefik_entrypoint_request_duration_seconds` | Histogram | Request processing duration at entrypoint level | p99 latency at the edge; high = Traefik itself is saturated | `code`, `method`, `protocol`, `entrypoint` |
| `traefik_entrypoint_requests_tls_total` | Counter | Total HTTPS requests by TLS version/cipher | Detect clients using weak TLS versions (TLS 1.0/1.1) | `tls_version`, `tls_cipher`, `entrypoint` |
| `traefik_entrypoint_requests_bytes_total` | Counter | Total inbound request bytes at entrypoint | Traffic volume (bytes); spike = large payload attack or bulk upload | `code`, `method`, `protocol`, `entrypoint` |
| `traefik_entrypoint_responses_bytes_total` | Counter | Total outbound response bytes at entrypoint | Egress volume; correlate with AWS data-transfer costs | `code`, `method`, `protocol`, `entrypoint` |

---

## 3. Router Metrics (Routing Layer)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `traefik_router_requests_total` | Counter | Total requests handled per router | Per-route traffic; 0 rate on expected router = routing rule broken | `code`, `method`, `protocol`, `router`, `service` |
| `traefik_router_request_duration_seconds` | Histogram | Request duration per router | Identify which route is slow; compare across routers | `code`, `method`, `protocol`, `router`, `service` |
| `traefik_router_requests_tls_total` | Counter | HTTPS requests per router by TLS version | Per-route TLS compliance audit | `tls_version`, `tls_cipher`, `router`, `service` |
| `traefik_router_requests_bytes_total` | Counter | Inbound request bytes per router | Per-route payload sizing | `code`, `method`, `protocol`, `router`, `service` |
| `traefik_router_responses_bytes_total` | Counter | Outbound response bytes per router | Per-route egress | `code`, `method`, `protocol`, `router`, `service` |

---

## 4. Service Metrics (Backend Layer)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `traefik_service_requests_total` | Counter | Total requests processed per backend service | RED method: rate. Filter `code=~"5.."` for error rate | `code`, `method`, `protocol`, `service` |
| `traefik_service_request_duration_seconds` | Histogram | Request duration per backend service | RED method: duration. p99 identifies slow backends | `code`, `method`, `protocol`, `service` |
| `traefik_service_requests_tls_total` | Counter | HTTPS requests per service by TLS version | Backend TLS compliance | `tls_version`, `tls_cipher`, `service` |
| `traefik_service_retries_total` | Counter | Retry count per service | Non-zero = backend instability (pods restarting, connection refused) | `service` |
| `traefik_service_server_up` | Gauge | Backend server health (1=up, 0=down) | **KEY** — 0 = Traefik sees backend as dead. Used in `TraefikServiceDown` alert | `service`, `url` |
| `traefik_service_requests_bytes_total` | Counter | Inbound request bytes per service | Per-service payload volume | `code`, `method`, `protocol`, `service` |
| `traefik_service_responses_bytes_total` | Counter | Outbound response bytes per service | Per-service response volume | `code`, `method`, `protocol`, `service` |

---

## 5. Go Runtime Metrics

Traefik is a Go binary and emits standard `go_*` and `process_*` metrics via
`client_golang`. See **`go-apm-metrics`** skill for full reference. Key ones for
Traefik troubleshooting:

| Metric Name | Type | Troubleshooting Use |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak (monotonic rise → connection/handler leak) |
| `go_memstats_alloc_bytes` | Gauge | Current heap — compare with container memory limit |
| `process_resident_memory_bytes` | Gauge | RSS — approaching limit = OOMKill risk |
| `go_gc_duration_seconds` | Summary | GC pause duration; high p99 = latency spikes |

---

## Deployed Alerting Rules

From `traefik-internal/prometheus.rules.yaml`:

| Alert | Expression | Severity | Notes |
|---|---|---|---|
| `TraefikServiceDown` | `count(traefik_service_server_up) by (service) == 0` | critical | All backends for a service are down |
| `TraefikHighHTTP4xxErrorRateService` | 4xx rate > 5% (3m window) | critical | Client error spike per service |
| `TraefikHighHTTP5xxErrorRateService` | 5xx rate > 5% (3m window) | critical | Server error spike per service |

> ⚠️ **Legacy rules present but non-functional on v3**: `TraefikBackendDown`,
> `TraefikHighHTTP4xxErrorRateBackend`, `TraefikHighHTTP5xxErrorRateBackend` — these
> reference `traefik_backend_*` metrics which do not exist in Traefik v3.

---

## Symptom → Metric Quick-Reference

| Symptom | First Query | Follow-Up |
|---------|-------------|-----------|
| Requests not reaching backend | `sum(rate(traefik_entrypoint_requests_total[5m])) - sum(rate(traefik_service_requests_total[5m]))` | Difference = dropped at routing. Check `traefik_router_requests_total` per router |
| High latency for a service | `histogram_quantile(0.99, sum by (le,service) (rate(traefik_service_request_duration_seconds_bucket[5m])))` | Compare with `traefik_entrypoint_request_duration_seconds` — difference = backend latency |
| Backend pods down | `traefik_service_server_up == 0` | Correlate with `kube_pod_status_phase` and pod events |
| TLS certificate about to expire | `(traefik_tls_certs_not_after - time()) / 86400 < 7` | Check cert-manager certificate status; trigger renewal |
| Config reloads failing | `increase(traefik_config_reloads_total[5m]) > 0 AND traefik_config_last_reload_success < (time() - 300)` | IngressRoute or Middleware CRD has validation error |
| Connection exhaustion | `traefik_open_connections{entrypoint="websecure"} > 10000` | Check HPA replicas; consider scaling or connection limit |
| 5xx error spike | `sum by (service) (rate(traefik_service_requests_total{code=~"5.."}[5m])) / sum by (service) (rate(traefik_service_requests_total[5m]))` | Identify affected service; check pod health + logs |
| Retries to backend | `rate(traefik_service_retries_total[5m]) > 0` | Backend instability — pods restarting, network issues |
| Traffic imbalance between entrypoints | `sum by (entrypoint) (rate(traefik_entrypoint_requests_total[5m]))` | Compare `web` vs `websecure`; HTTP→HTTPS redirect working? |

---

## MetricsQL Examples (Copy-Paste)

### Service error rate (RED)

```promql
sum by (service) (
  rate(traefik_service_requests_total{code=~"5.."}[5m])
)
/
sum by (service) (
  rate(traefik_service_requests_total[5m])
)
```

### Entrypoint p99 latency

```promql
histogram_quantile(0.99,
  sum by (le, entrypoint) (
    rate(traefik_entrypoint_request_duration_seconds_bucket[5m])
  )
)
```

### Backend health (all servers)

```promql
traefik_service_server_up == 0
```

### TLS cert expiry (days remaining)

```promql
(traefik_tls_certs_not_after - time()) / 86400
```

### Config reload failure detection

```promql
increase(traefik_config_reloads_total[5m]) > 0
and
traefik_config_last_reload_success < (time() - 300)
```

### Total ingress throughput (requests/sec)

```promql
sum(rate(traefik_entrypoint_requests_total[5m]))
```

### Per-service retry rate

```promql
sum by (service) (rate(traefik_service_retries_total[5m]))
```

---

## High-Cardinality Label Warnings

| Metric | Label | Risk | Mitigation |
|--------|-------|------|------------|
| `traefik_router_*` | `router` | Moderate — bounded by IngressRoute count | Safe if <500 routes; use `topk()` otherwise |
| `traefik_service_*` | `service` | Moderate — bounded by K8s Service count | Safe in practice |
| `traefik_service_server_up` | `url` | Moderate — one series per backend pod IP | Acceptable; rotates with pod churn |
| `traefik_tls_certs_not_after` | `sans` | Low but wide — multi-SAN certs create long label values | Safe; few certs total |

---

## Correlation with Other Signals

| Traefik metric anomaly | Correlate with |
|------------------------|----------------|
| `traefik_service_server_up == 0` | `kube_pod_status_phase`, pod events, readiness probe failures |
| High `traefik_entrypoint_request_duration_seconds` | OTel traces (`http.server.request.duration` on the backend pod) |
| `traefik_service_requests_total{code="503"}` spike | Pod CPU/memory saturation, HPA scaling lag |
| `traefik_open_connections` near limit | NLB active connections in CloudWatch, Karpenter node provisioning |
| `traefik_config_reloads_total` churn | ArgoCD sync events, IngressRoute CRD changes in git |

---

## Version Differences (v2 vs v3)

| v2 Metric Name | v3 Equivalent | Notes |
|---|---|---|
| `traefik_backend_requests_total` | `traefik_service_requests_total` | "backend" renamed to "service" |
| `traefik_backend_request_duration_seconds` | `traefik_service_request_duration_seconds` | Same rename |
| `traefik_backend_server_up` | `traefik_service_server_up` | Same rename |
| `traefik_backend_open_connections` | `traefik_open_connections` | Moved to global level with `entrypoint` label |
| `traefik_entrypoint_open_connections` | `traefik_open_connections` | Consolidated into single global metric |

---

## Complements

- **`go-apm-metrics`** — Deep Go runtime metrics (goroutines, GC, scheduler)
  emitted by the same Traefik binary
- **`istio-ambient-metrics`** — Service mesh L4/L7 metrics (ztunnel/waypoint)
  for pod-to-pod traffic AFTER Traefik
- **`k8s-workload-metrics`** — Container CPU/memory/restart metrics for Traefik
  pods themselves
- **`cert-manager-metrics`** — Certificate lifecycle (issuer health, renewal) —
  correlate with `traefik_tls_certs_not_after`

## Sources

- [Traefik v3.2 Metrics Overview (official docs)](https://doc.traefik.io/traefik/v3.2/observability/metrics/overview/) —
  canonical metric name reference (confirmed identical for v3.6.x)
- [Traefik v3.2 Prometheus Configuration](https://doc.traefik.io/traefik/v3.2/observability/metrics/prometheus/)
- Deployed Helm chart: `traefik/traefik` **40.2.0** (k8s-setup `traefik/helmfile.yaml.gotmpl`)
- Deployed values: `traefik/traefik-internal/values.yaml.gotmpl`
- Deployed alerts: `traefik/traefik-internal/prometheus.rules.yaml`
