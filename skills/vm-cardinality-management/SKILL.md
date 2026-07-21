---
name: vm-cardinality-management
description: "VictoriaMetrics cardinality control \u2014 detect and fix high-cardinality explosions. Use when vmselect OOMs, slow inserts increase, or new metrics appear with thousands of series. Covers label removal patterns, BigBoost-style anti-patterns, scrape config tuning."
keywords: [vm-cardinality, vm, cardinality, "vm cardinality", victoriametrics, vmselect]
---
# VictoriaMetrics Cardinality Management

How to detect, diagnose, and remediate high-cardinality issues in VictoriaMetrics.

## Detect high-cardinality metrics

### Top metrics by series count
```promql
topk(20, count by (__name__)({__name__=~".+"}))
```

### TSDB status (cluster-level)
```
GET /api/v1/status/tsdb
```
Returns: top metrics by series count, top labels by value count.

### Symptom: vmselect OOM during query
Common cause: a metric has tens of thousands of series and a 30d range query tries to load them all.

Example real-world incident:
- `BigBoost_SQStoLogStats_Datasets_NumberOfQueries_total` had 85K series due to `UserId` label
- 30-day range query → 2GB requested vs 1.7GB available → OOM
- Fix: remove the offending labels from the source app

## Anti-patterns (NEVER use as labels)

| Bad label | Why |
|-----------|-----|
| `user_id`, `UserId`, `customer_id` | Unbounded user count |
| `request_id`, `trace_id`, `span_id` | Unique per request |
| `email`, `phone`, `ip_address` | PII + high cardinality |
| `timestamp`, `date` | Infinite values |
| Error message with embedded data | Unique per occurrence |
| Raw URL `/api/users/12345/orders/67890` | Use `http.route` template instead |

## Fixing high-cardinality at the source

### Scenario: BigBoost_* metrics (real <org> case)

75 metrics with prefix `BigBoost_*` had labels `UserId`, `JobId`, `DatasetStatus` (with error messages embedded). Metrics were ingested by `BigBoostLogs.SQSStats` consumer of `BIGBOOST_LOG_RECORDS` queue.

**Resolution path:**
1. Identify the source app emitting these
2. Refactor to emit metrics directly with controlled labels (counter per dataset)
3. Keep queue for logging/audit only
4. Once source stops emitting, dead metrics can be deleted

## Removing labels via metric_relabel_configs

For labels that the source can't fix, drop them at scrape time using `metric_relabel_configs` in vmagent.

### Common label categories to drop

#### Istio internal labels
```yaml
metric_relabel_configs:
  - action: labeldrop
    regex: connection_security_policy|destination_canonical_revision|destination_cluster|destination_principal|destination_version|gateway_istio_io_managed|gateway_networking_k8s_io_gateway_class_name|istio_io_dataplane_mode|istio_io_waypoint_for|service_istio_io_canonical_revision|sidecar_istio_io_inject|source_canonical_revision|source_cluster|source_principal|source_version
```

#### ScaleOps labels
```yaml
- action: labeldrop
  regex: scaleops_sh_.*
```

#### Helm chart labels
```yaml
- action: labeldrop
  regex: helm_sh_.*
```

#### Kubernetes metadata (when not needed)
```yaml
- action: labeldrop
  regex: app_kubernetes_io_component|app_kubernetes_io_instance|app_kubernetes_io_managed_by|app_kubernetes_io_name|app_kubernetes_io_part_of|app_kubernetes_io_version|controller_revision_hash|managed_by|label_managed_by|pod_template_generation|pod_template_hash
```

#### High-cardinality identifiers (unless needed)
```yaml
- action: labeldrop
  regex: container|instance
```

### Why these labels appear

Generic `labelmap` action in scrape configs copies ALL pod/node labels:
```yaml
- action: labelmap
  regex: __meta_kubernetes_pod_label_(.+)
```

Either be more specific in `labelmap` regex OR drop unwanted labels with `labeldrop` after.

## Scrape config optimization

### Sample limits per scrape
```yaml
- job_name: kubernetes-pods
  sample_limit: 50000

- job_name: kubernetes-service-endpoints
  sample_limit: 100000  # kube-state-metrics needs more
```

### Drop terminal pods
```yaml
relabel_configs:
  - source_labels: [__meta_kubernetes_pod_phase]
    regex: Succeeded|Failed
    action: drop
```

### Reasonable scrape intervals
- cadvisor: 30s (was 10s — overkill, doubles ingestion)
- kube-state-metrics: 30s
- App metrics: 30s default, 15s for critical SLI-driving metrics

## Force merge after cleanup

When series are dropped at the source (no longer emitted):
1. Wait for retention to expire — series automatically purged
2. OR force merge: `POST /internal/force_merge` on each vmstorage

**Critical**: pods restart cancels force merge. Don't rollout vmstorage during merge.

## Storage status pre/post fix

| Metric | Before fix | After fix |
|--------|-----------|-----------|
| Total series | 700M+ | 13M |
| Ingestion rate | varies | ~341K samples/sec |
| Slow inserts | high | reducing |

(Real numbers from a <org> cardinality cleanup — May 2026)

## Cardinality limit (OTel SDK default)

**2000 unique time series per metric**. Plan within this. If you need more dimensions, consider:
- Logs (Loki) for high-cardinality dimensions
- Traces (Tempo) for per-request data
- Pre-aggregation in app code

## When to use streaming aggregation vs labeldrop

- **labeldrop**: drop a label entirely (e.g., always remove `instance`)
- **streaming aggregation**: keep raw data for some uses, aggregate down for dashboards

See related skill: `streaming-aggregation`.
