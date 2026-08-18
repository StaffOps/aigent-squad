---
name: multicluster-label-strategy
description: "Multi-cluster label strategy for VictoriaMetrics. Use when integrating kubernetes-mixin dashboards, cross-cluster trace correlation, or when external labels need both `cluster` (k8s name) and `eks_cluster` (<org> env). Covers vmagent externalLabels, scrape config alignment, recording rule joins."
keywords: [multicluster-label, multicluster, label, "multicluster label", victoriametrics, vmagent]
---
# Multi-Cluster Label Strategy

Standard label scheme for telemetry across multiple Kubernetes clusters at <org>.

## The two essential labels

| Label | Source | Purpose | Example |
|-------|--------|---------|---------|
| `cluster` | k8s cluster name | Required by kubernetes-mixin dashboards/rules | `<org>-eks-prd` |
| `eks_cluster` | <org> environment | Logical environment grouping | `core`, `dev`, `prd` |

Both MUST be present on every metric.

## Why both?

- `cluster`: **kubernetes-mixin** dashboards and recording rules expect this label (industry standard, prometheus-operator default). Without it, joins like `by (cluster, namespace, pod)` produce broken series.
- `eks_cluster`: <org>-specific, identifies environment (production, dev, devops). Used for filtering in dashboards, routing in alertmanager.

## Configuration — vmagent externalLabels

File: `vmagent-scrape-cluster-resource.yaml` (templated per cluster via helmfile).

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMAgent
spec:
  externalLabels:
    eks_cluster: '{{ .Values.environment }}'   # core / dev / prd
    cluster: '{{ .Values.cluster_name }}'      # <org>-eks-prd / <org>-eks-dev / etc
    region: '{{ .Values.region }}'             # us-east-1
```

Mappings:
| Environment | `eks_cluster` | `cluster` |
|-------------|---------------|-----------|
| core-devops | `core` | `<org>-eks-prd` |
| dev | `dev` | `<org>-eks-dev` |
| prd | `prd` | `<org>-eks-prd-nv` |

## OTel Collector — same labels in resource attributes

For traces, metrics, logs flowing through OTel:

```yaml
processors:
  transform/adjust_attributes:
    metric_statements:
      - context: resource
        statements:
          - set(attributes["eks_cluster"], "core")
          - set(attributes["cluster"], "<org>-eks-prd")
          - set(attributes["region"], "us-east-1")
```

When converted to Prometheus format, these become metric labels automatically.

## Scrape config — `cluster` propagation

If you don't use `externalLabels`, you must set `cluster` per scrape config:

```yaml
scrape_configs:
  - job_name: kubelet
    relabel_configs:
      - target_label: cluster
        replacement: <org>-eks-prd
```

Prefer `externalLabels` (single point of config) over per-job relabeling.

## kubernetes-mixin dashboard requirements

The dashboards from kube-prometheus-stack chart expect:
- `job="kubelet"` for kubelet/cadvisor metrics (covered by separate skill)
- `cluster=<name>` on all queries
- `namespace`, `pod`, `container` labels

Without `cluster`, panels like "Compute Resources / Cluster" show no data.

## Recording rule joins

Most kubernetes-mixin recording rules use `by (cluster, ...)`:

```promql
# Example: namespace:container_cpu_usage_seconds:sum_rate
sum by (cluster, namespace) (
  rate(container_cpu_usage_seconds_total{job="kubelet"}[5m])
)
```

If `cluster` is missing on the source metrics, the result has empty `cluster=""` — broken in dashboards.

## Cross-cluster trace correlation (Tempo)

Tempo doesn't strictly need `cluster` label, but having `eks_cluster` as a span attribute (via OTel resource attributes) lets you filter traces by environment in TraceQL:

```
{ resource.eks_cluster = "prd" && resource.service.name = "checkout-api" }
```

## Common issues

### Issue: "no data" in kubernetes-mixin dashboards
Cause: `cluster` label missing.
Fix: add `externalLabels.cluster` to vmagent.

### Issue: recording rules produce empty `cluster=""`
Cause: source metrics don't have `cluster`, but rule uses `by (cluster)`.
Fix: ensure all metric sources (vmagent + OTel Collector) emit `cluster`.

### Issue: same metric appears with both `cluster=""` and `cluster=<org>-eks-prd`
Cause: mixed sources — some have the label, some don't.
Fix: audit ALL ingestion paths (vmagent, OTel, federation, manual writes). Add `cluster` everywhere.

### Issue: kube-prometheus-stack VMServiceScrape doesn't sync new Karpenter nodes
Cause: prometheus-operator endpoint discovery breaks with ephemeral nodes.
Fix: use manual scrape configs with `kubernetes_sd_configs: role: node` instead. See related skill `kubelet-scrape-architecture`.

## Audit query

To find metrics WITHOUT the cluster label:
```promql
count by (__name__)({__name__=~".+", cluster=""})
```

Should return zero (or only externally-pushed metrics from systems you don't control).

## Reference

- Multi-cluster monitoring patterns: VictoriaMetrics docs
- kubernetes-mixin: https://github.com/kubernetes-monitoring/kubernetes-mixin
- Related skills: `kubelet-scrape-architecture`, `vm-cardinality-management`
