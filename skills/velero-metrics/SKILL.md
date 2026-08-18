---
name: velero-metrics
description: "Use when diagnosing Velero backup/restore health \u2014 backup failures, partial failures, schedule staleness, restore errors, volume snapshot issues, CSI snapshot failures, backup deletion tracking, and backup duration anomalies. Covers velero_backup_*, velero_restore_*, velero_volume_snapshot_*, velero_csi_snapshot_*, velero_backup_deletion_*, plus go_*. Grounded on Helm chart vmware-tanzu/velero 12.0.2 (appVersion v1.15.x)."
keywords: [velero, csi]
---
# Velero Backup/Restore Metrics

Prometheus metrics for the **Velero** Kubernetes backup and restore system.

**Question answered**: "Are backups succeeding? Are restores working? Are volume snapshots being taken?"

**Scope**: Velero server self-telemetry exposed at `:8085/metrics`, scraped via
ServiceMonitor into VictoriaMetrics by vmagent.

---

## Scrape Pipeline

```
Velero server pod (:8085/metrics) → ServiceMonitor → vmagent → VictoriaMetrics
Velero node-agent pods            → PodMonitor     → vmagent → VictoriaMetrics
```

### How metrics are enabled (deployed config)

From deployed `values.yaml.gotmpl` (Helm chart `vmware-tanzu/velero` **v12.0.2**):

```yaml
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
  nodeAgentPodMonitor:
    enabled: true
  prometheusRule:
    enabled: true
```

Metrics are **ENABLED** and actively scraped. PrometheusRules are deployed with
two alerts (`VeleroBackupPartialFailures`, `VeleroBackupFailures`).

---

## Backup Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `velero_backup_attempt_total` | Counter | Total backup attempts (all outcomes) | Baseline: rate of backup operations | `schedule` |
| `velero_backup_success_total` | Counter | Successful backup completions | Success rate = success/attempt | `schedule` |
| `velero_backup_failure_total` | Counter | Completely failed backups | **KEY — non-zero rate = data protection gap** | `schedule` |
| `velero_backup_partial_failure_total` | Counter | Backups with some items/volumes failed | Partial failures may still be restorable but incomplete | `schedule` |
| `velero_backup_validation_failure_total` | Counter | Backups that failed pre-flight validation | Bad spec, missing BSL, invalid configuration | `schedule` |
| `velero_backup_duration_seconds_bucket` | Histogram | Time from backup start to completion | Detect slowdowns (storage issues, large PVs); p99 trending up = problem | `schedule`, `le` |
| `velero_backup_tarball_size_bytes` | Gauge | Size of the backup tarball (resource data) | Growth tracking; sudden jump = unexpected resource explosion | `schedule` |
| `velero_backup_last_status` | Gauge | Numeric status of the last backup per schedule (1=New, 2=InProgress, 3=Uploading, 4=Completed, 6=Failed, 7=PartiallyFailed, etc.) | **KEY — quick health check per schedule without computing rates** | `schedule` |
| `velero_backup_last_successful_timestamp` | Gauge | Unix timestamp of the last successful backup per schedule | **KEY — staleness detection**: `time() - velero_backup_last_successful_timestamp > 86400` = no successful backup in 24h | `schedule` |

### Deployed Alert Rules (from `prometheus.rules.yaml`)

```yaml
- alert: VeleroBackupPartialFailures
  expr: velero_backup_partial_failure_total{schedule!=""} / velero_backup_attempt_total{schedule!=""} > 0.25
  for: 15m
  labels:
    severity: warning

- alert: VeleroBackupFailures
  expr: velero_backup_failure_total{schedule!=""} / velero_backup_attempt_total{schedule!=""} > 0.25
  for: 15m
  labels:
    severity: warning
```

---

## Restore Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `velero_restore_attempt_total` | Counter | Total restore attempts | Baseline restore activity | `schedule` |
| `velero_restore_success_total` | Counter | Successful restores | Success rate monitoring | `schedule` |
| `velero_restore_failed_total` | Counter | Completely failed restores | **Critical in DR scenarios** — non-zero = restore capability broken | `schedule` |
| `velero_restore_partial_failure_total` | Counter | Restores with some items failed | May indicate incompatible resources or missing CRDs | `schedule` |
| `velero_restore_validation_failed_total` | Counter | Restores that failed pre-flight validation | Invalid restore spec or missing backup | `schedule` |

---

## Volume Snapshot Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `velero_volume_snapshot_attempt_total` | Counter | Volume snapshot attempts (native cloud provider) | Baseline for PV backup operations | `schedule` |
| `velero_volume_snapshot_success_total` | Counter | Successful volume snapshots | Success rate for PV protection | `schedule` |
| `velero_volume_snapshot_failure_total` | Counter | Failed volume snapshots | **Critical** — failed snapshots = PV data NOT protected | `schedule` |

---

## CSI Snapshot Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `velero_csi_snapshot_attempt_total` | Counter | CSI VolumeSnapshot creation attempts | Baseline for CSI-based PV backup | `schedule` |
| `velero_csi_snapshot_success_total` | Counter | Successful CSI snapshots | CSI snapshot success rate | `schedule` |
| `velero_csi_snapshot_failure_total` | Counter | Failed CSI snapshots | CSI driver issues, VolumeSnapshotClass misconfiguration | `schedule` |

---

## Backup Deletion Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `velero_backup_deletion_attempt_total` | Counter | Backup deletion attempts (TTL expiry or manual) | Lifecycle management activity | `schedule` |
| `velero_backup_deletion_success_total` | Counter | Successful backup deletions | Confirming TTL cleanup working | `schedule` |
| `velero_backup_deletion_failure_total` | Counter | Failed backup deletions | Storage permission issues, orphaned data in S3 | `schedule` |

---

## Go Runtime Metrics

Standard `client_golang` runtime metrics (`go_goroutines`, `go_memstats_*`, `go_gc_*`, `process_*`).
See **go-apm-metrics** skill for full reference.

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | Likely Cause |
|---------|------------------------|--------------|
| No backups completing | `velero_backup_attempt_total` (rate=0?) | Schedule disabled, Velero pod not running, BSL unavailable |
| Backup failures | `velero_backup_failure_total` rate, `velero_backup_last_status` | S3 permissions (IRSA), BSL connectivity, large resource set timeout |
| Stale backups (>24h) | `time() - velero_backup_last_successful_timestamp` | Schedule cron not firing, pod crash loop, node-agent issues |
| Partial failures | `velero_backup_partial_failure_total` | Individual resource errors (CRDs, large secrets, PV hooks failing) |
| Slow backups | `velero_backup_duration_seconds_bucket` (p99 rising) | Large PVs, S3 throttling, node-agent filesystem backup slow |
| Volume snapshots failing | `velero_volume_snapshot_failure_total` | EBS API errors, missing IAM permissions, AZ mismatch |
| CSI snapshots failing | `velero_csi_snapshot_failure_total` | VolumeSnapshotClass missing, CSI driver not ready, storage class mismatch |
| Backup deletions failing | `velero_backup_deletion_failure_total` | S3 bucket policy blocking deletes, orphaned objects |
| Restore failures | `velero_restore_failed_total` | Missing CRDs in target cluster, incompatible API versions, PV already exists |

### Key Queries

```promql
# Backup success rate per schedule (last 24h)
sum by (schedule) (increase(velero_backup_success_total[24h]))
/
sum by (schedule) (increase(velero_backup_attempt_total[24h]))

# Schedules with no successful backup in 24h (staleness)
velero_backup_last_successful_timestamp < (time() - 86400)

# Last backup status (non-success = investigate)
velero_backup_last_status != 4

# Volume snapshot failure rate
sum(rate(velero_volume_snapshot_failure_total[1h]))
/
sum(rate(velero_volume_snapshot_attempt_total[1h]))
```

---

## Deployed Configuration Context

| Setting | Value |
|---------|-------|
| Helm chart | `vmware-tanzu/velero` v12.0.2 |
| App version | Velero v1.15.x |
| Plugin | `velero/velero-plugin-for-aws:v1.14.1` |
| Uploader type | `kopia` (filesystem backup) |
| CSI feature | Enabled (`features: EnableCSI`) |
| Default FS backup | Enabled (`defaultVolumesToFsBackup: true`) |
| Snapshot move data | Enabled (`defaultSnapshotMoveData: true`) |
| Backup TTL | 720h (30 days) |
| Node-agent | Deployed (DaemonSet, privileged) |
| Metrics endpoint | `:8085/metrics` |
| ServiceMonitor | ✅ Enabled |
| PodMonitor (node-agent) | ✅ Enabled |
| PrometheusRule | ✅ Enabled (2 alerts) |

---

## Complements

- **go-apm-metrics** — Go runtime metrics (`go_goroutines`, `go_gc_*`, `go_memstats_*`) for Velero server process health
- **k8s-workload-metrics** — Pod-level resource usage (CPU/memory) for Velero server and node-agent
- **aws-csi-driver-metrics** — EBS CSI driver health (relevant when CSI snapshots involve EBS)

---

## Sources

- Deployed config: `02-KUBE/00-CONFIG/k8s-setup/velero/velero/values.yaml.gotmpl` (chart v12.0.2)
- Deployed PrometheusRules: `02-KUBE/00-CONFIG/k8s-setup/velero/velero/prometheus.rules.yaml`
- Grafana Cloud Velero integration reference (Velero 1.13+): https://grafana.com/docs/grafana-cloud/monitor-infrastructure/integrations/integration-reference/integration-velero/
- Google Cloud Managed Prometheus Velero exporter: https://docs.cloud.google.com/stackdriver/docs/managed-prometheus/exporters/velero
- Velero source `pkg/metrics/metrics.go` (release-1.15): https://github.com/vmware-tanzu/velero/blob/release-1.15/pkg/metrics/metrics.go
