---
name: k8s-pvc-tagger-metrics
description: "Use when diagnosing k8s-pvc-tagger volume tagging operations — tag application success/failure rates, ignored PVCs, invalid tag annotations, and legacy EBS tagger counters. Covers k8s_pvc_tagger_*, k8s_aws_ebs_tagger_* (legacy), plus go_* and process_*. Grounded on Helm chart mtougeron/k8s-pvc-tagger 2.3.1 (appVersion ~v1.3.x), source confirmed from github.com/mtougeron/k8s-pvc-tagger main.go."
keywords: [k8s-pvc-tagger, k8s, pvc, tagger, "k8s pvc", "pvc tagger", ebs, go]
---
# k8s-pvc-tagger Metrics

Controller that watches PersistentVolumeClaims and applies AWS tags (EBS/EFS) to
the underlying cloud volumes based on PVC annotations (`k8s-pvc-tagger/tags`),
labels (`--copy-labels`), and default tags (`--default-tags`).

**Question answered**: "Are PVC volumes getting tagged correctly, or are tagging
operations silently failing?"

---

## Scrape Pipeline

```
k8s-pvc-tagger pod (:8001/metrics)
  → ServiceMonitor (serviceMonitor: true in values)
  → vmagent scrape
  → VictoriaMetrics
```

- **Metrics port**: `8001` (configurable via `--metrics-port` flag)
- **Health port**: `8000` (configurable via `--status-port` flag, `/healthz`)
- **Enabled by**: `serviceMonitor: true` in Helm values (deployed: ✅)
- **Library**: `github.com/prometheus/client_golang` — exposes custom counters + standard Go runtime (`go_*`, `process_*`, `promhttp_*`)

**Deployed version**: Helm chart `mtougeron/k8s-pvc-tagger` **2.3.1**
**Namespace**: `kube-system`
**Replicas**: 2 (leader election via K8s Lease)

---

## Custom Metrics (k8s-pvc-tagger application)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `k8s_pvc_tagger_actions_total` | Counter | Total PVC tagging operations attempted | Core operational metric — rate by `status` shows success vs failure. Spike in `status="error"` = AWS API issues or permission problems. | `status` (`success`, `error`), `storageclass` |
| `k8s_pvc_tagger_pvc_ignored_total` | Counter | PVCs skipped due to `k8s-pvc-tagger/ignore` annotation or non-matching volume type | High rate is normal if many PVCs use the ignore annotation. Unexpected growth = review annotation configuration. | `storageclass` |
| `k8s_pvc_tagger_invalid_tags_total` | Counter | PVCs with malformed `k8s-pvc-tagger/tags` annotation (unparseable JSON/CSV) | Non-zero = user misconfiguration; check PVC annotations. Correlate with specific storageclass. | `storageclass` |

### Legacy Metrics (aws-ebs-tagger era)

Retained for backward compatibility from pre-rename (`k8s-aws-ebs-tagger`). May be removed in future versions.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `k8s_aws_ebs_tagger_actions_total` | Counter | Legacy counter of EBS tagging actions | Use new `k8s_pvc_tagger_actions_total` instead; this exists for old dashboards. | `status` |
| `k8s_aws_ebs_tagger_pvc_ignored_total` | Counter | Legacy counter of ignored PVCs | Prefer `k8s_pvc_tagger_pvc_ignored_total`. | — |
| `k8s_aws_ebs_tagger_invalid_tags_total` | Counter | Legacy counter of invalid tags | Prefer `k8s_pvc_tagger_invalid_tags_total`. | — |

---

## Go Runtime & Process Metrics

Since `k8s-pvc-tagger` uses `client_golang`, the standard Go runtime collectors are automatically exposed. These are **fully documented in the `go-apm-metrics` skill** — do not duplicate here.

Key ones for this lightweight controller:

| Metric Name | Type | Relevance to k8s-pvc-tagger |
|---|---|---|
| `go_goroutines` | Gauge | Should be low and stable (PVC watchers + leader election). Leak = informer issue. |
| `go_memstats_alloc_bytes` | Gauge | Low-memory controller (128Mi limit). Approaching limit = risk of OOMKill. |
| `process_resident_memory_bytes` | Gauge | Actual RSS — compare vs container limit. |
| `process_open_fds` | Gauge | K8s API watchers hold FDs; unexpected growth = handle leak. |
| `promhttp_metric_handler_requests_total` | Counter | Scrape success; `code="500"` = internal issue on metrics endpoint. |

---

## Troubleshooting Quick Reference

| Symptom | First Metric to Check | Likely Cause |
|---|---|---|
| New PVCs not getting tagged | `rate(k8s_pvc_tagger_actions_total{status="error"}[5m])` | IRSA role misconfigured, AWS API throttling, or volume not yet provisioned |
| Tags appearing on wrong volumes | N/A (metric won't show this) | Check `--default-tags` and annotation values on PVCs |
| High `invalid_tags_total` rate | `rate(k8s_pvc_tagger_invalid_tags_total[5m])` by `storageclass` | Teams using wrong annotation format (CSV vs JSON mismatch) |
| Controller pod OOMKill | `process_resident_memory_bytes` / `go_memstats_alloc_bytes` | Unlikely at 128Mi for this controller; check goroutine leak or informer cache explosion if watching all namespaces |
| No metrics being scraped | `up{job=~".*pvc-tagger.*"}` | ServiceMonitor label mismatch, port wrong, or pod not Running |
| Leader election issues | Check pod logs for "leader lost" / "new leader elected" | Two replicas compete for Lease in `kube-system`; only leader actively tags |

### Useful Queries

```promql
# Tagging success rate (should be ~100%)
sum(rate(k8s_pvc_tagger_actions_total{status="success"}[5m]))
/
sum(rate(k8s_pvc_tagger_actions_total[5m]))

# Error rate by storageclass
sum by (storageclass) (rate(k8s_pvc_tagger_actions_total{status="error"}[5m]))

# Invalid tags — which storageclasses have misconfigured PVCs?
sum by (storageclass) (rate(k8s_pvc_tagger_invalid_tags_total[5m]))

# Memory usage vs limit (128Mi = 134217728 bytes)
process_resident_memory_bytes{job=~".*pvc-tagger.*"} / 134217728
```

---

## Deployment Notes

- **IRSA**: Pod uses `eks.amazonaws.com/role-arn` annotation to assume `K8sPvcTaggerRole-<cluster>` for EC2/EFS tagging API calls.
- **Leader election**: 2 replicas with K8s Lease lock (`k8s-pvc-tagger` in `kube-system`). Only the leader actively watches PVCs and applies tags.
- **Scope**: Watches all namespaces by default (no `--watch-namespace` set in deployed values).
- **Cloud mode**: AWS (default `--cloud aws`).

---

## Complements

- **`go-apm-metrics`** — Full Go runtime metric reference (`go_*`, `go_memstats_*`, `go_gc_*`); all present on this controller's `/metrics` endpoint.
- **`k8s-workload-metrics`** — Pod-level resource metrics (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`) for capacity monitoring of the controller itself.

---

## Sources

- **Deployed chart**: `mtougeron/k8s-pvc-tagger` Helm chart version **2.3.1** (helmfile in `k8s-setup/k8s-pvc-tagger/`)
- **Source code**: [github.com/mtougeron/k8s-pvc-tagger/blob/main/main.go](https://github.com/mtougeron/k8s-pvc-tagger/blob/main/main.go) — metric definitions confirmed from `promauto.NewCounterVec` declarations
- **App version**: ~v1.3.x (latest release v1.3.0 as of 2026-06-29)
- **Library**: `github.com/prometheus/client_golang` (`promhttp.Handler()` on port 8001)
