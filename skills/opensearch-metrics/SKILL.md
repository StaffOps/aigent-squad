---
name: opensearch-metrics
description: "Use when diagnosing OpenSearch cluster health \u2014 cluster status (RED/YELLOW), JVM heap pressure, disk watermarks, bulk rejection, threadpool saturation, indexing/search latency, circuit breaker trips, and transport layer health. Covers opensearch_cluster_*, opensearch_jvm_*, opensearch_indices_*, opensearch_fs_*, opensearch_os_*, opensearch_process_*, opensearch_transport_*, opensearch_threadpool_*, opensearch_circuitbreaker_*. Grounded on Helm chart opensearch/opensearch 2.27.0 (appVersion ~2.17.x) with Aiven/opensearch-project prometheus-exporter-plugin 2.17.1.0 exposed at /_prometheus/metric"
keywords: [opensearch, threadpool, jvm]
---
# OpenSearch Prometheus Metrics

Metrics exposed by the **Aiven/opensearch-project prometheus-exporter-plugin** for OpenSearch,
scraped at `/_prometheus/metrics` on port 9200.

---

## Deployment Status & Scrape Pipeline

### Plugin & Chart Version

| Component | Version | Source |
|-----------|---------|--------|
| Helm chart | `opensearch/opensearch` 2.27.0 | `k8s-setup/opensearch/helmfile.yaml.gotmpl` |
| OpenSearch (appVersion) | ~2.17.x | Chart version convention |
| Prometheus exporter plugin | `2.17.1.0` | `helmfile.yaml.gotmpl` variable `prometheus_exporter_plugin` |
| Repository | [opensearch-project/opensearch-prometheus-exporter](https://github.com/opensearch-project/opensearch-prometheus-exporter) (formerly Aiven-Open) |

### ⚠️ Plugin Install Status (CRITICAL)

In the deployed Helm values, the **plugin install is COMMENTED OUT** and `plugins.enabled: false`:

```yaml
# opensearch-master/values.yaml.gotmpl & opensearch-data/values.yaml.gotmpl
plugins:
  enabled: false
  # installList:
  #   - https://github.com/aiven/prometheus-exporter-plugin-for-opensearch/releases/download/{{ .Values.prometheus_exporter_plugin }}/prometheus-exporter-{{ .Values.prometheus_exporter_plugin }}.zip
serviceMonitor:
  enabled: false
```

**However**, the plugin IS installed on the **EC2-hosted** OpenSearch instances (not the K8s StatefulSets).
The vmagent scrape config targets EC2 instances tagged `MonitoringExporter: opensearch` via EC2 service discovery:

```
Pipeline: OpenSearch EC2 instances (plugin at /_prometheus/metrics:9200)
         → vmagent ec2_sd_configs scrape (job: ec2-opensearch-metrics)
         → VictoriaMetrics
```

### K8s Cluster Deployment

The K8s-deployed OpenSearch (via Helm chart in `k8s-setup/opensearch/`) does **NOT** currently expose
Prometheus metrics (plugin disabled, ServiceMonitor disabled). The PrometheusRule alerting rules are
deployed and presumably fire against metrics from the EC2-hosted cluster.

> **To enable metrics on K8s deployment**: uncomment the `installList` under `plugins`, set
> `plugins.enabled: true`, and create a VMServiceScrape targeting port 9200 path `/_prometheus/metrics`.

---

## Metric Names & Labels

All metrics use the **`opensearch_` prefix** (configurable via `prometheus.metric_name.prefix` in
opensearch.yml, default is `opensearch_`). Common labels on all node-level metrics:

| Label | Values | Description |
|-------|--------|-------------|
| `cluster` | `opensearch-cluster` | Cluster name |
| `node` | `opensearch-cluster-master-1`, etc. | Node name |
| `instance` | `<ip>:9200` | Scrape target |

---

## 1. Cluster Health

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_cluster_status` | Gauge | Cluster health: 0=GREEN, 1=YELLOW, 2=RED | **Primary health signal.** RED=no writes, YELLOW=replicas unassigned | `cluster` |
| `opensearch_cluster_nodes_number` | Gauge | Total nodes in cluster | Node departure detection | `cluster` |
| `opensearch_cluster_datanodes_number` | Gauge | Data nodes in cluster | Data node loss | `cluster` |
| `opensearch_cluster_shards_active_percent` | Gauge | Percentage of active shards | Below 100% = unassigned shards | `cluster` |
| `opensearch_cluster_shards_number` | Gauge | Shard counts by type | Shard imbalance, unassigned growth | `cluster`, `type` (active, relocating, initializing, unassigned, active_primary) |
| `opensearch_cluster_pending_tasks_number` | Gauge | Pending cluster state updates | Cluster master overloaded | `cluster` |
| `opensearch_cluster_inflight_fetch_number` | Gauge | In-flight fetches | Shard recovery pressure | `cluster` |

### Alert deployed (from `prometheus.rules.yaml`):
```promql
# RED for 2m → critical
sum by (cluster) (opensearch_cluster_status == 2)

# YELLOW for 20m → warning
sum by (cluster) (opensearch_cluster_status == 1)
```

---

## 2. JVM & Memory

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_jvm_mem_heap_used_bytes` | Gauge | JVM heap used (bytes) | Memory pressure; near limit = GC storms | `cluster`, `node` |
| `opensearch_jvm_mem_heap_max_bytes` | Gauge | JVM max heap configured | Calculate usage percent | `cluster`, `node` |
| `opensearch_jvm_mem_heap_used_percent` | Gauge | JVM heap used as percentage (0–100) | **Direct alert input** — >75% = pressure | `cluster`, `node` |
| `opensearch_jvm_mem_nonheap_used_bytes` | Gauge | Non-heap memory (metaspace, code cache) | Off-heap leak detection | `cluster`, `node` |
| `opensearch_jvm_mem_pool_used_bytes` | Gauge | Memory pool usage | Identify which pool is exhausted | `cluster`, `node`, `pool` (young, old, survivor) |
| `opensearch_jvm_mem_pool_max_bytes` | Gauge | Memory pool max | Pool saturation | `cluster`, `node`, `pool` |
| `opensearch_jvm_gc_collection_count` | Counter | GC invocation count | GC frequency spike = allocation pressure | `cluster`, `node`, `gc` (young, old) |
| `opensearch_jvm_gc_collection_time_seconds` | Counter | Time spent in GC (seconds) | Long GC pauses = stop-the-world impact | `cluster`, `node`, `gc` (young, old) |
| `opensearch_jvm_uptime_seconds` | Gauge | JVM uptime | Detect recent restarts | `cluster`, `node` |

### Alert deployed:
```promql
# JVM heap > 75% for 10m → alert
sum by (cluster, instance, node) (opensearch_jvm_mem_heap_used_percent) > 75
```

---

## 3. Filesystem & Disk

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_fs_path_total_bytes` | Gauge | Total filesystem size | Capacity planning | `cluster`, `node`, `path` |
| `opensearch_fs_path_available_bytes` | Gauge | Available filesystem bytes | **Disk watermark alerts** (85%/90% thresholds) | `cluster`, `node`, `path` |
| `opensearch_fs_path_free_bytes` | Gauge | Free bytes (includes reserved) | Similar to available but includes OS reserved | `cluster`, `node`, `path` |

### Alert deployed:
```promql
# Low watermark (85%) → alert severity
(1 - (opensearch_fs_path_available_bytes / opensearch_fs_path_total_bytes)) * 100 > 85

# High watermark (90%) → high severity
(1 - (opensearch_fs_path_available_bytes / opensearch_fs_path_total_bytes)) * 100 > 90
```

---

## 4. OS & Process CPU

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_os_cpu_percent` | Gauge | System-wide CPU percent (0–100) | Host saturation | `cluster`, `node` |
| `opensearch_process_cpu_percent` | Gauge | OpenSearch process CPU percent | Process-level saturation | `cluster`, `node` |
| `opensearch_os_mem_total_bytes` | Gauge | Total OS memory | Capacity context | `cluster`, `node` |
| `opensearch_os_mem_free_bytes` | Gauge | Free OS memory | OOM risk (page cache eviction) | `cluster`, `node` |
| `opensearch_os_mem_used_percent` | Gauge | OS memory usage percent | System memory pressure | `cluster`, `node` |
| `opensearch_process_mem_total_virtual_bytes` | Gauge | Virtual memory used by process | Unusual growth = memory mapping issues | `cluster`, `node` |
| `opensearch_os_load_average` | Gauge | System load averages | Sustained saturation signal | `cluster`, `node`, `duration` (1m, 5m, 15m) |

### Alerts deployed:
```promql
# OS CPU > 90% for 1m → alert
sum by (cluster, instance, node) (opensearch_os_cpu_percent) > 90

# Process CPU > 90% for 1m → alert
sum by (cluster, instance, node) (opensearch_process_cpu_percent) > 90
```

---

## 5. Indices — Indexing & Search

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_indices_indexing_index_count` | Counter | Total documents indexed | Indexing throughput baseline | `cluster`, `node` |
| `opensearch_indices_indexing_index_time_seconds` | Counter | Time spent indexing | Indexing latency (rate → avg per doc) | `cluster`, `node` |
| `opensearch_indices_indexing_delete_count` | Counter | Total documents deleted | Unusual delete activity | `cluster`, `node` |
| `opensearch_indices_indexing_is_throttled_bool` | Gauge | Is indexing throttled? (0/1) | Merge pressure causing backpressure | `cluster`, `node` |
| `opensearch_indices_search_query_count` | Counter | Total search queries | Search throughput baseline | `cluster`, `node` |
| `opensearch_indices_search_query_time_seconds` | Counter | Time spent in search queries | Search latency degradation | `cluster`, `node` |
| `opensearch_indices_search_fetch_count` | Counter | Total search fetches | Fetch-heavy queries | `cluster`, `node` |
| `opensearch_indices_search_fetch_time_seconds` | Counter | Time spent in fetch phase | Fetch latency (large result sets) | `cluster`, `node` |
| `opensearch_indices_search_open_contexts_number` | Gauge | Open search contexts (scroll/PIT) | Leak of scroll contexts | `cluster`, `node` |
| `opensearch_indices_docs_count` | Gauge | Total documents in node | Growth tracking, capacity | `cluster`, `node` |
| `opensearch_indices_docs_deleted_count` | Gauge | Deleted (not yet merged) docs | Merge pressure indicator | `cluster`, `node` |
| `opensearch_indices_store_size_bytes` | Gauge | Store size on node | Disk usage by data | `cluster`, `node` |
| `opensearch_indices_refresh_count` | Counter | Total refresh operations | Refresh overhead | `cluster`, `node` |
| `opensearch_indices_refresh_time_seconds` | Counter | Time spent refreshing | Refresh taking too long | `cluster`, `node` |
| `opensearch_indices_flush_count` | Counter | Total flush (translog commit) operations | Flush frequency | `cluster`, `node` |
| `opensearch_indices_flush_time_seconds` | Counter | Time spent flushing | Slow flush = disk I/O issue | `cluster`, `node` |
| `opensearch_indices_merge_count` | Counter | Total merges | Merge pressure | `cluster`, `node` |
| `opensearch_indices_merge_time_seconds` | Counter | Time spent merging | Merge bottleneck | `cluster`, `node` |
| `opensearch_indices_merge_current_number` | Gauge | Currently running merges | Active merge saturation | `cluster`, `node` |

---

## 6. Threadpool (Bulk Rejection)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_threadpool_threads_count` | Gauge | Current thread count in pool | Pool sizing | `cluster`, `node`, `name`, `type` (active, queue, rejected, completed) |
| `opensearch_threadpool_tasks_number` | Gauge | Active + queued tasks | Pool saturation | `cluster`, `node`, `name` |
| `opensearch_threadpool_rejected_count` | Counter | **Total rejected tasks** | **Bulk/write rejection = data loss risk** | `cluster`, `node`, `name` (write, search, get, bulk, etc.) |
| `opensearch_threadpool_completed_count` | Counter | Total completed tasks | Throughput baseline | `cluster`, `node`, `name` |

### Recording rule deployed (from alerts):
```promql
# Used in alert: bulk:reject_ratio:rate2m > 5%
# This is a recording rule calculating rejection ratio
```

---

## 7. Transport Layer

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_transport_rx_packets_count` | Counter | Received transport packets | Inter-node communication volume | `cluster`, `node` |
| `opensearch_transport_tx_packets_count` | Counter | Sent transport packets | Inter-node communication volume | `cluster`, `node` |
| `opensearch_transport_rx_bytes_count` | Counter | Received bytes (transport) | Network throughput | `cluster`, `node` |
| `opensearch_transport_tx_bytes_count` | Counter | Sent bytes (transport) | Network throughput / rebalance traffic | `cluster`, `node` |
| `opensearch_transport_server_open_number` | Gauge | Open transport connections | Connection pool health | `cluster`, `node` |

---

## 8. HTTP Layer

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_http_open_number` | Gauge | Current open HTTP connections | Client connection saturation | `cluster`, `node` |
| `opensearch_http_total_opened_count` | Counter | Total HTTP connections opened | Connection churn | `cluster`, `node` |

---

## 9. Circuit Breakers

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|-------------|------|------------------|---------------------|--------|
| `opensearch_circuitbreaker_estimated_bytes` | Gauge | Estimated memory usage for circuit | Near-trip detection | `cluster`, `node`, `name` (request, fielddata, in_flight_requests, parent) |
| `opensearch_circuitbreaker_limit_bytes` | Gauge | Circuit breaker limit | Headroom calculation | `cluster`, `node`, `name` |
| `opensearch_circuitbreaker_tripped_count` | Counter | **Times the breaker tripped** | Tripped = requests rejected to protect stability | `cluster`, `node`, `name` |
| `opensearch_circuitbreaker_overhead` | Gauge | Multiplier applied to estimates | Understand conservative estimates | `cluster`, `node`, `name` |

---

## 10. Index-Level Metrics (when `prometheus.indices: true`)

When enabled, per-index metrics are exposed with an additional `index` label:

| Metric Name | Type | What It Measures | Labels |
|-------------|------|------------------|--------|
| `opensearch_index_status` | Gauge | Index health (0=GREEN, 1=YELLOW, 2=RED) | `cluster`, `index` |
| `opensearch_index_shards_number` | Gauge | Shard count per type | `cluster`, `index`, `type` |
| `opensearch_index_doc_number` | Gauge | Document count in index | `cluster`, `index` |
| `opensearch_index_doc_deleted_number` | Gauge | Deleted docs in index | `cluster`, `index` |
| `opensearch_index_store_size_bytes` | Gauge | Index store size | `cluster`, `index` |
| `opensearch_index_indexing_*` | Counter | Per-index indexing stats | `cluster`, `index` |
| `opensearch_index_search_*` | Counter | Per-index search stats | `cluster`, `index` |

> ⚠️ **Cardinality warning**: per-index metrics multiply label cardinality by index count.
> Disable with `prometheus.indices: false` if you have hundreds of indices.

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Interpretation |
|---------|------------------------|----------------|
| Cluster RED | `opensearch_cluster_status == 2` | Master election failed or primary shards unassigned |
| Cluster YELLOW (prolonged) | `opensearch_cluster_status == 1` + `opensearch_cluster_shards_number{type="unassigned"}` | Replicas unassigned — disk watermark? node missing? |
| Slow indexing | `rate(opensearch_indices_indexing_index_time_seconds)` / `rate(opensearch_indices_indexing_index_count)` | Avg time per doc; also check `opensearch_indices_indexing_is_throttled_bool` |
| Bulk rejections | `rate(opensearch_threadpool_rejected_count{name="write"})` | Write queue full — scale data nodes or reduce bulk size |
| High GC pauses | `rate(opensearch_jvm_gc_collection_time_seconds{gc="old"})` | Old-gen GC time increasing = heap pressure, possible large aggregations |
| Disk watermark hit | `(1 - opensearch_fs_path_available_bytes/opensearch_fs_path_total_bytes) > 0.85` | Shards won't allocate to node; add disk or delete old indices |
| Circuit breaker trips | `rate(opensearch_circuitbreaker_tripped_count{name="parent"})` | Request too large for available memory; reduce query complexity |
| Node loss | `opensearch_cluster_nodes_number` drops | Node crashed or network partition; check transport metrics |
| Search latency spike | `rate(opensearch_indices_search_query_time_seconds)` / `rate(opensearch_indices_search_query_count)` | Avg query time; correlate with GC, merge activity, CPU |
| Scroll context leak | `opensearch_indices_search_open_contexts_number` growing monotonically | Clients not closing scroll/PIT sessions |

---

## Complements

- **k8s-workload-metrics** — container-level CPU/memory for OpenSearch pods (K8s deployment)
- **backing-services-metrics** — if OpenSearch is used as a backing service for an application
- **go-apm-metrics** — NOT applicable (OpenSearch is Java/JVM)
- **collector-internal-metrics** — if OTel Collector receives logs from OpenSearch

---

## Sources

- [opensearch-project/opensearch-prometheus-exporter](https://github.com/opensearch-project/opensearch-prometheus-exporter) — plugin source (formerly Aiven-Open)
- Plugin version: `2.17.1.0` (from `helmfile.yaml.gotmpl` variable)
- Helm chart: `opensearch/opensearch` v2.27.0
- Deployed alert rules: `k8s-setup/opensearch/opensearch-raw/prometheus.rules.yaml`
- Scrape config: `k8s-setup/monitoring/vm-operator-raw/vmagent-scrape-external/scrape-configs/ec2-instances.yaml` (job `ec2-opensearch-metrics`, path `/_prometheus/metrics`, port 9200)
- Default metric prefix: `opensearch_` (plugin config `prometheus.metric_name.prefix`)
