---
name: kubelet-scrape-architecture
description: Unified kubelet scrape configuration for VictoriaMetrics. Use when integrating kubernetes-mixin dashboards, fixing missing cadvisor metrics, dealing with Karpenter ephemeral nodes, or replacing broken VMServiceScrape kubelet. Covers `role: node` discovery, metrics_path label, cadvisor job rewrite.
keywords: [kubelet-scrape-architecture, kubelet, scrape, architecture, "kubelet scrape", "scrape architecture", karpenter, node, victoriametrics]
---
# Kubelet Scrape Architecture

Unified scrape configuration for kubelet `/metrics` and `/metrics/cadvisor` that aligns with kubernetes-mixin expectations and works reliably with Karpenter.

## The problem

kube-prometheus-stack provides `VMServiceScrape prometheus-kubelet` that:
1. Depends on a headless Service in `kube-system` whose endpoints are managed by prometheus-operator
2. With Karpenter creating/destroying nodes, the operator does NOT sync new nodes
3. Result: 33 nodes in cluster but only 38 endpoints in the Service (stale, missing 30 nodes)

**Symptom**: dashboards show only some nodes; new pods on Karpenter nodes have no metrics.

## The solution: manual scrape configs with `role: node`

Replace `VMServiceScrape` with manual scrape configs that use Kubernetes service discovery:

### File location
`vm-operator/vmagent-scrape-cluster/scrape-configs/default_vm.yaml`

### Two unified jobs

```yaml
# Kubelet own metrics (volume stats, runtime ops, etc)
- job_name: "kubelet"
  scrape_interval: 30s
  kubernetes_sd_configs:
    - role: node
  scheme: https
  tls_config:
    insecure_skip_verify: true
  bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
  metrics_path: /metrics
  relabel_configs:
    - action: replace
      source_labels: [__metrics_path__]
      target_label: metrics_path
    - action: labelmap
      regex: __meta_kubernetes_node_label_(.+)

# Container metrics (cadvisor)
- job_name: "kubelet-cadvisor"
  scrape_interval: 30s
  kubernetes_sd_configs:
    - role: node
  scheme: https
  tls_config:
    insecure_skip_verify: true
  bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
  metrics_path: /metrics/cadvisor
  relabel_configs:
    - action: replace
      source_labels: [__metrics_path__]
      target_label: metrics_path
    - action: labelmap
      regex: __meta_kubernetes_node_label_(.+)
  metric_relabel_configs:
    - target_label: job
      replacement: kubelet  # Rewrite to match upstream expectations
```

## Why these design decisions

### 1. `role: node` — auto-discovery
Discovers ALL nodes automatically via Kubernetes API. No dependency on prometheus-operator endpoint management. Karpenter ephemeral nodes appear immediately.

### 2. `metrics_path` as a label
Added via `relabel_configs: source_labels: [__metrics_path__]`. Allows dashboards to differentiate `/metrics` vs `/metrics/cadvisor` queries.

### 3. Job rewrite on cadvisor
`metric_relabel_configs: target_label: job, replacement: kubelet` — both endpoints appear as a single `job="kubelet"`. This matches what kubernetes-mixin recording rules expect.

### 4. Streaming aggregation preserves these labels
```yaml
streamAggrConfig:
  rules:
    - match: 'kubelet_runtime_operations_duration_seconds_bucket'
      by: [job, metrics_path, cluster, eks_cluster, operation_type, le]
```

`metrics_path` lets you distinguish kubelet vs cadvisor metrics in aggregations.

## Other scrape config standards

```yaml
# apiserver — renamed from kubernetes-apiservers to match upstream
- job_name: "apiserver"
  kubernetes_sd_configs:
    - role: endpoints
  relabel_configs:
    - source_labels: [__meta_kubernetes_namespace, __meta_kubernetes_service_name, __meta_kubernetes_endpoint_port_name]
      action: keep
      regex: default;kubernetes;https
```

Recording rules filter by `job="apiserver"`.

## Action items after migration

- [ ] Disable `VMServiceScrape prometheus-kubelet` (broken, redundant)
- [ ] Remove stale Services `prometheus-kubelet` and `kube-prometheus-stack-kubelet` in `kube-system`
- [ ] Evaluate removing prometheus-operator entirely (vm-operator handles VMServiceScrape, VMRule, VMAgent)

## Validation queries

### All nodes scraped?
```promql
count(up{job="kubelet"}) by (cluster)
# Compare with: kubectl get nodes -o name | wc -l
```

### Cadvisor metrics present?
```promql
count(container_cpu_usage_seconds_total{job="kubelet"}) by (cluster) > 0
```

### Karpenter node included?
```promql
up{job="kubelet", instance=~".*karpenter.*"} == 1
```

## Common issues

### Issue: missing nodes in dashboards
- Check: `count(up{job="kubelet"}) vs kubectl get nodes`
- Cause: VMServiceScrape with broken endpoint discovery
- Fix: switch to manual scrape with `role: node`

### Issue: cadvisor metrics duplicated
- Cause: both `VMServiceScrape kubelet` (10s interval) AND manual scrape config (30s) running
- Fix: pick one — manual scrape preferred

### Issue: dashboard query has `job=~"kubelet|kubelet-cadvisor"`
- Cause: someone updated for new scrape but didn't rewrite job on cadvisor
- Fix: keep `job="kubelet"` everywhere via `metric_relabel_configs` rewrite

### Issue: streaming aggregation produces empty series
- Cause: `instance` in `by:` but dropped via `labeldrop`
- Fix: see `streaming-aggregation` skill

## Final scrape config inventory

```
job=apiserver           — kubernetes API
job=kubelet (path=/metrics)     — kubelet own metrics + volume stats
job=kubelet (path=/metrics/cadvisor) — container metrics
job=kubernetes-service-endpoints — annotated services
job=kubernetes-pods    — annotated pods
job=vmagent            — self-monitoring
```

## Reference

- kubernetes-mixin: https://github.com/kubernetes-monitoring/kubernetes-mixin
- VictoriaMetrics scrape configs: https://docs.victoriametrics.com/sd_configs/
- Local cache: `<workspace>/01-DEVOPS/EXTERNAL-DOCS/VictoriaMetrics/docs`
- Related: `multicluster-label-strategy`, `streaming-aggregation`
