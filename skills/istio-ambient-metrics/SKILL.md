---
name: istio-ambient-metrics
description: "Use when querying Istio Ambient mesh metrics in VictoriaMetrics, diagnosing L7/L4 failures via response_flags, building RED dashboards from mesh telemetry, alerting on mTLS cert expiry, or understanding reporter=source vs destination in Ambient mode (ztunnel/waypoint). Covers all standard Istio metrics confirmed present in the live environment."
keywords: [istio-ambient, istio, ambient, "istio ambient", mtls, victoriametrics]
---
# Istio Ambient Metrics

> **Confirmed present in live VictoriaMetrics inventory (2026-07-06).**
>
> All metric names below were verified against a live `label_values(__name__, ...)`
> query on the organization's VictoriaMetrics cluster. Semantics verified against
> [Istio Standard Metrics (v1.30)](https://istio.io/latest/docs/reference/config/metrics/)
> and [Envoy Access Log Response Flags](https://www.envoyproxy.io/docs/envoy/latest/configuration/observability/access_log/usage).

---

## Why Istio mesh metrics matter (complementary to OTel SDK traces)

Istio emits metrics **at the proxy layer** (ztunnel for L4, waypoint for L7 in
Ambient mode). This provides:

- **100% coverage** — every request is counted (no sampling bias like traces)
- **No app instrumentation required** — works even for services without OTel SDK
- **Complements service graph** — Tempo's spanmetrics connector gives trace-derived
  RED; Istio gives proxy-observed RED. Cross-validate them.
- **L4 visibility** — TCP bytes/connections for non-HTTP workloads
- **mTLS health** — cert expiry, connection security policy

### Ambient mode specifics

| Component | Role | Metrics emitted |
|-----------|------|-----------------|
| **ztunnel** | L4 proxy (per-node DaemonSet) | TCP metrics, `connection_security_policy`, L4 request counting |
| **waypoint** | L7 proxy (per-namespace/service) | Full HTTP/gRPC metrics (duration, size, response_code, response_flags) |
| **istiod** | Control plane | xDS push metrics, cert distribution |

In Ambient, `reporter` label behavior differs from sidecar mode — see label
reference below.

> **Cross-reference**: `istio-ambient-otel` (ServiceEntry + cross-cluster routing),
> `istio-ambient-debugging` (SNAT, 400 errors, hostname mismatch).

---

## HTTP / gRPC Metrics (L7 — via waypoint proxy)

### istio_requests_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | requests |
| **What it measures** | Total count of requests handled by Istio proxy |
| **Troubleshooting use** | Request rate (R in RED), error rate by response_code, traffic split validation |
| **Key labels** | `reporter`, `source_workload`, `source_app`, `destination_workload`, `destination_service`, `destination_service_name`, `response_code`, `response_flags`, `request_protocol`, `connection_security_policy`, `grpc_response_status` |

### istio_request_duration_milliseconds (Histogram)

Present as `_bucket`, `_sum`, `_count` suffixes.

| Field | Value |
|-------|-------|
| **Type** | Histogram (Distribution) |
| **Unit** | milliseconds |
| **What it measures** | Duration of requests as observed by the proxy |
| **Troubleshooting use** | Latency (D in RED), p50/p95/p99 SLI, detecting slow upstreams |
| **Key labels** | Same as `istio_requests_total` |

### istio_request_bytes (Histogram)

Present as `_bucket`, `_sum`, `_count` suffixes.

| Field | Value |
|-------|-------|
| **Type** | Histogram (Distribution) |
| **Unit** | bytes |
| **What it measures** | HTTP request body size |
| **Troubleshooting use** | Detecting oversized payloads, egress cost drivers |
| **Key labels** | Same as `istio_requests_total` |

### istio_response_bytes (Histogram)

Present as `_bucket`, `_sum`, `_count` suffixes.

| Field | Value |
|-------|-------|
| **Type** | Histogram (Distribution) |
| **Unit** | bytes |
| **What it measures** | HTTP response body size |
| **Troubleshooting use** | Bandwidth analysis, detecting bloated responses |
| **Key labels** | Same as `istio_requests_total` |

### istio_request_messages_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | messages |
| **What it measures** | gRPC messages sent from client (streaming RPC message count) |
| **Troubleshooting use** | gRPC streaming throughput, detecting unbalanced client streams |
| **Key labels** | Same as `istio_requests_total` |

### istio_response_messages_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | messages |
| **What it measures** | gRPC messages sent from server (streaming RPC message count) |
| **Troubleshooting use** | gRPC streaming throughput, detecting server-side stream issues |
| **Key labels** | Same as `istio_requests_total` |

---

## TCP Metrics (L4 — via ztunnel or waypoint)

### istio_tcp_connections_opened_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | connections |
| **What it measures** | Total TCP connections opened |
| **Troubleshooting use** | Connection churn, detecting connection storms |
| **Key labels** | `reporter`, `source_workload`, `destination_workload`, `destination_service`, `connection_security_policy` |

### istio_tcp_sent_bytes_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | bytes |
| **What it measures** | Total bytes sent in TCP responses |
| **Troubleshooting use** | Network egress per service pair, bandwidth saturation |
| **Key labels** | Same as TCP connections |

### istio_tcp_received_bytes_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | bytes |
| **What it measures** | Total bytes received in TCP requests |
| **Troubleshooting use** | Network ingress per service pair, data transfer cost (FinOps) |
| **Key labels** | Same as TCP connections |

---

## DNS Metrics (Ambient DNS Proxy)

These are emitted by Istio's built-in DNS proxy in Ambient mode.

### istio_dns_requests_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | requests |
| **What it measures** | DNS queries handled by Istio's DNS proxy |
| **Troubleshooting use** | DNS query rate, detecting DNS storms or misrouted lookups |
| **Key labels** | *unverified — likely includes response type/rcode* |

### istio_dns_upstream_failures_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | failures |
| **What it measures** | DNS upstream resolution failures |
| **Troubleshooting use** | DNS resolution health, detecting upstream DNS unavailability |
| **Key labels** | *unverified* |

### istio_dns_upstream_request_duration_seconds (Histogram)

Present as `_bucket`, `_sum`, `_count` suffixes.

| Field | Value |
|-------|-------|
| **Type** | Histogram |
| **Unit** | seconds |
| **What it measures** | Latency of upstream DNS queries |
| **Troubleshooting use** | Slow DNS resolution impacting service startup or request latency |
| **Key labels** | *unverified* |

### istio_on_demand_dns_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | requests |
| **What it measures** | On-demand DNS resolutions triggered (lazy resolution in Ambient) |
| **Troubleshooting use** | Frequency of on-demand lookups; high rate may indicate ServiceEntry misconfiguration |
| **Key labels** | *unverified* |

---

## Control Plane / Agent Metrics

### istio_agent_cert_expiry_seconds

| Field | Value |
|-------|-------|
| **Type** | Gauge |
| **Unit** | seconds (Unix timestamp of expiry) |
| **What it measures** | Expiration time of the mTLS workload certificate |
| **Troubleshooting use** | **CRITICAL for alerting** — alert when cert approaches expiry without renewal |
| **Key labels** | (per-pod, scraped from istio-agent) |

### istio_agent_pilot_xds_* (family)

| Field | Value |
|-------|-------|
| **Type** | Counter / Gauge (varies) |
| **Unit** | varies |
| **What it measures** | xDS connection state between agent and istiod (pushes, errors, reconnections) |
| **Troubleshooting use** | Control plane connectivity issues, config push failures |
| **Key labels** | *unverified — likely includes type (CDS/EDS/LDS/RDS)* |

### istio_agent_process_* (family)

| Field | Value |
|-------|-------|
| **Type** | Gauge |
| **Unit** | varies (cpu_seconds, resident_memory_bytes, etc.) |
| **What it measures** | Standard Go process metrics for the istio-agent process |
| **Troubleshooting use** | Agent resource consumption, memory leaks in agent |
| **Key labels** | Standard process labels |

### istio_agent_scrape* (family)

| Field | Value |
|-------|-------|
| **Type** | Counter / Gauge |
| **Unit** | varies |
| **What it measures** | Metrics scraping statistics from the agent |
| **Troubleshooting use** | Detecting scrape failures or gaps in metric collection |
| **Key labels** | *unverified* |

### istio_xds_connection_terminations_total

| Field | Value |
|-------|-------|
| **Type** | Counter |
| **Unit** | terminations |
| **What it measures** | xDS gRPC stream disconnections from control plane |
| **Troubleshooting use** | Control plane instability, network partitions between agent and istiod |
| **Key labels** | *unverified* |

### istio_build

| Field | Value |
|-------|-------|
| **Type** | Gauge (info metric, value=1) |
| **Unit** | — |
| **What it measures** | Build information (version, component, tag) |
| **Troubleshooting use** | Version audit across fleet, detecting inconsistent upgrades |
| **Key labels** | `component`, `tag` |

---

## Standard Labels Reference

### Traffic labels (on all HTTP/gRPC/TCP data-plane metrics)

| Label | Values | Importance |
|-------|--------|------------|
| **`reporter`** | `source`, `destination` | **CRITICAL in Ambient**: determines which proxy reported. In sidecar mode, each side has a proxy. In Ambient, ztunnel/waypoint may only report from one side depending on topology. Always filter or group by this. |
| **`source_workload`** | workload name | Identifies caller. Uses `service.istio.io/workload-name` label or pod owner. |
| **`source_app`** | app label value | From `app` label on source pod. |
| **`source_workload_namespace`** | namespace | Source namespace. |
| **`destination_workload`** | workload name | Identifies target. |
| **`destination_service`** | FQDN | Full service FQDN (e.g., `details.default.svc.cluster.local`). |
| **`destination_service_name`** | short name | Just the service name (e.g., `details`). |
| **`destination_service_namespace`** | namespace | Target namespace. |
| **`request_protocol`** | `http`, `grpc`, `tcp` | Protocol of the request. |
| **`response_code`** | HTTP status code | Only on HTTP metrics. `0` for connection-level failures. |
| **`response_flags`** | Envoy flag string | L7 failure diagnosis — see table below. |
| **`connection_security_policy`** | `mutual_tls`, `unknown`, `none` | Whether mTLS was active. `unknown` when reported from source (source can't observe its own TLS). |
| **`grpc_response_status`** | gRPC status code | Only on gRPC traffic. |
| **`source_cluster`** | cluster name | Multi-cluster identification. |
| **`destination_cluster`** | cluster name | Multi-cluster identification. |

### ⚠️ High-cardinality warning

| Label | Risk | Mitigation |
|-------|------|------------|
| `source_workload` × `destination_service` | Medium — bounded by service count but can explode in service-mesh-heavy envs | Aggregate by `destination_service_name` when possible |
| `response_code` | Low (bounded ~50 values) | Safe |
| `source_version` / `destination_version` | **HIGH** if many canary versions or dynamic labels | Drop via Telemetry API `tagOverrides` if cardinality spikes |
| `source_principal` / `destination_principal` | **HIGH** — includes full SPIFFE URI per service account | Avoid in recording rules; use only for ad-hoc debugging |

---

## Response Flags — L7 Failure Diagnosis

The `response_flags` label contains Envoy-generated codes that identify **why** a
request failed at the proxy level. This is the single most powerful label for
diagnosing mesh-layer issues.

| Flag | Meaning | Typical cause |
|------|---------|---------------|
| `-` | No flag (success) | Normal response |
| `UO` | Upstream Overflow | Circuit breaker tripped — too many pending requests. Check `DestinationRule` circuit breaker settings. |
| `UH` | No Healthy Upstream | All endpoints unhealthy. Check readiness probes, endpoint health. |
| `NR` | No Route Configured | No matching VirtualService or DestinationRule. Misconfig or missing route. |
| `UT` | Upstream Request Timeout | Upstream didn't respond within timeout. Check `VirtualService` timeout settings. |
| `UF` | Upstream Connection Failure | TCP connection to upstream failed. Network issue, pod not ready, or port mismatch. |
| `UC` | Upstream Connection Termination | Connection reset by upstream. App crash, OOM, or abrupt shutdown. |
| `LR` | Connection Local Reset | Local proxy reset the connection. |
| `UR` | Upstream Remote Reset | Upstream sent TCP RST. |
| `DC` | Downstream Connection Termination | Client disconnected before response completed. |
| `DI` | Delay Injected | Fault injection active (intentional). |
| `FI` | Fault Injected | Abort fault injection active (intentional). |
| `RL` | Rate Limited | Local rate limit applied. |
| `UAEX` | Unauthorized External | External auth denied the request. |
| `RLSE` | Rate Limit Service Error | Rate limit service unavailable. |
| `IH` | Invalid Header | Strictly-checked header validation failed. |

### Reading response_flags in queries

```promql
# Error rate broken down by failure reason
sum by (response_flags, destination_service_name) (
  rate(istio_requests_total{response_code=~"5..",response_flags!~"-|"}[5m])
)

# Circuit breaker trips (UO)
sum by (destination_service_name) (
  rate(istio_requests_total{response_flags="UO"}[5m])
)
```

---

## How Metrics Interrelate (Correlation Map)

```
istio_requests_total ─────────────────────────────────────────────────
     │ (rate = request rate — R in RED)
     │ (filter response_code=~"5.." = error rate — E in RED)
     │
     ├── istio_request_duration_milliseconds
     │      (p99 = latency — D in RED)
     │      (correlate spike with response_flags to find mesh-layer cause)
     │
     ├── istio_request_bytes / istio_response_bytes
     │      (payload size — cost driver, bandwidth saturation)
     │
     ├── response_flags label
     │      (UO → check DestinationRule circuit breaker)
     │      (UH → check endpoint health / readiness)
     │      (NR → check VirtualService routing)
     │      (UT → check timeout config vs actual upstream latency)
     │
     └── connection_security_policy
            (mutual_tls → healthy mTLS)
            (none → mTLS NOT enforced — security gap!)
            └── istio_agent_cert_expiry_seconds
                   (low value → cert about to expire → mTLS will break)

istio_tcp_connections_opened_total
     │ (connection rate — L4 health)
     │
     ├── istio_tcp_sent_bytes_total / istio_tcp_received_bytes_total
     │      (throughput — correlate with connection count for avg size)
     │
     └── cross-reference with istio_requests_total
            (if HTTP rate drops but TCP connections stay high → connection reuse issue)

istio_dns_requests_total
     │
     ├── istio_dns_upstream_failures_total
     │      (failure rate / total rate = DNS error ratio)
     │
     └── istio_dns_upstream_request_duration_seconds
            (slow DNS → slow first request to new service)
```

### Correlation with OTel traces (Tempo)

Istio metrics show **aggregate** RED; OTel traces show **individual** request paths.
When Istio metrics detect a latency spike:

1. Identify time window + destination from `istio_request_duration_milliseconds`
2. Search Tempo: `{resource.service.name="<destination>"} | duration > 1s`
3. The trace reveals which **internal span** caused the delay

This is why both exist — mesh metrics for alerting (100% coverage), traces for
root cause (individual request detail).

---

## Symptom → Metric Quick-Reference

| Symptom | First query | What to look for |
|---------|-------------|------------------|
| **5xx spike for service X** | `sum(rate(istio_requests_total{destination_service_name="X",response_code=~"5.."}[5m])) by (response_code, response_flags)` | `response_flags` tells you mesh vs app: `-` = app returned 5xx; `UH`/`UF` = mesh couldn't reach upstream |
| **Latency increase** | `histogram_quantile(0.99, sum(rate(istio_request_duration_milliseconds_bucket{destination_service_name="X"}[5m])) by (le, source_workload))` | Identify which caller sees latency; if all callers → upstream issue; if one caller → client-side or routing |
| **Circuit breaker tripping** | `sum(rate(istio_requests_total{response_flags="UO",destination_service_name="X"}[5m]))` | Non-zero = circuit breaker active. Check `DestinationRule` `connectionPool` settings |
| **No route errors** | `sum(rate(istio_requests_total{response_flags="NR"}[5m])) by (destination_service)` | VirtualService or DestinationRule misconfigured or not applied |
| **mTLS not enforced** | `sum(istio_requests_total{connection_security_policy="none"}) by (destination_service_name)` | Security gap — PeerAuthentication not in STRICT mode or traffic bypassing mesh |
| **mTLS cert about to expire** | `istio_agent_cert_expiry_seconds - time() < 86400` | Cert expires in <24h. Istiod not rotating — check istiod health. |
| **TCP connection storm** | `sum(rate(istio_tcp_connections_opened_total{destination_service_name="X"}[5m]))` | High connection open rate = no keep-alive or connection pool exhaustion |
| **DNS failures in Ambient** | `sum(rate(istio_dns_upstream_failures_total[5m]))` | Ambient DNS proxy can't resolve upstream — check CoreDNS health |
| **Control plane disconnect** | `sum(rate(istio_xds_connection_terminations_total[5m]))` | Agent losing xDS stream — istiod overloaded or network issues |
| **Unknown traffic (no mTLS)** | `sum(rate(istio_requests_total{connection_security_policy!="mutual_tls"}[5m])) by (source_workload, destination_service_name)` | Identify which traffic paths are not secured |
| **gRPC streaming imbalance** | `rate(istio_request_messages_total{destination_service_name="X"}[5m]) / rate(istio_response_messages_total{destination_service_name="X"}[5m])` | Ratio far from expected means client or server streaming stall |

---

## MetricsQL / PromQL Examples

### RED dashboard (per service)

```promql
# Request rate
sum(rate(istio_requests_total{destination_service_name="$service",reporter="destination"}[5m]))

# Error rate (%)
sum(rate(istio_requests_total{destination_service_name="$service",reporter="destination",response_code=~"5.."}[5m]))
/
sum(rate(istio_requests_total{destination_service_name="$service",reporter="destination"}[5m]))
* 100

# p99 latency
histogram_quantile(0.99,
  sum(rate(istio_request_duration_milliseconds_bucket{destination_service_name="$service",reporter="destination"}[5m])) by (le)
)
```

### mTLS cert expiry alert

```promql
# Alert when any cert expires within 24 hours
(istio_agent_cert_expiry_seconds - time()) < 86400
```

### Top error sources by response_flags

```promql
topk(10,
  sum by (destination_service_name, response_flags) (
    rate(istio_requests_total{response_flags!~"-|",reporter="destination"}[5m])
  )
)
```

### Reporter label — choosing source vs destination

```promql
# Use reporter="destination" for server-side view (recommended for RED):
# - Captures all callers to a service in one query
# - connection_security_policy is accurate (shows mutual_tls when mTLS active)

# Use reporter="source" for client-side view:
# - Shows what a specific caller sees
# - connection_security_policy will be "unknown" (source can't observe its own TLS)
# - Useful for debugging from a caller's perspective
```

### TCP throughput per service pair

```promql
sum by (source_workload, destination_service_name) (
  rate(istio_tcp_sent_bytes_total[5m])
) / 1024 / 1024  # MB/s
```

---

## Alerting Recommendations

| Alert | Expression | Severity |
|-------|-----------|----------|
| High error rate | `sum(rate(istio_requests_total{response_code=~"5..",reporter="destination"}[5m])) by (destination_service_name) / sum(rate(istio_requests_total{reporter="destination"}[5m])) by (destination_service_name) > 0.05` | warning (>5%) |
| Circuit breaker active | `sum(rate(istio_requests_total{response_flags="UO"}[5m])) by (destination_service_name) > 0` | warning |
| mTLS cert expiry <24h | `(istio_agent_cert_expiry_seconds - time()) < 86400` | critical |
| No healthy upstream | `sum(rate(istio_requests_total{response_flags="UH"}[5m])) by (destination_service_name) > 0` | critical |
| xDS disconnections | `sum(rate(istio_xds_connection_terminations_total[5m])) > 5` | warning |

---

## Ambient Mode Considerations

1. **ztunnel reports L4 only** — TCP metrics and `connection_security_policy` come
   from ztunnel. HTTP-level metrics (duration, response_code, response_flags) require
   a **waypoint proxy** to be deployed for that namespace/service.

2. **reporter label in Ambient** — In sidecar mode, both source and destination have
   a sidecar, so both sides report. In Ambient, reporting depends on waypoint
   placement. If only the destination has a waypoint, you primarily get
   `reporter="destination"`. If neither has a waypoint, you only get L4 (TCP) metrics
   from ztunnel.

3. **SNAT impact** — ztunnel performs SNAT; this means `source_workload` attribution
   relies on HBONE metadata, not IP. If metadata propagation fails, source labels
   may show as `unknown`. See `istio-ambient-debugging` skill.

4. **No per-pod sidecar overhead** — Ambient removes per-pod resource cost of
   sidecars. But fewer reporting points means you MUST deploy waypoints for
   namespace/services where you need L7 observability.

---

## Reference

- [Istio Standard Metrics (v1.30)](https://istio.io/latest/docs/reference/config/metrics/)
- [Envoy Response Flags](https://www.envoyproxy.io/docs/envoy/latest/configuration/observability/access_log/usage)
- [Istio Ambient Architecture](https://istio.io/latest/docs/ambient/architecture/)
- Related skills: `istio-ambient-otel`, `istio-ambient-debugging`, `trace-derived-metrics`
