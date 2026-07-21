---
name: cert-manager-metrics
description: "Use when diagnosing cert-manager certificate lifecycle issues — expiring certs, failed issuance, ACME rate limits, controller reconciliation backlog, or aws-privateca-issuer health. Covers certmanager_certificate_*, certmanager_controller_sync_*, certmanager_acme_client_*, certmanager_clock_*, workqueue_*, and aws-privateca-issuer controller-runtime metrics. Grounded on Helm chart jetstack/cert-manager v1.20.1 + awspca/aws-privateca-issuer 1.4.0."
keywords: [cert-manager, cert, manager, "cert manager", acme]
---
# cert-manager Prometheus Metrics Catalog

Platform certificate management health metrics for **cert-manager v1.20.1** (Helm chart `jetstack/cert-manager`) and **aws-privateca-issuer v1.4.0** (Helm chart `awspca/aws-privateca-issuer`).

**Question answered**: "Are certificates being issued, renewed, and remaining valid — or silently expiring?"

---

## Scrape Pipeline

```
cert-manager controller Pod :9402/metrics ─┐
cert-manager webhook Pod :9402/metrics ────┼─→ vmagent (ServiceMonitor) ─→ VictoriaMetrics
cert-manager cainjector Pod :9402/metrics ──┘
aws-privateca-issuer Pod :8080/metrics ─────→ vmagent (ServiceMonitor) ─→ VictoriaMetrics
```

**How enabled**: Helm value `prometheus.enabled: true` + `prometheus.servicemonitor.enabled: true` (cert-manager). For aws-privateca-issuer: `serviceMonitor.create: true`.

**Components emitting metrics**:
- **Controller** (primary): bespoke `certmanager_*` metrics + `workqueue_*` + `go_*`
- **Webhook**: controller-runtime metrics + `go_*`
- **CA Injector**: controller-runtime metrics + `go_*`
- **aws-privateca-issuer**: controller-runtime metrics (`controller_runtime_reconcile_*`, `workqueue_*`) + `go_*`

> ⚠️ **v1.19+ metric rename**: `certmanager_http_acme_client_request_count` → `certmanager_acme_client_request_count` (dropped `http_` prefix). The `path` label was removed, replaced with `action`. Deployed alerts already use the new name.

---

## 1. Certificate Lifecycle (Controller)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `certmanager_certificate_ready_status` | Gauge | Whether a Certificate is in Ready condition (`1` = condition matches) | Core cert health — filter `condition!="True"` to find broken certs | `name`, `namespace`, `condition`, `exported_namespace` |
| `certmanager_certificate_expiration_timestamp_seconds` | Gauge | Unix timestamp when the certificate expires | Compare to `time()` — alert if `< 21 days` remaining (deployed rule) | `name`, `namespace`, `exported_namespace` |
| `certmanager_certificate_renewal_timestamp_seconds` | Gauge | Unix timestamp when cert-manager plans to renew | If `renewal > expiration` something is wrong; should be ~30 days before expiry | `name`, `namespace` |
| `certmanager_certificate_challenge_status` | Gauge | Current status of ACME challenges for a Certificate (added v1.19) | Track pending/failed challenges blocking issuance | `name`, `namespace`, `type`, `status` |

---

## 2. Controller Sync Activity

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `certmanager_controller_sync_call_count` | Counter | Number of sync() calls by controller name | Reconciliation throughput — sudden drop = controller stuck | `controller` |

---

## 3. ACME Client Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `certmanager_acme_client_request_count` | Counter | Total ACME HTTP requests made | Filter `status="429"` for rate-limit hits (deployed alert fires on this) | `scheme`, `host`, `action`, `method`, `status` |
| `certmanager_acme_client_request_duration_seconds` | Summary | Latency of outbound ACME requests | High p99 = DNS/network issues reaching Let's Encrypt / ACME provider | `scheme`, `host`, `action`, `method`, `status` |

> **Label `action`** (bounded, replaced unbounded `path` in v1.19): logical ACME operation (e.g. `new-acct`, `new-order`, `authz`, `challenge`, `finalize`, `cert`).

---

## 4. Clock Metric

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `certmanager_clock_time_seconds` | Gauge | Current controller clock time (Unix seconds) | Detects clock skew on nodes — compare to `time()`; drift > 5s is problematic for cert validation | — |

---

## 5. Controller Workqueue Metrics (client-go)

Standard Kubernetes client-go workqueue metrics emitted by the controller:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Current items in the queue | High depth = controller falling behind (reconciliation backlog) | `name` |
| `workqueue_adds_total` | Counter | Total items added to queue | Rate indicates reconciliation demand | `name` |
| `workqueue_retries_total` | Counter | Total retries | High retry rate = persistent reconciliation failures | `name` |
| `workqueue_queue_duration_seconds` | Histogram | Time items wait in queue before processing | High latency = controller overloaded | `name`, `le` |
| `workqueue_work_duration_seconds` | Histogram | Time spent processing a single item | High = slow reconciliation (network, API calls, ACME) | `name`, `le` |
| `workqueue_longest_running_processor_seconds` | Gauge | Longest currently running processor duration | Stuck reconciliation loop | `name` |
| `workqueue_unfinished_work_seconds` | Gauge | Seconds of unfinished work in the queue | Growing = controller cannot keep up | `name` |

**Queue names** (controller-specific, use as `name` filter): `certificates-readiness`, `certificates-trigger`, `certificates-issuing`, `certificates-key-manager`, `certificaterequests-*`, `orders`, `challenges`, `clusterissuers`, `issuers`.

---

## 6. aws-privateca-issuer Metrics (controller-runtime)

The aws-privateca-issuer (v1.4.0) emits standard controller-runtime metrics on `:8080/metrics`:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations by controller | Filter `result="error"` for persistent issuer failures | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Total reconciliation errors | Non-zero sustained = issuer cannot sign (IAM, PCA connectivity) | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Reconciliation duration | High latency = slow AWS PCA API calls | `controller`, `le` |
| `workqueue_depth` | Gauge | Queue depth per controller | Backlog of CertificateRequest signing | `name` |
| `workqueue_retries_total` | Counter | Retries per controller | High retries = IAM or PCA auth issues | `name` |

---

## 7. Go Runtime Metrics

Both cert-manager and aws-privateca-issuer expose standard `go_*` and `process_*` metrics. See `go-apm-metrics` skill for the full reference. Key ones for cert-manager specifically:

| Metric Name | Type | Troubleshooting Use |
|---|---|---|
| `go_goroutines` | Gauge | Goroutine leak in controller (should be stable ~50-200) |
| `go_memstats_alloc_bytes` | Gauge | Memory growth indicating leak |
| `process_resident_memory_bytes` | Gauge | Compare to container memory limit |
| `process_cpu_seconds_total` | Counter | CPU usage rate |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Query Pattern |
|---------|------------------------|---------------|
| Certificate not ready | `certmanager_certificate_ready_status{condition!="True"}` | Identify which certs + namespace |
| Certificate expiring soon | `certmanager_certificate_expiration_timestamp_seconds - time() < 21*24*3600` | Deployed alert `Cert-ManagerCertificateExpiringSoon` |
| ACME rate limiting | `rate(certmanager_acme_client_request_count{status="429"}[5m]) > 0` | Deployed alert `Cert-ManagerHittingACMERateLimits` |
| Controller not running | `absent(up{job="cert-manager"})` | Deployed alert `Cert-ManagerAbsent` |
| Reconciliation backlog | `workqueue_depth{name=~"certificates.*"} > 50` | Queue growing = controller overloaded |
| Slow issuance | `workqueue_work_duration_seconds{name="certificates-issuing"}` p99 | Slow ACME or PCA calls |
| aws-privateca-issuer errors | `controller_runtime_reconcile_total{result="error"}` on PCA issuer | IAM role / PCA availability |
| Clock skew | `abs(certmanager_clock_time_seconds - time()) > 5` | Node NTP issues |
| Challenge stuck | `certmanager_certificate_challenge_status{status!="valid"}` | DNS propagation / solver issues |

---

## Deployed Alert Rules (PrometheusRule `cert-manager-rules`)

| Alert | Expression | Severity | What It Catches |
|-------|-----------|----------|-----------------|
| `Cert-ManagerAbsent` | `absent(up{job="cert-manager"})` for 10m | critical | Controller completely down |
| `Cert-ManagerCertificateExpiringSoon` | `certmanager_certificate_expiration_timestamp_seconds - time() < 21d` for 1h | warning | Cert renewal not happening |
| `Cert-ManagerCertificateNotReady` | `certmanager_certificate_ready_status{condition!="True"} == 1` for 10m | critical | Cert in failed state |
| `Cert-ManagerHittingACMERateLimits` | `rate(certmanager_acme_client_request_count{status="429"}[5m]) > 0` for 5m | critical | Let's Encrypt rate limit hit |

---

## Version Grounding

| Component | Chart | Version | App Version | Port |
|-----------|-------|---------|-------------|------|
| cert-manager | `jetstack/cert-manager` | `v1.20.1` | v1.20.1 | `:9402` (controller, webhook, cainjector) |
| aws-privateca-issuer | `awspca/aws-privateca-issuer` | `1.4.0` | ~1.4.0 | `:8080` |

**Key version notes**:
- v1.19 removed `path` label from ACME metrics, added `action` (bounded cardinality)
- v1.19 renamed `certmanager_http_acme_client_request_*` → `certmanager_acme_client_request_*`
- v1.19 added `certmanager_certificate_challenge_status`
- v1.19 moved certificate metrics to collector approach (PR #7856)
- v1.20.1 inherits all the above

---

## Complements

- `go-apm-metrics` — full Go runtime metrics reference (both controllers are Go)
- `collector-internal-metrics` — if certs are for OTel Collector mTLS
- `victoriametrics-troubleshooting` — if metrics storage itself has issues

## Sources

- [cert-manager Prometheus Metrics docs](https://cert-manager.io/docs/usage/prometheus-metrics/)
- [cert-manager v1.19 Release Notes](https://cert-manager.io/docs/releases/release-notes/release-notes-1.19/) — metric rename details
- [cert-manager source: pkg/metrics/metrics.go](https://github.com/cert-manager/cert-manager/blob/master/pkg/metrics/metrics.go)
- [aws-privateca-issuer GitHub](https://github.com/cert-manager/aws-privateca-issuer)
- Deployed helmfile: `k8s-setup/cert-manager/helmfile.yaml.gotmpl`
- Deployed values: `k8s-setup/cert-manager/cert-manager/values.yaml.gotmpl`
- Deployed rules: `k8s-setup/cert-manager/cert-manager/prometheus.rules.yaml`
