---
name: datahub-metrics
description: "Use when diagnosing DataHub metadata platform health \u2014 GMS/MAE/MCE JVM performance, Kafka consumer lag, GraphQL request latency, thread pool saturation, or cache efficiency. Covers JMX exporter metrics (jvm_*, kafka_consumer_*, java_lang_*) on port 4318 and Micrometer metrics (graphql.request.duration, messaging.queue.time, executor.*, cache.*) on port 4319 (newer versions). Grounded on Helm chart datahub/datahub 0.8.21 with global.datahub.monitoring.enablePrometheus: true. IMPORTANT: No ServiceMonitor/PodMonitor is configured in the deployed helmfile \u2014 metrics are EXPOSED but may NOT be activ"
keywords: [datahub, helmfile, kafka, gms, mae, mce, jvm, jmx]
---
# DataHub Metrics — Metadata Platform Observability

**Grounded on**: Helm chart `datahub/datahub` version **0.8.21** (from `helm.datahubproject.io`).
**Deployed config**: `global.datahub.monitoring.enablePrometheus: true` — JMX Prometheus agent enabled.
**Official docs**: https://datahubproject.io/docs/advanced/monitoring

---

## Scrape Pipeline & Status

### How metrics are exposed

```
DataHub JVM (GMS/MAE/MCE/Frontend)
  → JMX Prometheus Java agent (jmx_prometheus_javaagent) on port 4318, path /metrics
  → (newer versions) Micrometer Spring Actuator on port 4319, path /actuator/prometheus
```

### Deployed config status

| Setting | Value | Effect |
|---------|-------|--------|
| `global.datahub.monitoring.enablePrometheus` | `true` | JMX exporter agent attached to JVM, exposing metrics on `:4318/metrics` |
| ServiceMonitor / PodMonitor | **NOT CONFIGURED** | ⚠️ Metrics are exposed but NOT scraped by vmagent |
| Pod annotations (`prometheus.io/scrape`) | **NOT CONFIGURED** | ⚠️ No annotation-based discovery either |

**CRITICAL**: In the deployed configuration (chart 0.8.21), **metrics are exposed on port 4318** by the JMX agent but **no scrape target is configured** in vmagent. To actually collect these metrics, a `VMPodScrape` or `VMServiceScrape` must be added targeting port 4318 with `metrics_path: /metrics` on the DataHub pods in the `datahub` namespace.

### DataHub components exposing metrics

| Component | Pod prefix | Metrics Port | Description |
|-----------|-----------|--------------|-------------|
| GMS (Generalized Metadata Service) | `datahub-datahub-gms-*` | 4318 | Core metadata API + storage |
| MAE Consumer | `datahub-datahub-mae-consumer-*` | 4318 | Metadata Audit Event consumer (Elasticsearch indexing) |
| MCE Consumer | `datahub-datahub-mce-consumer-*` | 4318 | Metadata Change Event consumer (SQL writes) |
| Frontend | `datahub-datahub-frontend-*` | 4318 | React UI + GraphQL API |

---

## Version & Metrics Mode Context

The deployed chart version **0.8.21** corresponds to an **early-era DataHub** (pre-Micrometer transition). At this version:

- **JMX/DropWizard** is the primary metrics system
- Metrics are exposed via `jmx_prometheus_javaagent` (Prometheus client_java format)
- The newer Micrometer metrics (`graphql.request.duration`, `messaging.queue.time`, etc.) documented in official DataHub docs are from **v1.5.0+ / chart 0.9.0+** and are **NOT available** at chart 0.8.21

The metrics tables below are split into:
1. **JMX exporter metrics** — available at chart 0.8.21 (confirmed by JMX agent presence)
2. **Micrometer metrics** — available only if upgraded to chart ≥0.9.0 (documented for future reference)

---

## 1. JMX Exporter Metrics (Available at 0.8.21)

These are standard JMX metrics exposed by `jmx_prometheus_javaagent` for any JVM application.

### JVM Memory

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_memory_bytes_used` | Gauge | Current memory usage by area | OOMKill risk, memory pressure | `area` (heap/nonheap) |
| `jvm_memory_bytes_committed` | Gauge | Memory committed to JVM | Capacity baseline | `area` |
| `jvm_memory_bytes_max` | Gauge | Maximum memory available | Headroom calculation | `area` |
| `jvm_memory_pool_bytes_used` | Gauge | Memory usage per pool | Identify which pool is full (Old Gen, Eden, Metaspace) | `pool` |
| `jvm_memory_pool_bytes_max` | Gauge | Max per pool | Old Gen full → Full GC pressure | `pool` |

### JVM Garbage Collection

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_gc_collection_seconds_count` | Counter | Number of GC events | GC frequency — high rate = memory pressure | `gc` (G1 Young/Old) |
| `jvm_gc_collection_seconds_sum` | Counter | Total time spent in GC | GC overhead — sum/count = avg pause | `gc` |

### JVM Threads

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_threads_current` | Gauge | Current live threads | Thread leak detection (monotonic rise) | — |
| `jvm_threads_daemon` | Gauge | Daemon thread count | Background work level | — |
| `jvm_threads_peak` | Gauge | Peak thread count since start | Burst capacity used | — |
| `jvm_threads_deadlocked` | Gauge | Threads in deadlock | >0 = immediate investigation needed | — |
| `jvm_threads_state` | Gauge | Threads by state | BLOCKED high = contention; WAITING high = I/O starvation | `state` |

### JVM Class Loading

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `jvm_classes_currently_loaded` | Gauge | Currently loaded classes | Metaspace pressure signal | — |
| `jvm_classes_loaded_total` | Counter | Total classes ever loaded | Class leak (monotonic with no unload) | — |

### Process Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `process_cpu_seconds_total` | Counter | Total CPU time consumed | Rate = CPU utilization | — |
| `process_resident_memory_bytes` | Gauge | RSS memory | OOMKill threshold proximity | — |
| `process_open_fds` | Gauge | Open file descriptors | FD exhaustion risk | — |
| `process_max_fds` | Gauge | Max allowed FDs | Headroom = max - open | — |
| `process_start_time_seconds` | Gauge | Process start timestamp | Uptime, restart detection | — |

### Kafka Consumer (via JMX)

When the JMX exporter is configured with Kafka consumer MBeans (standard in DataHub's JMX config), these metrics are available for MAE/MCE consumers:

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kafka_consumer_fetch_manager_metrics_records_lag` | Gauge | Consumer lag in records | Growing lag = consumer falling behind | `client_id`, `topic`, `partition` |
| `kafka_consumer_fetch_manager_metrics_records_lag_max` | Gauge | Max lag across partitions | Worst-case freshness | `client_id` |
| `kafka_consumer_fetch_manager_metrics_fetch_rate` | Gauge | Fetch requests per second | Consumer throughput | `client_id` |
| `kafka_consumer_fetch_manager_metrics_bytes_consumed_rate` | Gauge | Bytes consumed per second | Ingestion throughput | `client_id` |
| `kafka_consumer_coordinator_metrics_commit_rate` | Gauge | Offset commit rate | Commit failures = reprocessing risk | `client_id` |
| `kafka_consumer_coordinator_metrics_rebalance_rate_per_hour` | Gauge | Rebalance frequency | High = instability, consumer group churn | `client_id` |

> ⚠️ **Unconfirmed in live inventory**: Since no scrape target is configured, these metrics are NOT confirmed present in VictoriaMetrics. They are documented based on the standard `jmx_prometheus_javaagent` Kafka consumer MBean export behavior.

---

## 2. Micrometer Metrics (Chart ≥0.9.0 / DataHub ≥v1.5.0 — NOT at 0.8.21)

These metrics are documented here for **future reference** when the DataHub deployment is upgraded. They require the Micrometer Spring Actuator endpoint (port 4319).

### GraphQL API Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `graphql_request_duration_seconds` | Timer | GraphQL query latency distribution | Identify slow operations (p99 > 5s = problem) | `operation`, `operation_type`, `success` |
| `graphql_request_errors_total` | Counter | GraphQL errors by operation | Error rate spikes per operation | `operation`, `operation_type` |
| `graphql_field_duration_seconds` | Timer | Per-field resolver latency | N+1 detection, slow resolver identification | `parent_type`, `field`, `operation` |

### Kafka Consumer Queue Time

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `messaging_queue_time_seconds` | Timer | Time from message production to consumption | Data freshness SLA compliance | `messaging_system`, `topic`, `consumer_group` |

### DataHub Request Hook Latency

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `datahub_request_hook_queue_time_seconds` | Timer | End-to-end request-to-hook latency | Pipeline bottleneck identification | `hook` |

### Thread Pool Executors

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `executor_pool_size` | Gauge | Current thread count | Scaling behavior | `name` |
| `executor_active` | Gauge | Actively executing threads | Saturation signal (active ≈ max) | `name` |
| `executor_queued` | Gauge | Queued tasks | Growing queue = throughput insufficient | `name` |
| `executor_completed_total` | Counter | Completed tasks | Throughput rate | `name` |
| `executor_rejected_total` | Counter | Rejected tasks | >0 = pool saturated, requests dropped | `name` |

### Cache Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `cache_gets_total` | Counter | Cache access attempts | Hit rate = hit/(hit+miss); <0.7 = poorly sized cache | `cache`, `result` (hit/miss) |
| `cache_puts_total` | Counter | Entries added to cache | Write rate | `cache` |
| `cache_evictions_total` | Counter | Evicted entries | High eviction = undersized cache | `cache` |
| `cache_size` | Gauge | Current entry count | Capacity utilization | `cache` |

### API Usage Aggregation (opt-in)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `datahub_request_count` | Counter | API requests per flush window | Traffic patterns by operation type | `usage_operation`, `actor_class`, `request_api` |
| `datahub_usage_input_bytes` | Counter | Request body bytes | Ingestion throughput | `usage_operation`, `actor_class` |
| `datahub_usage_output_bytes` | Counter | Response body bytes | Read traffic volume | `usage_operation`, `actor_class` |
| `datahub_usage_active_identities` | Gauge | Unique active identities | User activity monitoring | `identity_metric`, `actor_class` |

### Rate Limiting (if enabled)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `gms_rate_limit_requests` | Counter | Rate-limited requests | Sustained denials = capacity issue | `rule_id` |
| `gms_rate_limit_adaptive_limit` | Gauge | Current adaptive limit | Dynamic capacity headroom | `rule_id` |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Action |
|---------|------------------------|--------|
| GMS unresponsive / slow | `jvm_memory_pool_bytes_used{pool="G1 Old Gen"}`, `jvm_gc_collection_seconds_sum` | Check if Full GC is thrashing; increase heap |
| MAE/MCE consumer lag growing | `kafka_consumer_fetch_manager_metrics_records_lag` | Check consumer resources, GC pressure, Elasticsearch health |
| Thread deadlock (pods healthy but hanging) | `jvm_threads_deadlocked` | >0 = restart pod, investigate root cause |
| High CPU but no visible load | `jvm_gc_collection_seconds_sum` rate | GC overhead > 20% = memory tuning needed |
| Frontend GraphQL timeout | `graphql_request_duration_seconds` p99 (≥v1.5 only) | Identify slow resolvers; check Elasticsearch latency |
| Metadata changes not appearing | `messaging_queue_time_seconds` (≥v1.5 only) | High queue time = consumer bottleneck |
| Pod OOMKilled | `jvm_memory_bytes_used{area="heap"}` vs limits | Increase memory limits or tune JVM heap |
| File descriptor exhaustion | `process_open_fds` / `process_max_fds` | Connection leak; check DB pool, HTTP clients |

---

## Enabling Scrape (Action Required)

To actually collect DataHub metrics into VictoriaMetrics, add a `VMPodScrape`:

```yaml
apiVersion: operator.victoriametrics.com/v1beta1
kind: VMPodScrape
metadata:
  name: datahub-jmx
  namespace: datahub
spec:
  podMetricsEndpoints:
    - port: "4318"        # JMX exporter port (unnamed — use targetPort)
      path: /metrics
      interval: 30s
  namespaceSelector:
    matchNames:
      - datahub
  selector:
    matchLabels:
      app.kubernetes.io/instance: datahub
```

> **Note**: Port 4318 is exposed by the JMX agent sidecar inside the container. If the chart doesn't name the port in the pod spec, use `targetPort: 4318` instead of `port`.

---

## Prerequisites — Cross-Reference (Do NOT Duplicate)

DataHub's backing services have their own metrics covered by existing skills:

| Backing Service | Deployed Config | Existing Skill |
|-----------------|-----------------|----------------|
| Kafka (datahub-prerequisites) | Bitnami Kafka, 1 broker | `strimzi-kafka-metrics` (for Strimzi-managed) / `backing-services-metrics` |
| Elasticsearch / OpenSearch | 3 replicas, elasticsearch-master | `opensearch-metrics` |
| PostgreSQL (RDS) | External RDS `eks-postgres` | `backing-services-metrics` |
| Redis (if used for caching) | — | `backing-services-metrics` |

---

## Complements

- **k8s-workload-metrics** — pod/container resource metrics (CPU, memory, restarts) for all DataHub pods
- **opensearch-metrics** — Elasticsearch/OpenSearch cluster health, indexing latency, JVM
- **strimzi-kafka-metrics** — Kafka broker/consumer metrics (for Strimzi-managed clusters)
- **backing-services-metrics** — PostgreSQL, Redis
- **go-apm-metrics** — N/A (DataHub is JVM/Java, not Go)

---

## Sources

- Deployed helmfile: `02-KUBE/00-CONFIG/k8s-setup/datahub/helmfile.yaml.gotmpl` — chart version 0.8.21
- Deployed values: `datahub/datahub/values.yaml.gotmpl` — `global.datahub.monitoring.enablePrometheus: true`
- Official monitoring docs: https://datahubproject.io/docs/advanced/monitoring
- Official updating docs (port 4318/4319 details): https://datahubproject.io/docs/how/updating-datahub
- JMX Prometheus Java agent: https://github.com/prometheus/jmx_exporter
- DataHub Helm chart source: https://github.com/acryldata/datahub-helm
