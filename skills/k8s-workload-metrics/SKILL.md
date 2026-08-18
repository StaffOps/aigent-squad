---
name: k8s-workload-metrics
description: >
  Use when diagnosing Kubernetes workload health — CPU throttling, OOM kills, memory pressure,
  network drops, node saturation, conntrack exhaustion, deployment rollout status, or HPA behavior.
  Covers cAdvisor (container_*), kube-state-metrics (kube_*), and node_exporter (node_*) metrics
  as confirmed present in the organization's live VictoriaMetrics inventory. Includes correlation
  patterns with APM runtime skills and MetricsQL query examples.
keywords:
  - container_cpu
  - container_memory
  - oomkill
  - oomkilled
  - oom
  - throttling
  - cfs_throttled
  - kube_pod
  - kube_deployment
  - kube_node
  - node_cpu
  - node_memory
  - conntrack
  - psi
  - pressure
  - working_set
  - hpa
  - pod restart
  - crashloop
  - eviction
  - cadvisor
  - kube-state-metrics
  - node_exporter
---

# Kubernetes Workload & Resource Metrics

> **Confirmed present in live VictoriaMetrics inventory (2026-07-06).**
> All metric names below are the exact Prometheus-form names scraped into VictoriaMetrics.

---

## 1. cAdvisor Metrics (`container_*`)

Source: kubelet's embedded cAdvisor. Per-container resource consumption at the cgroup level.

### 1.1 CPU

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_cpu_usage_seconds_total` | Counter | seconds | Cumulative CPU time consumed (all cores) | `rate()` gives actual CPU cores used; compare to quota for saturation | `namespace`, `pod`, `container`, `cpu` |
| `container_cpu_cfs_periods_total` | Counter | — | Number of elapsed CFS enforcement periods | Denominator for throttle ratio calculation | `namespace`, `pod`, `container` |
| `container_cpu_cfs_throttled_periods_total` | Counter | — | Number of periods where container was throttled | Numerator for throttle ratio; >25% indicates CPU limit too tight | `namespace`, `pod`, `container` |
| `container_cpu_cfs_throttled_seconds_total` | Counter | seconds | Total time the container was CPU-throttled | `rate()` gives seconds/second of throttling; directly impacts latency | `namespace`, `pod`, `container` |
| `container_spec_cpu_quota` | Gauge | microseconds | CFS CPU quota per period (−1 = unlimited) | `quota/period` = CPU limit in cores; compare to usage | `namespace`, `pod`, `container` |
| `container_spec_cpu_period` | Gauge | microseconds | CFS CPU period (typically 100000 = 100ms) | Used with quota to derive limit in cores | `namespace`, `pod`, `container` |

### 1.2 Memory

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_memory_working_set_bytes` | Gauge | bytes | Current working set (actively used memory; basis for OOM kill decisions) | **THE** metric to compare against limit — OOM kills when this ≥ limit | `namespace`, `pod`, `container` |
| `container_memory_rss` | Gauge | bytes | Resident Set Size (anonymous memory, heap) | Memory that cannot be reclaimed; true app footprint | `namespace`, `pod`, `container` |
| `container_memory_cache` | Gauge | bytes | Page cache memory (reclaimable under pressure) | High cache is normal for I/O-heavy apps; subtract from total for real pressure | `namespace`, `pod`, `container` |
| `container_spec_memory_limit_bytes` | Gauge | bytes | Memory limit for the container (0 = unlimited) | Ceiling for OOM kill calculation | `namespace`, `pod`, `container` |
| `container_oom_events_total` | Counter | — | Count of OOM events observed for the container | Direct OOM signal — correlate with restarts and `last_terminated_reason` | `namespace`, `pod`, `container` |

### 1.3 Network

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_network_receive_bytes_total` | Counter | bytes | Cumulative bytes received | Network throughput; compare inter-AZ for FinOps | `namespace`, `pod`, `interface` |
| `container_network_transmit_bytes_total` | Counter | bytes | Cumulative bytes transmitted | Egress throughput | `namespace`, `pod`, `interface` |
| `container_network_receive_errors_total` | Counter | — | Cumulative receive errors | Non-zero rate = NIC/driver/kernel issue | `namespace`, `pod`, `interface` |
| `container_network_transmit_errors_total` | Counter | — | Cumulative transmit errors | Non-zero rate = egress problem | `namespace`, `pod`, `interface` |
| `container_network_receive_packets_dropped_total` | Counter | — | Packets dropped while receiving | Kernel buffer overflow; correlate with PSI and conntrack | `namespace`, `pod`, `interface` |
| `container_network_transmit_packets_dropped_total` | Counter | — | Packets dropped while transmitting | Transmit queue full | `namespace`, `pod`, `interface` |

### 1.4 Disk I/O

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_fs_reads_bytes_total` | Counter | bytes | Cumulative bytes read from disk | I/O throughput for read-heavy workloads | `namespace`, `pod`, `container`, `device` |
| `container_fs_writes_bytes_total` | Counter | bytes | Cumulative bytes written to disk | Write throughput; spikes correlate with logging/checkpointing | `namespace`, `pod`, `container`, `device` |

### 1.5 PSI (Pressure Stall Information) — cgroup v2 + kernel 4.20+

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_pressure_cpu_waiting_seconds_total` | Counter | seconds | Time tasks spent waiting for CPU (some stalled) | True CPU saturation signal — better than usage alone | `namespace`, `pod`, `container` |
| `container_pressure_cpu_stalled_seconds_total` | Counter | seconds | Time ALL tasks stalled on CPU (full stall) | Severe CPU starvation | `namespace`, `pod`, `container` |
| `container_pressure_memory_waiting_seconds_total` | Counter | seconds | Time tasks spent waiting for memory (some stalled) | Memory pressure even before OOM — reclaim stalls | `namespace`, `pod`, `container` |
| `container_pressure_memory_stalled_seconds_total` | Counter | seconds | Time ALL tasks stalled on memory | Severe memory pressure; expect latency spikes | `namespace`, `pod`, `container` |
| `container_pressure_io_waiting_seconds_total` | Counter | seconds | Time tasks spent waiting for I/O (some stalled) | Disk/storage saturation at container level | `namespace`, `pod`, `container` |
| `container_pressure_io_stalled_seconds_total` | Counter | seconds | Time ALL tasks stalled on I/O | Complete I/O stall; check node disk metrics | `namespace`, `pod`, `container` |

### 1.6 Process / Lifecycle

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `container_processes` | Gauge | — | Number of processes inside the container | Fork bomb detection; unexpected process count | `namespace`, `pod`, `container` |
| `container_threads` | Gauge | — | Number of threads inside the container | Thread starvation/exhaustion correlation (see .NET ThreadPool metrics) | `namespace`, `pod`, `container` |
| `container_start_time_seconds` | Gauge | seconds (unix epoch) | Container start time | Calculate uptime; detect frequent restarts | `namespace`, `pod`, `container` |
| `container_last_seen` | Gauge | timestamp | Last time cAdvisor saw the container | Stale containers; detect zombie pods | `namespace`, `pod`, `container` |

---

## 2. kube-state-metrics (`kube_*`)

Source: kube-state-metrics. Kubernetes object state as metrics — no runtime data, purely API-server state.

### 2.1 Pod / Container Status

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_pod_container_status_restarts_total` | Counter | — | Number of container restarts | `increase()` over time detects CrashLoopBackOff; OOM triad member | `namespace`, `pod`, `container`, ⚠️`uid` |
| `kube_pod_container_status_last_terminated_reason` | Gauge | — | Last reason container was terminated (value=1 per reason) | Filter `reason="OOMKilled"` for OOM confirmation | `namespace`, `pod`, `container`, `reason` |
| `kube_pod_container_status_waiting_reason` | Gauge | — | Reason container is in waiting state (value=1 per reason) | `reason="CrashLoopBackOff"` or `"ImagePullBackOff"` | `namespace`, `pod`, `container`, `reason` |
| `kube_pod_status_phase` | Gauge | — | Pod's current phase (value=1 for active phase) | Filter `phase="Pending"` for scheduling issues | `namespace`, `pod`, `phase`, ⚠️`uid` |
| `kube_pod_status_qos_class` | Gauge | — | Pod's QoS class | `qos_class="BestEffort"` = first eviction target | `namespace`, `pod`, `qos_class` |

### 2.2 Resource Requests/Limits

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_pod_container_resource_requests` | Gauge | cores/bytes | Requested resource by container | Compare actual usage to requests for right-sizing | `namespace`, `pod`, `container`, `resource`, `unit`, `node` |
| `kube_pod_container_resource_limits` | Gauge | cores/bytes | Resource limit by container | Ceiling for throttling (CPU) or OOM (memory) | `namespace`, `pod`, `container`, `resource`, `unit`, `node` |

### 2.3 Deployment Status

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_deployment_spec_replicas` | Gauge | — | Desired number of replicas | Baseline for availability check | `namespace`, `deployment` |
| `kube_deployment_status_replicas_available` | Gauge | — | Number of available replicas | `available < spec` = degraded service | `namespace`, `deployment` |
| `kube_deployment_status_replicas_unavailable` | Gauge | — | Number of unavailable replicas | >0 sustained = rollout stuck or crash loop | `namespace`, `deployment` |
| `kube_deployment_status_replicas_updated` | Gauge | — | Number of replicas matching desired template | `updated < spec` = rollout in progress | `namespace`, `deployment` |

### 2.4 HPA (Horizontal Pod Autoscaler)

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_horizontalpodautoscaler_status_current_replicas` | Gauge | — | Current replica count managed by HPA | Compare to desired/max for saturation | `namespace`, `horizontalpodautoscaler` |
| `kube_horizontalpodautoscaler_status_desired_replicas` | Gauge | — | Desired replica count computed by HPA | `desired > current` = scaling up; `desired == max` = ceiling hit | `namespace`, `horizontalpodautoscaler` |
| `kube_horizontalpodautoscaler_spec_max_replicas` | Gauge | — | Maximum replicas configured | Ceiling; when desired == max, may need increase | `namespace`, `horizontalpodautoscaler` |
| `kube_horizontalpodautoscaler_spec_min_replicas` | Gauge | — | Minimum replicas configured | Floor for availability | `namespace`, `horizontalpodautoscaler` |

### 2.5 PodDisruptionBudget

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_poddisruptionbudget_status_current_healthy` | Gauge | — | Current number of healthy pods | Compare to `desired_healthy` for safety margin | `namespace`, `poddisruptionbudget` |
| `kube_poddisruptionbudget_status_desired_healthy` | Gauge | — | Minimum desired healthy pods | Below this = PDB violated; blocks drains | `namespace`, `poddisruptionbudget` |
| `kube_poddisruptionbudget_status_pod_disruptions_allowed` | Gauge | — | Number of disruptions currently allowed | 0 = drain/eviction blocked by PDB | `namespace`, `poddisruptionbudget` |

### 2.6 Node Status

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `kube_node_status_condition` | Gauge | — | Node condition status (value=1 per condition/status) | `condition="Ready",status="true"` = healthy node | `node`, `condition`, `status` |
| `kube_node_status_allocatable` | Gauge | cores/bytes | Resources allocatable on the node | Scheduling capacity ceiling | `node`, `resource`, `unit` |
| `kube_node_status_capacity` | Gauge | cores/bytes | Total resource capacity on the node | Raw capacity before system reservations | `node`, `resource`, `unit` |

---

## 3. node_exporter Metrics (`node_*`)

Source: node_exporter DaemonSet. Host-level (node) metrics from `/proc` and `/sys`.

### 3.1 CPU

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `node_cpu_seconds_total` | Counter | seconds | CPU time by mode | `rate(..{mode="idle"})` gives available CPU; `1 - idle` = utilization | `cpu`, `mode` (idle/user/system/iowait/steal/irq/softirq) |
| `node_load1` | Gauge | — | 1-minute load average | Quick saturation check; load > CPU count = queued work | — |
| `node_load5` | Gauge | — | 5-minute load average | Sustained load signal | — |
| `node_load15` | Gauge | — | 15-minute load average | Long-term load baseline | — |

### 3.2 Memory

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `node_memory_MemAvailable_bytes` | Gauge | bytes | Memory available for use (kernel's estimate) | **Primary** node memory pressure signal; includes reclaimable | — |
| `node_memory_MemFree_bytes` | Gauge | bytes | Completely free memory | Usually low; not alarming alone (kernel caches aggressively) | — |
| `node_vmstat_oom_kill` | Gauge | — | Count of OOM kills on the node (from `/proc/vmstat`) | Node-wide OOM signal; correlate with which pods got killed | — |

### 3.3 Filesystem

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `node_filesystem_avail_bytes` | Gauge | bytes | Available filesystem space (non-root) | `avail/size < 0.1` = critical; containers evicted at 85% | `device`, `mountpoint`, `fstype` |
| `node_filesystem_size_bytes` | Gauge | bytes | Total filesystem size | Denominator for utilization calculation | `device`, `mountpoint`, `fstype` |
| `node_disk_io_time_seconds_total` | Counter | seconds | Total time disk was busy doing I/O | `rate()` = disk utilization (0–1); near 1 = saturated | `device` |

### 3.4 PSI (node-level)

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `node_pressure_cpu_waiting_seconds_total` | Counter | seconds | Time some tasks waiting for CPU (node-wide) | Node-level CPU saturation; affects all pods on node | — |
| `node_pressure_io_waiting_seconds_total` | Counter | seconds | Time some tasks waiting for I/O (node-wide) | Disk saturation affecting all tenants | — |
| `node_pressure_io_stalled_seconds_total` | Counter | seconds | Time ALL tasks stalled on I/O (node-wide) | Full I/O stall — critical alert threshold | — |
| `node_pressure_memory_waiting_seconds_total` | Counter | seconds | Time some tasks waiting for memory (node-wide) | Memory pressure even before OOM — reclaim storms | — |
| `node_pressure_memory_stalled_seconds_total` | Counter | seconds | Time ALL tasks stalled on memory (node-wide) | Severe memory starvation | — |

### 3.5 Network / Conntrack (KEY for high-connection services)

| Metric | Type | Unit | Description | Troubleshooting use | Key labels |
|--------|------|------|-------------|---------------------|------------|
| `node_nf_conntrack_entries` | Gauge | — | Current number of conntrack entries | Compare to limit; >80% = danger zone for new connections | — |
| `node_nf_conntrack_entries_limit` | Gauge | — | Maximum conntrack table entries | Ceiling; when entries ≈ limit, new TCP connections fail silently | — |
| `node_sockstat_TCP_inuse` | Gauge | — | TCP sockets currently in use | Connection pool sizing; correlate with app connection errors | — |
| `node_sockstat_TCP_orphan` | Gauge | — | Orphaned TCP sockets (no owning process) | Leak signal; should be near zero | — |
| `node_sockstat_TCP_tw` | Gauge | — | TCP sockets in TIME_WAIT state | High values with conntrack near limit = connection issues | — |
| `node_netstat_Tcp_RetransSegs` | Gauge | — | TCP segments retransmitted (from `/proc/net/netstat`) | Network quality signal; high retransmission = packet loss or congestion | — |
| `node_netstat_TcpExt_ListenDrops` | Gauge | — | Connections dropped from listen queue (from `/proc/net/netstat`) | Server backlog full; app not accepting fast enough | — |
| `node_netstat_TcpExt_ListenOverflows` | Gauge | — | Listen queue overflows (from `/proc/net/netstat`) | Same as ListenDrops; both signal accept queue saturation | — |

---

## 4. How Metrics Interrelate (Correlation Patterns)

### 4.1 The OOM Triad

Three signals that together confirm an OOM kill:

```
container_memory_working_set_bytes ≈ container_spec_memory_limit_bytes   (pressure)
  + container_oom_events_total increasing                                  (kill happened)
  + kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}   (K8s confirms)
  + increase(kube_pod_container_status_restarts_total) > 0                 (pod restarted)
```

**Cross-skill**: correlate with `process.runtime.dotnet.gc.heap.size` (.NET) or `process.runtime.cpython.memory` (Python) from APM runtime skills to identify which runtime component is leaking.

### 4.2 Throttling → Latency (without high CPU%)

CPU throttling can degrade latency even when average CPU usage looks low:

```
Throttle ratio: rate(container_cpu_cfs_throttled_periods_total[5m]) / rate(container_cpu_cfs_periods_total[5m])
  > 0.25 = significant throttling

Container is using bursts but stays within average — limit is too tight.
```

**Cross-skill**: correlate with `http.server.request.duration` p99 from APM skills. Throttling causes tail latency spikes invisible in average CPU.

### 4.3 Conntrack Exhaustion → Connection Failures

```
node_nf_conntrack_entries / node_nf_conntrack_entries_limit > 0.8  (danger zone)
  + node_sockstat_TCP_tw is high                                     (TIME_WAIT consuming slots)
  → New connections silently fail; apps see "connection refused" or timeout
```

**Cross-skill**: correlate with `http.client.request.duration` p99 and `http.client.open_connections` from .NET APM. Connection pool exhaustion might be a symptom, not a cause.

### 4.4 PSI = True Saturation Signal

CPU utilization (usage/limit) can be misleading because of averaging. PSI tells you tasks are ACTUALLY waiting:

```
rate(container_pressure_cpu_waiting_seconds_total[5m]) > 0.1  → 10% of time, tasks waited for CPU
rate(node_pressure_memory_stalled_seconds_total[5m]) > 0.05  → 5% full stall on memory
```

**Cross-skill**: PSI memory waiting correlates with GC pauses in .NET (`process.runtime.dotnet.gc.collections.count`) and Python (`process.runtime.cpython.gc_count`).

### 4.5 HPA Ceiling + Throttling = Under-provisioned

```
kube_horizontalpodautoscaler_status_desired_replicas == kube_horizontalpodautoscaler_spec_max_replicas
  + container_cpu_cfs_throttled_periods_total rate > 0
  → HPA wants to scale but can't; individual pods are throttled
```

### 4.6 Deployment Rollout Stuck

```
kube_deployment_status_replicas_unavailable > 0  for > 10m
  + kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1
  → New version is crash-looping; rollout is stuck
```

---

## 5. Symptom → Metric Quick-Reference

| Symptom | First metrics to check | MetricsQL query |
|---------|------------------------|-----------------|
| **Latency spikes without high CPU** | CFS throttling | `rate(container_cpu_cfs_throttled_seconds_total{namespace="$ns",pod=~"$app.*"}[5m])` |
| **Pod crash-looping** | OOM triad | `kube_pod_container_status_last_terminated_reason{reason="OOMKilled",namespace="$ns"}` |
| **Memory growing monotonically** | Working set vs limit | `container_memory_working_set_bytes{pod=~"$app.*"} / container_spec_memory_limit_bytes{pod=~"$app.*"}` |
| **Connection failures / timeouts** | Conntrack + ListenDrops | `node_nf_conntrack_entries / node_nf_conntrack_entries_limit` |
| **Deployment stuck at partial rollout** | Unavailable replicas | `kube_deployment_status_replicas_unavailable{namespace="$ns",deployment="$app"} > 0` |
| **Pods pending (not scheduling)** | Node allocatable | `sum(kube_node_status_allocatable{resource="cpu"}) - sum(kube_pod_container_resource_requests{resource="cpu"})` |
| **Node under memory pressure** | Node PSI + MemAvailable | `rate(node_pressure_memory_waiting_seconds_total[5m])` |
| **Disk saturation** | io_time + PSI I/O | `rate(node_disk_io_time_seconds_total{device!~"dm-.*"}[5m])` |
| **HPA not scaling** | Desired vs max | `kube_horizontalpodautoscaler_status_desired_replicas == kube_horizontalpodautoscaler_spec_max_replicas` |
| **TCP retransmissions (network issues)** | RetransSegs rate | `rate(node_netstat_Tcp_RetransSegs[5m])` |
| **Server accept queue full** | ListenDrops | `rate(node_netstat_TcpExt_ListenDrops[5m]) > 0` |
| **Pod evictions** | QoS class + node OOM | `kube_pod_status_qos_class{qos_class="BestEffort"} * on(pod,namespace) kube_pod_container_status_restarts_total` |
| **PDB blocking drain** | Disruptions allowed | `kube_poddisruptionbudget_status_pod_disruptions_allowed == 0` |

---

## 6. Recording Rule / Query Examples

### CPU throttle ratio (per workload)

```yaml
# Recording rule
- record: workload:container_cpu_cfs_throttled_ratio:rate5m
  expr: |
    sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total{container!=""}[5m]))
    /
    sum by (namespace, pod) (rate(container_cpu_cfs_periods_total{container!=""}[5m]))
```

### Memory utilization vs limit

```promql
# Instant: pods over 80% memory limit
container_memory_working_set_bytes{container!=""}
  / on(namespace,pod,container) container_spec_memory_limit_bytes{container!=""}
  > 0.8
```

### Conntrack saturation per node

```promql
# Alert when conntrack > 80% of limit
node_nf_conntrack_entries / node_nf_conntrack_entries_limit > 0.8
```

### OOM events rate (last 1h)

```promql
increase(container_oom_events_total{namespace="$ns"}[1h]) > 0
```

### Node CPU saturation via PSI

```promql
# Percentage of time tasks are waiting for CPU (node-wide)
rate(node_pressure_cpu_waiting_seconds_total[5m]) * 100
```

### Deployment health ratio

```promql
kube_deployment_status_replicas_available
  / on(namespace,deployment) kube_deployment_spec_replicas
  < 1
```

---

## 7. High-Cardinality Label Warnings

| Label | Found on | Risk | Mitigation |
|-------|----------|------|------------|
| ⚠️ `uid` (pod UID) | All `kube_*` pod metrics | Unique per pod lifecycle — explodes on frequent restarts | Aggregate by `namespace`+`pod` or `namespace`+`deployment`; drop `uid` in recording rules |
| ⚠️ `pod` | All container/pod metrics | High cardinality in namespaces with many short-lived pods (Jobs, CronJobs) | Use recording rules that aggregate to workload level (`deployment`, `statefulset`) |
| ⚠️ `container_id` | `kube_pod_container_info` | Unique per container instance | Never use in alerting/recording rules; informational only |
| ⚠️ `image_id` | `kube_pod_container_info` | Changes with every build | Never group by; use `image` (repo+tag) if needed |
| ⚠️ `cpu` (core number) | `container_cpu_usage_seconds_total`, `node_cpu_seconds_total` | One series per core (up to 192 on large instances) | Always aggregate across cores; drop label in remote_write if not needed |
| ⚠️ `device` | `node_disk_*`, `container_fs_*` | Many virtual devices (dm-*, loop*) | Filter: `device!~"dm-.*|loop.*"` |

---

## 8. Cross-Skill References

| This skill's signal | Related APM runtime skill | Correlation |
|---------------------|--------------------------|-------------|
| CPU throttle ratio > 25% | `dotnet-apm-metrics`: `process.runtime.dotnet.threadpool.queue.length` | Throttling causes thread pool queuing; both spike together |
| `container_memory_working_set_bytes` growing | `dotnet-apm-metrics`: `process.runtime.dotnet.gc.heap.size` | Heap growth is the runtime-level cause of working_set growth |
| `container_memory_working_set_bytes` growing | `python-apm-metrics`: `process.runtime.cpython.memory` | CPython RSS growth maps to working_set |
| Node conntrack near limit | `dotnet-apm-metrics`: `http.client.request.time_in_queue` | New connections blocked → requests queue → timeout |
| PSI memory stall | `go-apm-metrics`: `go.memory.gc.pause_ns_total` | GC pauses lengthen under memory pressure |
| `container_threads` high | `dotnet-apm-metrics`: `process.runtime.dotnet.threadpool.queue.length` | Thread exhaustion signal; .NET ThreadPool metrics show root cause |
| Node `node_netstat_Tcp_RetransSegs` high | `apm-metrics-cross-runtime`: `http.client.request.duration` p99 | Retransmissions inflate outbound HTTP latency |
