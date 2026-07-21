---
name: streaming-aggregation
description: VictoriaMetrics streaming aggregation configuration. Use when reducing cardinality at scrape time without losing critical labels, or aggregating high-volume histograms (kubelet operations, storage operations). Covers `streamAggrConfig` syntax and gotchas.
keywords: [streaming-aggregation, streaming, aggregation, "streaming aggregation", victoriametrics]
---
# Streaming Aggregation (vmagent)

Pre-aggregate metrics at scrape time before sending to vmstorage. Reduces cardinality and ingestion volume without losing aggregated insights.

## When to use

- High-volume histograms with redundant labels
- Per-instance metrics that don't need raw resolution at storage
- Pre-computing rollups for dashboards (alternative to recording rules)

## When NOT to use

- Container CPU/memory raw metrics — keep raw, recording rules aggregate per-namespace for dashboards
- Low-cardinality metrics — no benefit
- Metrics where you need per-instance debugging

## Configuration in VMAgent CRD

File: `vm-operator/vmagent-scrape-cluster/vmagent-scrape-cluster-resource.yaml`

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMAgent
spec:
  remoteWrite:
    - url: http://vminsert.monitoring:8480/insert/0/prometheus
      streamAggrConfig:
        rules:
          - match:
              - 'kubelet_runtime_operations_duration_seconds_bucket'
              - 'storage_operation_duration_seconds_bucket'
            interval: 1m
            outputs: [total]
            by: [job, metrics_path, cluster, eks_cluster, operation_type, le]
            keep_metric_names: true
```

## Critical gotcha: dropped labels

If you drop labels via `metric_relabel_configs` (`labeldrop: instance`) and then include `instance` in `by:`, the aggregation produces empty series.

**Wrong:**
```yaml
metric_relabel_configs:
  - action: labeldrop
    regex: instance

streamAggrConfig:
  rules:
    - match: '...'
      by: [instance, operation_type, le]  # ❌ instance was dropped!
```

**Right:**
```yaml
streamAggrConfig:
  rules:
    - match: '...'
      by: [job, metrics_path, cluster, eks_cluster, operation_type, le]  # ✅
```

## Outputs

Common output types:
| Output | Equivalent PromQL | Use case |
|--------|-------------------|----------|
| `total` | `sum_increase()` | Counter aggregation |
| `count_samples` | `count()` | Sample counting |
| `sum_samples` | `sum()` | Sum of values |
| `avg` | `avg()` | Average |
| `min`, `max` | `min()`, `max()` | Extrema |
| `quantiles(0.5, 0.9, 0.99)` | quantile estimate | Percentiles |
| `histogram_bucket` | bucket aggregation | Histograms |

## Real example: kubelet runtime operations

Original metric:
```
kubelet_runtime_operations_duration_seconds_bucket{
  job="kubelet", instance="node-01", operation_type="pull_image",
  metrics_path="/metrics", cluster="<org>-eks-prd", eks_cluster="core",
  le="0.5"
}
```

After streaming aggregation:
- `instance` dropped (per-node not needed for aggregate dashboards)
- 1-min total per (job, metrics_path, cluster, eks_cluster, operation_type, le)
- Result: 1 series per cluster per operation type per bucket

Cardinality reduction: ~30 nodes × 10 operation types × 10 buckets = 3000 → 100 (after instance drop).

## Trade-offs

### Pros
- Reduces ingestion volume to vmstorage
- Reduces query load on vmselect
- Bandwidth-efficient for remote_write across clusters

### Cons
- Loses per-instance granularity for the aggregated metric
- Recording rules can't replace this if data isn't ingested raw
- Adds complexity to the pipeline

## Coexistence with recording rules

| Approach | When |
|----------|------|
| **Streaming aggregation** | High-volume metrics where raw data isn't needed for any query |
| **Recording rules (vmalert)** | Pre-compute common aggregations FOR dashboards/alerts; keep raw data accessible |
| **Both** | For critical metrics: streaming agg for the storage layer, recording rules for fast dashboard queries |

## CRD support caveats

Some `streamAggrConfig` sub-fields may not be supported by older `VMAgent` operator CRDs. Always validate after applying:
```bash
kubectl logs -n monitoring deploy/vmagent | grep -i "streamAggr"
```

If the operator silently ignores config, fall back to manual flag injection:
```yaml
spec:
  extraArgs:
    streamAggr.config: /etc/vmagent/stream_aggr.yaml
```

## Reference

- Docs: https://docs.victoriametrics.com/stream-aggregation/
- Local cache: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/VictoriaMetrics/docs`
- Related skill: `vm-cardinality-management`
