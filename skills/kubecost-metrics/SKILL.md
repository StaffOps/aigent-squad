---
name: kubecost-metrics
description: "Use when diagnosing Kubecost cost-model accuracy, node pricing correctness, network cost attribution, container allocation drift, PV cost gaps, or internal API health. Covers node_*_hourly_cost, container_cpu_allocation, container_memory_allocation_bytes, kubecost_network_*, kubecost_load_balancer_cost, kubecost_cluster_management_cost, pv_hourly_cost, kubecost_pod_network_*, kubecost_http_*. Grounded on Helm chart kubecost/cost-analyzer v2.8.5."
keywords: [kubecost, cost, node, pv]
---
# Kubecost Cost-Model Metrics

Metrics emitted by the **Kubecost cost-model** (OpenCost-based) and the **network-costs DaemonSet** for Kubernetes cost allocation and FinOps analysis.

**Question answered**: "Are node/pod/PV costs being calculated correctly? Is network cost attribution working? Is Kubecost's internal API healthy?"

**Scope**: Cost-model generated metrics (node pricing, resource allocation, storage, network egress pricing) + network-costs DaemonSet pod-level traffic metrics + cost-model internal HTTP metrics. Does NOT cover cAdvisor, kube-state-metrics, or node-exporter metrics that Kubecost *consumes* — those are documented in `k8s-workload-metrics`.

---

## Deployed Version & Pipeline

- **Chart**: `kubecost/cost-analyzer` **v2.8.5**
- **Namespace**: `kubecost`
- **Components**:
  - `cost-analyzer` (cost-model + frontend) — exposes `:9003/metrics`
  - `kubecost-network-costs` DaemonSet — exposes `:3001/metrics`
  - Bundled Prometheus server (retention 1096d, gp3 100Gi) — for internal storage
- **Scrape pipeline**: cost-model `:9003` + network-costs `:3001` → bundled Prometheus → Kubecost UI. Additionally, **ServiceMonitor** (`serviceMonitor.enabled: true`) + **PodMonitor** (`networkCosts.podMonitor.enabled: true`) allow external vmagent to scrape these same endpoints into VictoriaMetrics.
- **Metrics ENABLED**: ✅ ServiceMonitor + PodMonitor both enabled in deployed config.

---

## 1. Node Cost Metrics (from cost-model :9003)

These are the core pricing metrics that Kubecost generates from cloud pricing APIs (AWS CUR/Athena in this deployment) and writes back as Prometheus metrics.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `node_total_hourly_cost` | Gauge | Total hourly cost of the node (CPU+RAM+GPU+overhead) | Verify node pricing accuracy; sudden jumps = pricing API issue or instance type change; `sum() * 730` = monthly cluster cost | `node`, `instance`, `provider_id` |
| `node_cpu_hourly_cost` | Gauge | Hourly cost per vCPU on this node | CPU cost attribution accuracy; compare across instance families to validate spot/on-demand pricing | `node`, `instance`, `provider_id` |
| `node_ram_hourly_cost` | Gauge | Hourly cost per GiB of memory on this node | Memory cost attribution; should reflect instance family pricing split | `node`, `instance`, `provider_id` |
| `node_gpu_hourly_cost` | Gauge | Hourly cost per GPU on this node | GPU workload costing (0 for non-GPU nodes) | `node`, `instance`, `provider_id` |
| `node_gpu_count` | Gauge | Number of GPUs available on node | GPU inventory; mismatch with DCGM = detection issue | `node`, `instance`, `provider_id` |
| `kubecost_node_is_spot` | Gauge | Whether node is spot/preemptible (1=spot, 0=on-demand) | Spot detection accuracy; must match `karpenter.sh/capacity-type` label (configured as `spotLabel` in this deployment) | `node`, `instance`, `provider_id` |

---

## 2. Resource Allocation Metrics (from cost-model :9003)

Container-level allocation metrics — the bridge between raw usage and cost attribution.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `container_cpu_allocation` | Gauge | Average CPU cores requested/used over last 1m per container | Per-namespace CPU cost = `sum by (namespace)(container_cpu_allocation) * on(node) node_cpu_hourly_cost` | `container`, `namespace`, `pod`, `node` |
| `container_memory_allocation_bytes` | Gauge | Average memory bytes requested/used over last 1m per container | Per-namespace memory cost = `sum by (namespace)(container_memory_allocation_bytes / 1024^3) * on(node) node_ram_hourly_cost` | `container`, `namespace`, `pod`, `node` |
| `container_gpu_allocation` | Gauge | Average GPU count requested over last 1m per container | GPU cost per workload | `container`, `namespace`, `pod`, `node` |
| `pod_pvc_allocation` | Gauge | Bytes provisioned for PVC attached to a pod | Storage cost per pod = `pod_pvc_allocation * on(persistentvolume) pv_hourly_cost / 1024^3` | `persistentvolume`, `namespace`, `pod` |

---

## 3. Storage & Infrastructure Cost Metrics (from cost-model :9003)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `pv_hourly_cost` | Gauge | Hourly cost per GiB for a persistent volume | Storage pricing accuracy; validate against gp3/gp2/io1 rates; `0` = pricing not detected | `persistentvolume` |
| `kubecost_load_balancer_cost` | Gauge | Hourly cost of a load balancer | LB cost attribution by namespace/service; verify ALB/NLB pricing | `namespace`, `service` |
| `kubecost_cluster_management_cost` | Gauge | Hourly EKS/GKE/AKS management fee | Should be ~$0.10/hour for EKS; `0` = cloud integration misconfigured | `cluster` |

---

## 4. Network Egress Pricing Metrics (from cost-model :9003)

These define the per-GiB cost rates used for network cost attribution (not actual traffic — see section 5 for traffic).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kubecost_network_zone_egress_cost` | Gauge | Cost per GiB of cross-zone egress (typically $0.01/GiB on AWS) | Validate zone egress pricing; `0` = custom pricing not configured | `namespace`, `service` |
| `kubecost_network_region_egress_cost` | Gauge | Cost per GiB of cross-region egress (typically $0.02/GiB on AWS) | Validate region egress pricing | `namespace`, `service` |
| `kubecost_network_internet_egress_cost` | Gauge | Cost per GiB of internet egress (typically $0.09/GiB on AWS) | Validate internet egress pricing; most expensive tier | `namespace`, `service` |

---

## 5. Network Traffic Metrics (from network-costs DaemonSet :3001)

The `kubecost-network-costs` DaemonSet (enabled in this deployment) tracks actual pod-level network traffic using conntrack.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kubecost_pod_network_egress_bytes_total` | Counter | Total egressed bytes per pod, classified by destination | Identify top talkers; correlate spikes with cost jumps; `rate()` for bandwidth | `pod_name`, `namespace` |
| `kubecost_pod_network_ingress_bytes_total` | Counter | Total ingressed bytes per pod | Inbound traffic attribution; detect unexpected traffic sources | `pod_name`, `namespace` |
| `kubecost_network_costs_parsed_entries` | Gauge | Total parsed conntrack entries | DaemonSet health: dropping entries = conntrack table overflow | — |
| `kubecost_network_costs_parse_time` | Gauge | Time in ms to parse conntrack entries | Performance: >500ms = conntrack table too large, may miss flows | — |

---

## 6. Internal Operation Metrics (from cost-model :9003)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kubecost_http_requests_total` | Counter | Total HTTP requests to cost-model API | Request rate by endpoint; error spike = API health issue | `endpoint`, `method`, `status` |
| `kubecost_http_response_time_seconds` | Histogram | Response time for cost-model API endpoints | Latency by endpoint; slow `/allocation` = aggregator pressure | `endpoint`, `method` |
| `kubecost_http_response_size_bytes` | Histogram | Response size for cost-model API endpoints | Large responses = heavy allocation queries, potential timeouts | `endpoint`, `method` |
| `kubecost_cluster_info` | Gauge (info) | Cluster metadata | Verify cloud integration detection | `cluster`, `provider` |

---

## 7. Cluster Recording Rule Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kubecost_cluster_memory_working_set_bytes` | Gauge | Cluster-wide memory working set (recording rule) | Cluster capacity validation | `cluster` |

---

## 8. Label Metadata Metrics (from cost-model :9003)

Used for workload→owner mapping (join/group queries).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `service_selector_labels` | Info | Service selector label mappings | Join service cost with workload labels | `namespace`, `service`, `label_*` |
| `deployment_match_labels` | Info | Deployment match label mappings | Join deployment cost with owner labels | `namespace`, `deployment`, `label_*` |
| `statefulSet_match_labels` | Info | StatefulSet match label mappings | Join statefulset cost with owner labels | `namespace`, `statefulset`, `label_*` |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Likely Cause |
|---|---|---|
| Node costs showing $0 | `node_total_hourly_cost == 0` | Cloud integration broken (Athena/CUR), IRSA permission issue, or custom pricing misconfigured |
| Spot nodes priced as on-demand | `kubecost_node_is_spot == 0` on known spot nodes | `spotLabel` / `spotLabelValue` mismatch (should be `karpenter.sh/capacity-type` = `spot`) |
| PV costs missing | `pv_hourly_cost == 0` | StorageClass pricing not configured or PV not bound |
| Network costs showing $0 | `kubecost_pod_network_egress_bytes_total` absent | network-costs DaemonSet not running or PodMonitor missing |
| Allocation drift (UI ≠ Prometheus) | Compare `container_cpu_allocation` with `kube_pod_container_resource_requests` | Kubecost uses max(request, usage) — will differ from raw requests |
| Cost-model API slow | `kubecost_http_response_time_seconds{endpoint="/allocation"}` p99 > 30s | Aggregator overloaded (check kubecostAggregator resources) |
| conntrack overflow | `kubecost_network_costs_parsed_entries` dropping, `parse_time` spiking | Node has too many connections; increase conntrack table or filter |
| CUR/Athena pricing stale | `node_cpu_hourly_cost` unchanged for days despite instance changes | Check `athenaTable`/`athenaDatabase` config, IAM role `masterPayerARN` |

### Key PromQL Queries

```promql
# Monthly cluster compute cost
sum(node_total_hourly_cost) * 730

# Top 10 namespaces by CPU cost (monthly)
topk(10,
  sum by (namespace)(
    container_cpu_allocation * on(node) group_left() node_cpu_hourly_cost
  ) * 730
)

# Spot vs on-demand node cost split
sum(node_total_hourly_cost * kubecost_node_is_spot) * 730          # Spot monthly
sum(node_total_hourly_cost * (1 - kubecost_node_is_spot)) * 730    # On-demand monthly

# Top egress pods (bytes/sec)
topk(10, rate(kubecost_pod_network_egress_bytes_total[5m]))

# Network-costs DaemonSet health
kubecost_network_costs_parse_time > 500  # Parsing taking too long
```

---

## Deployment-Specific Configuration

Key settings affecting metric accuracy in this deployment:

| Setting | Value | Impact |
|---|---|---|
| `kubecostMetrics.emitPodAnnotations` | `true` | Pod annotations available as labels |
| `kubecostMetrics.emitNamespaceAnnotations` | `true` | Namespace annotations available as labels |
| `spotLabel` | `karpenter.sh/capacity-type` | How Kubecost identifies spot nodes |
| `spotLabelValue` | `spot` | Value that indicates spot instance |
| `labelMappingConfigs.owner_label` | `CostCenter` | Maps to org CostCenter tag |
| `labelMappingConfigs.team_label` | `team` | Team attribution |
| `labelMappingConfigs.environment_label` | `Environment` | Environment classification |
| `athenaDatabase` / `athenaTable` | `kubecost` / `kubecost_split` | AWS CUR source for actual pricing |
| `federatedStorageConfig` | S3 `devops-kubecost-report` | Multi-cluster cost federation |
| `prometheusRule.enabled` | `true` | Recording rules registered |

---

## Complements

- **k8s-workload-metrics** — cAdvisor and kube-state-metrics that Kubecost *consumes* (container_cpu_usage_seconds_total, kube_pod_container_resource_requests, etc.)
- **go-apm-metrics** — Go runtime metrics from the cost-model process itself (go_goroutines, go_memstats_*, go_gc_*)
- **karpenter-metrics** — Node provisioning metrics; correlate with `kubecost_node_is_spot` for spot detection validation
- **aws-csi-driver-metrics** — EBS volume metrics; correlate with `pv_hourly_cost` for storage cost accuracy

---

## Sources

- [Kubecost Official Metrics Documentation](https://docs.kubecost.com/architecture/user-metrics) — cost-model + network-costs + cAdvisor + KSM metrics
- [OpenCost Metrics Reference Guide](https://opencost.io/docs/integrations/metrics) — comprehensive generated metrics list with labels
- Deployed chart: `kubecost/cost-analyzer` v2.8.5 at `/home/karlipegomes/Documents/<ORG>/02-KUBE/00-CONFIG/k8s-setup/kubecost/cost-analyzer/values.yaml.gotmpl`
- Kubecost GitHub: [cost-analyzer-helm-chart](https://github.com/kubecost/cost-analyzer-helm-chart)
