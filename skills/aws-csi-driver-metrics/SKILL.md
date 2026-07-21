---
name: aws-csi-driver-metrics
description: "Use when diagnosing AWS CSI driver health — EBS volume provisioning/attach failures, EC2 API throttling, NVMe I/O saturation, EFS mount latency, or S3 Mountpoint OTLP metrics. Covers aws_ebs_csi_*, csi_sidecar_operations_seconds, workqueue_*, leader_election_*, rest_client_*, and kubelet_volume_stats_*. Grounded on Helm charts aws-ebs-csi-driver v2.38.1, aws-efs-csi-driver v4.3.0, aws-mountpoint-s3-csi-driver v2.5.0."
keywords: [aws-csi-driver, aws, csi, driver, "aws csi", "csi driver", ebs, ec2, efs, s3, otlp]
---
# AWS CSI Driver Metrics — EBS, EFS, Mountpoint-S3

Combined metrics reference for **all three AWS CSI drivers** deployed in the environment.

**Pipeline**: CSI controller/node pods (`:3301/metrics`, `:9808/metrics`) → vmagent scrape (via ServiceMonitor) → VictoriaMetrics.

**Deployed chart versions** (from k8s-setup helmfiles):
| Driver | Chart | Repo |
|--------|-------|------|
| EBS CSI | `aws-ebs-csi-driver v2.38.1` | kubernetes-sigs.github.io/aws-ebs-csi-driver |
| EFS CSI | `aws-efs-csi-driver v4.3.0` | kubernetes-sigs.github.io/aws-efs-csi-driver |
| Mountpoint S3 CSI | `aws-mountpoint-s3-csi-driver v2.5.0` | awslabs.github.io/mountpoint-s3-csi-driver |

**Configuration highlights (from deployed values)**:
- EBS: `controller.enableMetrics: true`, `node.enableMetrics: true`, `controller.serviceMonitor.forceEnable: true`
- EFS: No explicit metrics enablement (sidecars only)
- Mountpoint S3: OTLP-based monitoring (added Nov 2025); no native Prometheus `/metrics` endpoint in chart v2.5.0

---

## 1. Shared CSI Sidecar Metrics (All Drivers)

These metrics come from the [kubernetes-csi sidecar containers](https://kubernetes-csi.github.io/docs/sidecar-containers.html) — `external-provisioner`, `external-attacher`, `external-resizer`, `external-snapshotter` — via [`csi-lib-utils/metrics`](https://github.com/kubernetes-csi/csi-lib-utils/blob/master/metrics/metrics.go). Present on **any** CSI controller pod that exposes a metrics endpoint.

### CSI RPC Operations

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `csi_sidecar_operations_seconds_bucket` | Histogram | Latency distribution of CSI gRPC calls (CreateVolume, DeleteVolume, ControllerPublishVolume, etc.) | P99 provisioning/attach latency; slow calls indicate backend or API throttling | `driver_name`, `method_name`, `grpc_status_code`, `le` |
| `csi_sidecar_operations_seconds_count` | Counter | Total CSI gRPC calls completed | Rate of operations; sudden drop = sidecar issue | `driver_name`, `method_name`, `grpc_status_code` |
| `csi_sidecar_operations_seconds_sum` | Counter | Cumulative duration of CSI gRPC calls | Average latency = sum/count | `driver_name`, `method_name`, `grpc_status_code` |

**Key `method_name` values**: `CreateVolume`, `DeleteVolume`, `ControllerPublishVolume`, `ControllerUnpublishVolume`, `ControllerExpandVolume`, `CreateSnapshot`, `DeleteSnapshot`.

**Key `grpc_status_code` values**: `OK`, `ResourceExhausted` (throttled), `Internal`, `DeadlineExceeded`, `Unavailable`.

### Workqueue Metrics (controller-runtime / client-go)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `workqueue_adds_total` | Counter | Total items added to the work queue | Rate of incoming reconciliation work | `name` |
| `workqueue_depth` | Gauge | Current queue depth | Growing depth = processing can't keep up | `name` |
| `workqueue_queue_duration_seconds_bucket` | Histogram | Time items spend waiting in queue before processing | P99 queue wait > 10s = controller saturated | `name`, `le` |
| `workqueue_work_duration_seconds_bucket` | Histogram | Time to actually process a work item | Slow processing = upstream API latency | `name`, `le` |
| `workqueue_retries_total` | Counter | Total retries | High retry rate = persistent failures | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of the longest in-progress processor | Stuck processor = potential deadlock | `name` |
| `workqueue_unfinished_work_seconds` | Gauge | Total seconds of unfinished work (depth × avg processing time) | Capacity planning signal | `name` |

### Leader Election Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `leader_election_master_status` | Gauge | 1 if this replica is the leader, 0 otherwise | Split-brain detection; sum > 1 = problem | `name` |

### REST Client (kube-apiserver communication)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `rest_client_requests_total` | Counter | Total HTTP requests to kube-apiserver | Rate by `code` shows 429 (throttled) or 5xx (apiserver issues) | `method`, `code` |
| `rest_client_request_duration_seconds_bucket` | Histogram | Latency of kube-apiserver requests | Slow apiserver = cascading CSI delays | `verb`, `url`, `le` |

---

## 2. EBS CSI Driver Metrics (`ebs.csi.aws.com`)

**Source**: Official [docs/metrics.md](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/metrics.md) for chart v2.38.x.

Metrics enabled via `controller.enableMetrics: true` → port `3301/metrics` (controller), port `3302/metrics` (node).

### AWS EC2 API Metrics (Controller — port 3301)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `aws_ebs_csi_api_request_duration_seconds_bucket` | Histogram | Latency of AWS SDK API calls (CreateVolume, AttachVolume, DescribeVolumes, etc.) | P99 EC2 API latency; high = regional throttling or service degradation | `request`, `le` |
| `aws_ebs_csi_api_request_duration_seconds_count` | Counter | Total AWS API requests by type | Rate of API calls; capacity planning against EC2 rate limits | `request` |
| `aws_ebs_csi_api_request_duration_seconds_sum` | Counter | Cumulative AWS API call duration | Average latency = sum/count | `request` |
| `aws_ebs_csi_api_request_errors_total` | Counter | **KEY** — Total AWS API errors | Non-zero rate = EC2 API returning errors; check `error` label for code | `request`, `error` |
| `aws_ebs_csi_api_request_throttles_total` | Counter | **KEY** — Total throttled (rate-limited) AWS API requests | Non-zero = hitting EC2 API rate limits; correlate with request type | `request` |
| `aws_ebs_csi_ec2_detach_pending_seconds` | Counter | Seconds waiting for volume detach to complete | High value = volume stuck in `detaching` state (EC2 side) | `attachment_state`, `volume_id`, `instance_id` |

**Key `request` label values**: `CreateVolume`, `DeleteVolume`, `AttachVolume`, `DetachVolume`, `DescribeVolumes`, `DescribeInstances`, `CreateSnapshot`, `DeleteSnapshot`, `DescribeSnapshots`, `ModifyVolume`.

**Key `error` label values**: `RequestLimitExceeded`, `VolumeInUse`, `InvalidVolume.NotFound`, `Client.VolumeInUse`, `IncorrectState`.

### EBS NVMe Performance Metrics (Node — port 3302)

Sourced from [EBS detailed performance stats](https://docs.aws.amazon.com/ebs/latest/userguide/nvme-detailed-performance-stats.html) via NVMe ioctl.

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `aws_ebs_csi_read_ops_total` | Counter | Total completed read operations | IOPS rate (read) per volume | `instance_id`, `volume_id` |
| `aws_ebs_csi_write_ops_total` | Counter | Total completed write operations | IOPS rate (write) per volume | `instance_id`, `volume_id` |
| `aws_ebs_csi_read_bytes_total` | Counter | Total bytes read | Read throughput per volume | `instance_id`, `volume_id` |
| `aws_ebs_csi_write_bytes_total` | Counter | Total bytes written | Write throughput per volume | `instance_id`, `volume_id` |
| `aws_ebs_csi_read_seconds_total` | Counter | Total time spent in read operations | Read latency contribution = seconds/ops | `instance_id`, `volume_id` |
| `aws_ebs_csi_write_seconds_total` | Counter | Total time spent in write operations | Write latency contribution = seconds/ops | `instance_id`, `volume_id` |
| `aws_ebs_csi_exceeded_iops_seconds_total` | Counter | **KEY** — Time volume exceeded provisioned IOPS | Non-zero rate = volume under-provisioned for IOPS | `instance_id`, `volume_id` |
| `aws_ebs_csi_exceeded_tp_seconds_total` | Counter | **KEY** — Time volume exceeded provisioned throughput | Non-zero rate = volume under-provisioned for throughput | `instance_id`, `volume_id` |
| `aws_ebs_csi_ec2_exceeded_iops_seconds_total` | Counter | Time volume exceeded EC2 instance's IOPS limit | Instance-level IOPS bottleneck (not volume-level) | `instance_id`, `volume_id` |
| `aws_ebs_csi_ec2_exceeded_tp_seconds_total` | Counter | Time volume exceeded EC2 instance's throughput limit | Instance-level throughput bottleneck | `instance_id`, `volume_id` |
| `aws_ebs_csi_volume_queue_length` | Gauge | Pending I/O operations in flight | High queue = volume saturated; correlate with exceeded_* | `instance_id`, `volume_id` |
| `aws_ebs_csi_read_io_latency_seconds_bucket` | Histogram | Read I/O latency distribution | P99 read latency per volume | `instance_id`, `volume_id`, `le` |
| `aws_ebs_csi_write_io_latency_seconds_bucket` | Histogram | Write I/O latency distribution | P99 write latency per volume | `instance_id`, `volume_id`, `le` |
| `aws_ebs_csi_nvme_collector_scrapes_total` | Counter | NVMe collector scrape attempts | Collector operational health | — |
| `aws_ebs_csi_nvme_collector_errors_total` | Counter | NVMe collector scrape errors | Non-zero = can't read NVMe stats (permissions, device issue) | — |
| `aws_ebs_csi_nvme_collector_duration_seconds_bucket` | Histogram | NVMe collector scrape duration | Slow scrape = many volumes on node | `le` |

> ⚠️ **Cardinality note**: NVMe metrics have `volume_id` and `instance_id` labels — cardinality scales with (nodes × volumes). Monitor `tsdb_status` if volume count is high.

---

## 3. EFS CSI Driver Metrics (`efs.csi.aws.com`)

**Chart**: `aws-efs-csi-driver v4.3.0` (kubernetes-sigs).

The EFS CSI driver **does NOT expose driver-specific Prometheus metrics** (no `efs_csi_*` prefix exists in the source code as of v2.1.x app version bundled with chart v4.3.0). The controller pod contains standard CSI sidecars (`csi-provisioner`) that expose the **shared sidecar metrics** from Section 1 if a metrics port is configured.

**What IS available**:
- `csi_sidecar_operations_seconds` — from `csi-provisioner` sidecar (covers `CreateVolume`/`DeleteVolume` for EFS access points)
- `workqueue_*`, `rest_client_*`, `leader_election_*` — from controller-runtime
- Standard Go runtime metrics (`go_*`, `process_*`) if metrics are enabled

**What is NOT available**:
- ❌ No EFS-specific throughput or IOPS metrics from the driver
- ❌ No AWS API call metrics like EBS has (no `efs_csi_api_*`)
- ❌ No mount latency metrics

**How to monitor EFS health**: Use CloudWatch EFS metrics (`PercentIOLimit`, `BurstCreditBalance`, `TotalIOBytes`) via the `aws` skill or CloudWatch exporter, not the CSI driver itself.

> **Note**: The deployed values do NOT set `controller.enableMetrics` or a ServiceMonitor for EFS. The CSI sidecars may still expose metrics on their default port, but scraping is not explicitly configured.

---

## 4. Mountpoint for Amazon S3 CSI Driver (`s3.csi.aws.com`)

**Chart**: `aws-mountpoint-s3-csi-driver v2.5.0` (awslabs).

### Prometheus Endpoint Status

As of chart v2.5.0, Mountpoint S3 CSI driver **does NOT expose a native Prometheus `/metrics` endpoint**. The driver's node plugin and sidecars do not have metrics ports configured in the deployed values.

### OTLP-Based Monitoring (Nov 2025+)

AWS [announced](https://aws.amazon.com/about-aws/whats-new/2025/11/mountpoint-amazon-s3-csi-driver-monitoring-capability/) OTLP support for Mountpoint metrics in November 2025. When enabled, Mountpoint emits metrics via OpenTelemetry Protocol including:
- FUSE request count and latency
- S3 API call counts and durations
- Memory consumption

**However**: This requires explicit configuration of an OTLP collector endpoint in the CSI driver, which is **NOT configured in the current deployed values** for chart v2.5.0. The metrics would flow App → OTel Collector → VictoriaMetrics (not via vmagent scrape).

### What IS Available

- `csi_sidecar_operations_seconds` — from `csi-node-driver-registrar` (minimal; node-only driver with no provisioner sidecar)
- Standard Go runtime (`go_*`, `process_*`) — only if an HTTP endpoint is exposed (not currently configured)

### What is NOT Available

- ❌ No S3-specific metrics (no `s3_csi_*` or `mountpoint_*` prefix)
- ❌ No volume IOPS/throughput (S3 is object storage — different model)
- ❌ No Prometheus scrape target configured

**How to monitor S3 Mountpoint**: Use CloudWatch S3 request metrics (`s3:GetObject` latency/errors) and consider enabling OTLP in a future chart upgrade.

---

## 5. Kubelet Volume Stats (All Drivers)

Available for **any** CSI driver implementing `NodeGetVolumeStats` (EBS implements it; EFS and S3 do not for their respective volume types).

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `kubelet_volume_stats_capacity_bytes` | Gauge | Total capacity of the volume | Baseline for utilization calculations | `namespace`, `persistentvolumeclaim` |
| `kubelet_volume_stats_available_bytes` | Gauge | Available bytes on volume | Low value = disk filling up | `namespace`, `persistentvolumeclaim` |
| `kubelet_volume_stats_used_bytes` | Gauge | Used bytes on volume | Growth rate predicts exhaustion | `namespace`, `persistentvolumeclaim` |
| `kubelet_volume_stats_inodes` | Gauge | Total inodes | Baseline for inode saturation | `namespace`, `persistentvolumeclaim` |
| `kubelet_volume_stats_inodes_free` | Gauge | Free inodes | Low = small-file workload exhausting inodes | `namespace`, `persistentvolumeclaim` |
| `kubelet_volume_stats_inodes_used` | Gauge | Used inodes | Growth rate | `namespace`, `persistentvolumeclaim` |

> **Scope**: `kubelet_volume_stats_*` applies to **EBS volumes only** in this environment. EFS (NFS) and S3 (FUSE) do not report filesystem stats via this interface.

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Root Cause |
|---------|------------------------|------------|
| PVC stuck Pending | `csi_sidecar_operations_seconds_count{method_name="CreateVolume", grpc_status_code!="OK"}` | Provisioning failure — check `grpc_status_code` |
| Volume attach timeout | `aws_ebs_csi_api_request_duration_seconds{request="AttachVolume"}` p99 | EC2 API slow or throttled |
| EC2 API throttling | `aws_ebs_csi_api_request_throttles_total` rate > 0 | Hitting EC2 rate limits — batch operations or request increase |
| Volume stuck detaching | `aws_ebs_csi_ec2_detach_pending_seconds` growing | EC2 detach stalled — check instance state |
| Slow disk I/O | `aws_ebs_csi_read_io_latency_seconds` / `write_io_latency_seconds` p99 | Volume type under-provisioned or instance bottleneck |
| IOPS exceeded | `aws_ebs_csi_exceeded_iops_seconds_total` rate > 0 | Volume needs more provisioned IOPS (or switch gp3→io2) |
| Throughput exceeded | `aws_ebs_csi_exceeded_tp_seconds_total` rate > 0 | Volume throughput cap hit — increase provisioned or upgrade type |
| Instance-level bottleneck | `aws_ebs_csi_ec2_exceeded_iops_seconds_total` rate > 0 | Instance type caps EBS performance — upsize instance |
| Disk filling up | `kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes > 0.85` | Volume expansion needed or application leaking disk |
| CSI controller overwhelmed | `workqueue_depth{name=~".*csi.*"}` growing | Too many concurrent volume operations; check replica count |
| NVMe stats collection failing | `aws_ebs_csi_nvme_collector_errors_total` rate > 0 | Node permissions or NVMe device access issue |

### Key Queries

```promql
# EBS provisioning failure rate (last 5m)
sum(rate(csi_sidecar_operations_seconds_count{driver_name="ebs.csi.aws.com", method_name="CreateVolume", grpc_status_code!="OK"}[5m]))
/ sum(rate(csi_sidecar_operations_seconds_count{driver_name="ebs.csi.aws.com", method_name="CreateVolume"}[5m]))

# EBS API throttle rate
sum(rate(aws_ebs_csi_api_request_throttles_total[5m])) by (request)

# P99 volume provision latency
histogram_quantile(0.99,
  sum(rate(csi_sidecar_operations_seconds_bucket{driver_name="ebs.csi.aws.com", method_name="CreateVolume"}[5m])) by (le)
)

# Volumes exceeding provisioned IOPS (any volume)
sum(rate(aws_ebs_csi_exceeded_iops_seconds_total[5m])) by (volume_id) > 0

# Volume utilization > 85%
(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) > 0.85

# EFS access point creation errors
sum(rate(csi_sidecar_operations_seconds_count{driver_name="efs.csi.aws.com", grpc_status_code!="OK"}[5m])) by (method_name, grpc_status_code)
```

---

## Complements

- `k8s-workload-metrics` — pod-level CPU/memory for CSI controller/node pods
- `go-apm-metrics` — Go runtime (goroutines, GC, memory) for CSI driver processes
- `karpenter-metrics` — node provisioning that may block volume attachment
- `backing-services-metrics` — if EBS-backed databases show I/O saturation

---

## Sources

- [aws-ebs-csi-driver docs/metrics.md](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/metrics.md) — official metric names for chart v2.38.x
- [kubernetes-csi/csi-lib-utils metrics.go](https://github.com/kubernetes-csi/csi-lib-utils/blob/master/metrics/metrics.go) — CSI sidecar metric definitions
- [AWS: EBS detailed performance stats](https://docs.aws.amazon.com/ebs/latest/userguide/nvme-detailed-performance-stats.html) — NVMe counters exposed by node
- [AWS: Mountpoint S3 monitoring announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/mountpoint-amazon-s3-csi-driver-monitoring-capability/) — OTLP support (Nov 2025)
- [kubernetes-sigs/aws-efs-csi-driver](https://github.com/kubernetes-sigs/aws-efs-csi-driver) — confirmed no EFS-specific metrics in source
- Deployed helmfile configs: `k8s-setup/aws-ebs-csi-driver/`, `aws-efs-csi-driver/`, `aws-mountpoint-s3-csi-driver/`
