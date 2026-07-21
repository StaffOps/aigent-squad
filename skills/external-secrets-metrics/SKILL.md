---
name: external-secrets-metrics
description: >   Use when diagnosing External Secrets Operator health — sync failures,   provider API errors, reconciliation latency, store readiness. Covers   externalsecret_*, secretstore_*, clustersecretstore_*, pushsecret_*,   clusterexternalsecret_*, externalsecret_provider_api_calls_count, plus   controller-runtime controller_runtime_reconcile_*, workqueue_*,   rest_client_*, controller_runtime_webhook_*. Complement (do NOT duplicate)   skills/infrastructure/external-secrets-aws-sm (config/CRD patterns).   Grounded on Helm chart external-secrets/external-secrets 0.17.0.
keywords: [external-secrets, external, secrets, "external secrets", crd]
---
# External Secrets Operator — Prometheus Metrics Catalog

Metrics emitted by the **External Secrets Operator (ESO) v0.17.0** controller,
webhook, and cert-controller components.

**Grounded on**: Helm chart `external-secrets/external-secrets` version **0.17.0**
deployed via helmfile in `k8s-setup/external-secrets/external-secrets`. ServiceMonitor
enabled (`serviceMonitor.enabled: true`).

---

## Scrape Pipeline

```
ESO Controller (:8080/metrics)  ─┐
ESO Webhook (:8080/metrics)     ─┼─→ vmagent (ServiceMonitor) → VictoriaMetrics
ESO CertController (:8080/metrics)┘
```

**How metrics are enabled**: Set `serviceMonitor.enabled: true` in Helm values
(already set in the deployed config). This creates a `ServiceMonitor` CR that
vmagent discovers. Additionally, `webhook.metrics.service.enabled` and
`certController.metrics.service.enabled` can expose separate `/metrics` services.

The controller exports:
1. **ESO-specific metrics** — prefixed with `externalsecret_`, `secretstore_`,
   `clustersecretstore_`, `clusterexternalsecret_`, `pushsecret_`.
2. **Controller-runtime metrics** — inherited from kubebuilder: `controller_runtime_*`,
   `workqueue_*`, `rest_client_*`.
3. **Go runtime metrics** — standard `go_*` (see `go-apm-metrics` skill).

---

## 1. ExternalSecret Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `externalsecret_sync_calls_total` | Counter | Total sync calls for ExternalSecrets | Baseline reconciliation throughput; rate drop = controller stuck | `name`, `namespace` |
| `externalsecret_sync_calls_error` | Counter | Total sync errors for ExternalSecrets | **KEY** — non-zero rate = secrets not being delivered. Check provider connectivity, IAM, rate limits | `name`, `namespace` |
| `externalsecret_status_condition` | Gauge | Status condition of a specific ExternalSecret (1=true, 0=false) | Detect ES stuck in non-Ready state; filter by `condition` label | `name`, `namespace`, `condition`, `status` |
| `externalsecret_reconcile_duration` | Gauge | Duration of the last reconciliation (seconds) | High values = provider latency or complex templating; correlate with API call metrics | `name`, `namespace` |
| `externalsecret_provider_api_calls_count` | Counter | Number of API calls to upstream secret provider | Track provider quota usage; identify chatty secrets; high rate may hit AWS SM throttling | `provider`, `call`, `status` |

---

## 2. SecretStore & ClusterSecretStore Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `secretstore_status_condition` | Gauge | Status condition of a SecretStore (1=true, 0=false) | Detect stores that lost connectivity to provider (IAM expiry, network) | `name`, `namespace`, `condition`, `status` |
| `secretstore_reconcile_duration` | Gauge | Duration to reconcile a SecretStore | High value = provider health-check slow (auth, network) | `name`, `namespace` |
| `clustersecretstore_status_condition` | Gauge | Status condition of a ClusterSecretStore (1=true, 0=false) | Same as above but cluster-scoped; all namespaces affected if unhealthy | `name`, `condition`, `status` |
| `clustersecretstore_reconcile_duration` | Gauge | Duration to reconcile a ClusterSecretStore | Same diagnostic as secretstore variant | `name` |

---

## 3. ClusterExternalSecret & PushSecret Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `clusterexternalsecret_status_condition` | Gauge | Status condition of a ClusterExternalSecret | Detect CES not propagating to target namespaces | `name`, `condition`, `status` |
| `clusterexternalsecret_reconcile_duration` | Gauge | Duration to reconcile a ClusterExternalSecret | Slow = many target namespaces or complex selectors | `name` |
| `pushsecret_status_condition` | Gauge | Status condition of a PushSecret | Detect push failures to external provider | `name`, `namespace`, `condition`, `status` |
| `pushsecret_reconcile_duration` | Gauge | Duration to reconcile a PushSecret | High value = provider write latency or auth issues | `name`, `namespace` |

---

## 4. Controller-Runtime Metrics (inherited)

These are emitted by the underlying controller-runtime framework (kubebuilder).

### Reconciliation

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations per controller | Rate = controller throughput; `result="error"` = reconcile failures | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Total reconciliation errors per controller | **KEY** — sustained non-zero = controller cannot process resources | `controller` |
| `controller_runtime_reconcile_time_seconds_bucket` | Histogram | Distribution of reconciliation durations | p99 high = controller overloaded or provider slow | `controller`, `le` |

### Workqueue

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Current items waiting in workqueue | **KEY** — growing depth = controller cannot keep up; secrets delivery delayed | `name` |
| `workqueue_adds_total` | Counter | Total items added to workqueue | Rate = incoming work; spike = mass secret updates or label change | `name` |
| `workqueue_queue_duration_seconds_bucket` | Histogram | Time items spend waiting in queue | High p99 = starved controller (CPU/memory limits too low?) | `name`, `le` |
| `workqueue_work_duration_seconds_bucket` | Histogram | Time spent processing items | High p99 = slow provider calls during reconcile | `name`, `le` |
| `workqueue_unfinished_work_seconds` | Gauge | Seconds of in-progress work not yet observed | Large = stuck reconciliation threads | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of longest active processor | Very high = single resource stuck (e.g., provider timeout) | `name` |
| `workqueue_retries_total` | Counter | Total retries handled by workqueue | High rate = frequent transient failures (network, rate limits) | `name` |

### Webhook

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_webhook_requests_total` | Counter | Total HTTP requests to webhook | Split by `code`; 500s = webhook errors cascading to kube-apiserver | `webhook`, `code` |
| `controller_runtime_webhook_latency_seconds_bucket` | Histogram | Webhook request latency | High p99 = webhook pod under-resourced or cert issues | `webhook`, `le` |

### REST Client (kube-apiserver calls)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | HTTP requests to kube-apiserver | High 429s = API throttling; 5xx = apiserver issues | `code`, `host`, `method` |
| `rest_client_request_duration_seconds_bucket` | Histogram | Latency of kube-apiserver calls | High latency = apiserver overloaded or network issues | `host`, `method`, `le` |

---

## 5. Go Runtime Metrics

Standard `go_*` metrics (goroutines, GC, memory). See **`go-apm-metrics`** skill
for full catalog — do not duplicate here.

Key ones for ESO troubleshooting:
- `go_goroutines` — goroutine leak in controller
- `go_memstats_alloc_bytes` — memory growth (OOMKill risk)
- `go_gc_duration_seconds` — GC pressure

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Next Steps |
|---------|------------------------|------------|
| Secrets not syncing | `externalsecret_sync_calls_error` rate, `externalsecret_status_condition{condition="Ready",status="False"}` | Check provider API calls: `externalsecret_provider_api_calls_count{status!="200"}` |
| SecretStore not ready | `secretstore_status_condition{condition="Ready",status="False"}` or `clustersecretstore_status_condition` | Check IAM (IRSA annotation), network, provider health |
| High reconcile latency | `externalsecret_reconcile_duration` or `controller_runtime_reconcile_time_seconds` p99 | Check `workqueue_work_duration_seconds` + provider API latency |
| Workqueue growing | `workqueue_depth` sustained > 0 | Check controller CPU/memory limits, `controller_runtime_reconcile_errors_total` |
| Webhook errors | `controller_runtime_webhook_requests_total{code="500"}` | Check cert-controller logs, cert expiry, webhook pod resources |
| Provider throttled | `externalsecret_provider_api_calls_count{status="429"}` or `rest_client_requests_total{code="429"}` | Reduce `refreshInterval` on ExternalSecrets, or increase provider quota |
| Controller stuck | `workqueue_longest_running_processor_seconds` very high | Check pod logs for stuck goroutine; may need restart |
| Secrets delivery delayed | `workqueue_queue_duration_seconds` p99 high | Controller starved — scale up resources or reduce reconcile load |

---

## Useful PromQL / MetricsQL Queries

### Error rate (% of syncs failing)

```promql
sum(rate(externalsecret_sync_calls_error[5m]))
/
sum(rate(externalsecret_sync_calls_total[5m]))
```

### ExternalSecrets not Ready

```promql
externalsecret_status_condition{condition="Ready", status="False"} == 1
```

### Provider API error rate

```promql
sum by (provider, call) (
  rate(externalsecret_provider_api_calls_count{status!~"2.."}[5m])
)
```

### Controller reconcile error rate

```promql
sum(increase(
  controller_runtime_reconcile_total{service=~"external-secrets.*", result="error"}[5m]
)) by (controller)
```

### Workqueue saturation

```promql
sum(workqueue_depth{service=~"external-secrets.*"}) by (name)
```

### Webhook p99 latency

```promql
histogram_quantile(0.99,
  sum(rate(controller_runtime_webhook_latency_seconds_bucket{service=~"external-secrets.*"}[5m])) by (le)
)
```

---

## SLI Recommendations (from official ESO docs)

| SLI | Query Pattern | Target |
|-----|---------------|--------|
| Webhook error rate | `sum(rate(webhook_requests{code="500"})) / sum(rate(webhook_requests))` | < 0.1% |
| Webhook p99 latency | `histogram_quantile(0.99, webhook_latency_seconds)` | < 1s |
| Workqueue depth | `workqueue_depth > 0 for > 5m` | Alert if sustained |
| Reconcile p99 latency | `histogram_quantile(0.99, reconcile_time_seconds)` | < 30s |
| Reconcile error rate | `rate(reconcile_total{result="error"}) / rate(reconcile_total)` | < 1% |

---

## Version Notes

- **Chart version**: `external-secrets/external-secrets` **0.17.0** (deployed in
  dev, prd, and core-devops clusters).
- **Metric naming**: ESO uses custom metrics registered via `prometheus/client_golang`
  (not controller-runtime's custom metric registration). Names are stable since v0.7.0+.
- `externalsecret_provider_api_calls_count`: added in ESO v0.8.0+; provides
  `provider` (e.g., `aws`), `call` (e.g., `GetSecretValue`), and `status` labels.
- Controller-runtime metrics follow kubebuilder conventions. The `controller` label
  on reconcile metrics identifies which ESO controller emitted it (e.g.,
  `externalsecret`, `secretstore`, `clustersecretstore`).
- Note: after a controller restart, `workqueue_depth` will temporarily equal the
  total number of ExternalSecret resources (full re-reconciliation). Define alert
  thresholds based on sustained depth, not spikes.

---

## Related Skills

- `external-secrets-aws-sm` — ExternalSecret/SecretStore CRD configuration,
  refresh intervals, IRSA setup (config, not metrics)
- `go-apm-metrics` — Go runtime metrics (`go_*`) emitted by ESO pods
- `collector-internal-metrics` — if OTel Collector scrapes ESO (pipeline health)
- `k8s-workload-metrics` — pod CPU/memory/restart metrics for ESO pods

---

## Sources

- [External Secrets Operator — Metrics (official docs, main branch)](https://external-secrets.io/main/api/metrics/)
- [Controller-Runtime Metrics Reference (kubebuilder)](https://book.kubebuilder.io/reference/metrics-reference)
- Deployed Helm values: `k8s-setup/external-secrets/external-secrets/values.yaml.gotmpl`
- Helmfile: `k8s-setup/external-secrets/helmfile.yaml.gotmpl` (chart version 0.17.0)
