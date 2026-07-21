---
name: backstage-metrics
description: "Use when assessing Backstage developer portal observability. The deployed chart (backstage/backstage 2.5.2) has metrics.serviceMonitor.enabled=false \u2014 Prometheus metrics are NOT scraped into VictoriaMetrics. The custom image (bdc-images/backstage:latest) MAY expose /metrics if the OTel Metrics Service or legacy prom-client was configured, but there is NO evidence of this and NO ServiceMonitor exists. For workload-level health use k8s-workload-metrics; for Node.js runtime use nodejs-apm-metrics (if prom-client is enabled in a future config)."
keywords: [backstage, node, otel, prometheus, victoriametrics]
---
# Backstage Metrics — Status: NOT SCRAPED

**Grounded on**: Helm chart `backstage/backstage` version **2.5.2** from
`https://backstage.github.io/charts`. Image: `harbor.bigdatacorp.com.br/bdc-images/backstage:latest`.
Backstage stable version: aligned with **v1.52.0** (current stable as of 2026-07).

---

## Current State: Metrics DISABLED

The deployed Backstage instance at `<org>` has **no Prometheus metrics reaching VictoriaMetrics**.

| Config key | Value | Effect |
|---|---|---|
| `metrics.serviceMonitor.enabled` | `false` | No ServiceMonitor CRD created → vmagent does not scrape the pod |
| Image `/metrics` endpoint | **Unknown** (custom image) | Even if exposed, nothing scrapes it |

**Evidence** (from `k8s-setup/backstage/backstage/values.yaml.gotmpl`):
```yaml
metrics:
  serviceMonitor:
    enabled: false
```

### Official Helm Chart Note (upstream `values.yaml`)

> "Note that the /metrics endpoint is NOT present in a freshly scaffolded Backstage app.
> To setup, follow the Prometheus metrics tutorial."

This means the `/metrics` endpoint requires **explicit application-level configuration** — it
is not available by default even if the ServiceMonitor were enabled.

---

## Backstage Metrics Architecture (when enabled)

Backstage has evolved its metrics approach:

| Era | Mechanism | Default port | Notes |
|-----|-----------|------|-------|
| Legacy (pre-2023) | `prom-client` (`collectDefaultMetrics()`) | 7007 `/metrics` | Node.js runtime + custom counters |
| Current (v1.25+) | OpenTelemetry Metrics Service (alpha) | 9464 (OTel Prometheus exporter) | OTel SDK wrapping, scoped by plugin |

The Helm chart's `metrics.serviceMonitor.port` defaults to `http-backend` (7007) but notes that
OpenTelemetry's default Prometheus exporter port is **9464** — the correct port depends on which
mechanism the app uses.

### What WOULD be exposed (if enabled)

If the custom image has the OTel Metrics Service or prom-client configured, the following
metric families would be expected:

#### Node.js prom-client default metrics (legacy path)

See **`nodejs-apm-metrics`** skill for the complete catalog. Key metrics:
- `nodejs_eventloop_lag_seconds` / `nodejs_eventloop_lag_mean_seconds`
- `nodejs_gc_duration_seconds_bucket`
- `nodejs_heap_size_total_bytes` / `nodejs_heap_size_used_bytes`
- `nodejs_active_handles_total` / `nodejs_active_requests_total`
- `process_cpu_seconds_total` / `process_resident_memory_bytes`

#### Backstage backend HTTP metrics (if instrumented)

| Metric Name (expected) | Type | What It Measures | Status |
|---|---|---|---|
| `http_request_duration_seconds` | Histogram | Backend HTTP request latency | ⚠️ UNCONFIRMED — requires explicit instrumentation |
| `http_request_size_bytes` | Histogram | Request payload size | ⚠️ UNCONFIRMED |
| `http_response_size_bytes` | Histogram | Response payload size | ⚠️ UNCONFIRMED |
| `http_requests_total` | Counter | Total backend HTTP requests | ⚠️ UNCONFIRMED |

#### OTel Metrics Service application-level (alpha, if configured)

Plugin-scoped metrics via `@backstage/backend-plugin-api/alpha`:
- `catalog.entities.count` (ObservableGauge)
- Scaffolder task metrics
- Custom plugin counters

These use OTel naming conventions (dots) and would appear in VictoriaMetrics as
underscore-separated if scraped via a Prometheus exporter.

> ⚠️ **ALL of the above are UNCONFIRMED for this deployment.** The custom image build
> (`bdc-images/backstage:latest`) has not been inspected for OTel or prom-client setup.

---

## How to Enable Metrics (Remediation)

To get Backstage metrics into VictoriaMetrics:

### Step 1: Enable metrics in the application

**Option A — prom-client (simpler, legacy)**:
Add `@backstage/plugin-metrics-prometheus` or configure `collectDefaultMetrics()` in the
backend `packages/backend/src/index.ts`.

**Option B — OTel Metrics (recommended, modern)**:
Follow [Backstage OTel tutorial](https://backstage.io/docs/tutorials/setup-opentelemetry) —
register `@opentelemetry/exporter-prometheus` on port 9464.

### Step 2: Enable ServiceMonitor in Helm values

```yaml
metrics:
  serviceMonitor:
    enabled: true
    path: /metrics          # or the OTel exporter path
    port: http-backend      # 7007 for prom-client, or define a custom port for 9464
    interval: 30s
    labels:
      release: victoria-metrics  # match vmagent's servicemonitor selector
```

### Step 3: Verify

```bash
# Check if /metrics responds
kubectl port-forward -n backstage svc/backstage 7007:7007
curl -s http://localhost:7007/metrics | head -20

# Or for OTel port:
kubectl port-forward -n backstage deploy/backstage 9464:9464
curl -s http://localhost:9464/metrics | head -20
```

---

## What IS Available Today (without app-level metrics)

For observability of the Backstage deployment **right now**, use:

| Signal | Source | Skill |
|--------|--------|-------|
| Pod CPU/memory/restarts | kubelet/cadvisor via vmagent | `k8s-workload-metrics` |
| Container OOMKill, CrashLoopBackOff | kube-state-metrics | `k8s-workload-metrics` |
| HTTP traffic (Istio Ambient L4) | ztunnel connection metrics | `istio-ambient-metrics` |
| Ingress traffic (Traefik) | `traefik_*` metrics on ingress | `traefik-metrics` |
| Backing PostgreSQL (RDS) | CloudWatch RDS metrics | AWS CloudWatch / `backing-services-metrics` |
| Node.js runtime (IF prom-client enabled) | prom-client `/metrics` | `nodejs-apm-metrics` |

---

## Troubleshooting Quick Reference

| Symptom | What to Check | Tool |
|---------|---------------|------|
| Backstage UI slow / unresponsive | Pod resource usage (CPU throttling, memory) | `kubectl top pod -n backstage` |
| 5xx errors | Pod logs for stack traces | `kubectl logs -n backstage deploy/backstage` |
| Pod CrashLoopBackOff | Events + previous logs | `kubectl describe pod` + `kubectl logs --previous` |
| Database connection issues | RDS connectivity, secret validity | Check `POSTGRES_HOST` reachability, RDS metrics |
| High memory (Node.js heap) | Container memory vs request/limit | k8s-workload-metrics `container_memory_working_set_bytes` |
| GitLab/LDAP auth failures | Application logs | `kubectl logs -n backstage` filtered for auth errors |

---

## Complements

- **`k8s-workload-metrics`** — pod/container resource metrics (the ONLY metrics available today for Backstage)
- **`nodejs-apm-metrics`** — Node.js prom-client runtime metrics (applicable IF metrics are enabled in future)
- **`traefik-metrics`** — ingress-level HTTP metrics for traffic reaching Backstage
- **`backing-services-metrics`** — PostgreSQL (RDS) health metrics
- **`istio-ambient-metrics`** — L4 mesh metrics if namespace is enrolled

## Sources

- Helm chart: `backstage/backstage` v2.5.2 — [GitHub](https://github.com/backstage/charts)
- Backstage Metrics Service docs (alpha): [backstage.io/docs/backend-system/core-services/metrics](https://backstage.io/docs/backend-system/core-services/metrics)
- Backstage OTel tutorial: [backstage.io/docs/tutorials/setup-opentelemetry](https://backstage.io/docs/tutorials/setup-opentelemetry)
- Deployed config: `k8s-setup/backstage/backstage/values.yaml.gotmpl` (`metrics.serviceMonitor.enabled: false`)
