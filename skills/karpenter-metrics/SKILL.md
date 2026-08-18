---
name: karpenter-metrics
description: "Use when diagnosing Karpenter node provisioning, disruption decisions, scheduling latency, EC2 API health, spot interruption handling, or cluster utilization. Covers karpenter_nodes_*, karpenter_nodeclaims_*, karpenter_pods_*, karpenter_voluntary_disruption_*, karpenter_scheduler_*, karpenter_nodepools_*, karpenter_interruption_*, karpenter_cluster_state_*, karpenter_cloudprovider_*, aws_sdk_go_*, controller_runtime_*, workqueue_*. Grounded on Helm chart karpenter/karpenter v1.8.3 (public.ecr.aws/karpenter/karpenter), official docs v1.11 metrics reference."
keywords: [karpenter, ec2, node]
---
# Karpenter Metrics — Prometheus Catalog

Metrics emitted by **Karpenter v1.8.3** (Helm chart `karpenter/karpenter` from `public.ecr.aws/karpenter`).

**Pipeline**: Karpenter controller (`:8080/metrics`) → vmagent scrape (ServiceMonitor enabled in values) → VictoriaMetrics.

**Deployment**: 2 replicas, `kube-system` namespace, hostNetwork, on dedicated `critical-apps` node group (not Karpenter-managed nodes). ServiceMonitor enabled via `serviceMonitor.enabled: true`.

**Feature gates active**: `nodeRepair: true`, `spotToSpotConsolidation: true`.

> **Version note**: Karpenter v1.0+ uses the `karpenter_` prefix for all native metrics (renamed from legacy `karpenter_*` / unprefixed names in v0.32–v0.37). The `karpenter_disruption_*` prefix was renamed to `karpenter_voluntary_disruption_*` in v1.0. Some ALPHA metrics listed from v1.11 docs may not be present in v1.8.3 — these are marked.

---

## 1. Nodes Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_nodes_created_total` | Counter | Nodes created by Karpenter | Provisioning rate; spike = scaling event | `nodepool` |
| `karpenter_nodes_terminated_total` | Counter | Nodes terminated by Karpenter | Churn rate; high = aggressive disruption or instability | `nodepool` |
| `karpenter_nodes_drained_total` | Counter | Nodes drained during disruption | Disruption activity; compare with `terminated` | — |
| `karpenter_nodes_allocatable` | Gauge | Allocatable resources on Karpenter-managed nodes | Capacity planning; compare with pod requests | `nodepool`, `resource_type` |
| `karpenter_nodes_total_pod_requests` | Gauge | Total pod resource requests on managed nodes | Utilization denominator (requests/allocatable) | `nodepool`, `resource_type` |
| `karpenter_nodes_total_pod_limits` | Gauge | Total pod resource limits on managed nodes | Over-commit detection | `nodepool`, `resource_type` |
| `karpenter_nodes_total_daemon_requests` | Gauge | DaemonSet resource requests on managed nodes | Overhead from DaemonSets (impacts packing) | `nodepool`, `resource_type` |
| `karpenter_nodes_total_daemon_limits` | Gauge | DaemonSet resource limits | DaemonSet overhead visibility | `nodepool`, `resource_type` |
| `karpenter_nodes_system_overhead` | Gauge | System reserved resources (capacity − allocatable) | Understand kubelet/OS overhead per node | `nodepool`, `resource_type` |
| `karpenter_nodes_termination_duration_seconds` | Histogram | Time from delete request to finalizer removal | Slow terminations = drain issues or PDB blocks | — |
| `karpenter_nodes_lifetime_duration_seconds` | Histogram | Total node lifetime from creation to deletion | Short lifetimes = instability; long = no consolidation | — |
| `karpenter_nodes_current_lifetime_seconds` | Gauge | Current age of each managed node | Identify stale nodes beyond `expireAfter` | — |

---

## 2. NodeClaims Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_nodeclaims_created_total` | Counter | NodeClaims created | Provisioning requests; `reason` shows trigger | `nodepool`, `reason` |
| `karpenter_nodeclaims_terminated_total` | Counter | NodeClaims terminated | Lifecycle completion rate | `nodepool` |
| `karpenter_nodeclaims_disrupted_total` | Counter | NodeClaims disrupted by Karpenter | Disruption breakdown by reason | `nodepool`, `reason` |
| `karpenter_nodeclaims_termination_duration_seconds` | Histogram | Duration of NodeClaim termination | Slow termination = instance stuck or API delay | — |
| `karpenter_nodeclaims_instance_termination_duration_seconds` | Histogram | Duration of EC2 instance termination API call | AWS API latency for TerminateInstances | — |

**`reason` label values for `created_total`**: `provisioning` (new pods need capacity), `disruption` (replacement during consolidation/drift/expiry).

**`reason` label values for `disrupted_total`**: `consolidation`, `drift`, `expiry`, `emptiness`, `underutilized`.

---

## 3. Pods Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_pods_state` | Gauge | Current state of pods visible to Karpenter | Pod inventory by phase/readiness/nodepool/zone/arch/capacity_type | `name`, `namespace`, `owner`, `node`, `nodepool`, `zone`, `arch`, `capacity_type`, `instance_type`, `phase` |
| `karpenter_pods_startup_duration_seconds` | Histogram | Time from pod creation to Running | End-to-end scheduling+provisioning latency (SLI) | — |
| `karpenter_pods_bound_duration_seconds` | Histogram | Time from pod creation to Bound | Scheduling latency (excludes container startup) | — |
| `karpenter_pods_unstarted_time_seconds` | Gauge | Time since pod creation without Running | Stuck-pod detection in real time | — |
| `karpenter_pods_unbound_time_seconds` | Gauge | Time since pod creation without Bound | Scheduling stall detection | — |
| `karpenter_pods_scheduling_decision_duration_seconds` | Histogram | Time from Karpenter seeing pod to first scheduling attempt | Internal scheduler decision latency | — |
| `karpenter_pods_eviction_requests_total` | Counter | Pod eviction requests issued by Karpenter | Eviction activity during disruption; `code` label shows success/failures | `code` |
| `karpenter_pods_drained_total` | Counter | Pods drained during node termination | Disruption blast radius per event | `reason` |

---

## 4. Voluntary Disruption Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_voluntary_disruption_decisions_total` | Counter | Disruption decisions performed | Activity rate per reason/type; 0 = consolidation stalled | `decision`, `reason`, `consolidation_type` |
| `karpenter_voluntary_disruption_eligible_nodes` | Gauge | Nodes eligible for disruption right now | Consolidation opportunity; 0 = nothing to do or all blocked | `reason` |
| `karpenter_voluntary_disruption_decision_evaluation_duration_seconds` | Histogram | Duration of disruption evaluation cycle | Long evaluations = expensive simulation (many nodes) | `method`, `consolidation_type` |
| `karpenter_voluntary_disruption_queue_failures_total` | Counter | Failed enqueued disruption decisions | Repeated failures = PDB blocks or validation errors | `method` |
| `karpenter_voluntary_disruption_consolidation_timeouts_total` | Counter | Consolidation algorithm timeouts | Complex cluster topology causing simulation timeout | `consolidation_type` |
| `karpenter_voluntary_disruption_failed_validations_total` | Counter | Candidates that failed validation post-selection | Race conditions between selection and execution | `consolidation_type` |

**`decision` label values**: `disrupted` (action taken), `non-disrupted` (decided not to).
**`reason` label values**: `consolidation`, `drift`, `emptiness`, `expiry`, `underutilized`.
**`consolidation_type` label values**: `single`, `multi`.

---

## 5. Scheduler Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_scheduler_scheduling_duration_seconds` | Histogram | Duration of scheduling simulations | Slow scheduling = complex constraints or many pending pods | — |
| `karpenter_scheduler_queue_depth` | Gauge | Pods waiting in scheduler queue | Growing queue = Karpenter can't keep up | — |
| `karpenter_scheduler_unschedulable_pods_count` | Gauge | Count of unschedulable pods | Key trigger metric — non-zero means capacity needed | — |
| `karpenter_scheduler_unfinished_work_seconds` | Gauge | Unobserved scheduling work in progress | Large values = stuck scheduling loop | — |

---

## 6. NodePools Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_nodepools_usage` | Gauge | Resources provisioned per NodePool | Capacity tracking against limits | `nodepool`, `resource_type` |
| `karpenter_nodepools_limit` | Gauge | Resource limits configured per NodePool | Ceiling detection; usage ≈ limit = cannot scale more | `nodepool`, `resource_type` |
| `karpenter_nodepools_allowed_disruptions` | Gauge | Concurrent disruptions allowed right now | 0 = budget exhausted, no more consolidation possible | `nodepool` |
| `karpenter_nodepools_nodes_consuming_budgets` | Gauge | Nodes currently consuming disruption budget | Budget saturation visibility | `nodepool` |

---

## 7. Interruption Metrics (SQS)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_interruption_received_messages_total` | Counter | SQS messages received from interruption queue | Spot interruption / rebalance / scheduled-event activity | `message_type`, `actionable` |
| `karpenter_interruption_message_queue_duration_seconds` | Histogram | Time messages sit in SQS before processing | High latency = processing backlog or slow polling | — |
| `karpenter_interruption_deleted_messages_total` | Counter | Messages deleted after processing | Should match received (minus failures) | — |

**`message_type` label values**: `SpotInterruption`, `ScheduledChange`, `StateChange`, `RebalanceRecommendation`.

---

## 8. Cluster State Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_cluster_state_synced` | Gauge | 1 if cluster state is in sync with API server, 0 otherwise | **Critical health signal** — 0 = stale decisions | — |
| `karpenter_cluster_state_node_count` | Gauge | Total nodes in Karpenter's view | Cross-check with `kubectl get nodes` count | — |
| `karpenter_cluster_state_unsynced_time_seconds` | Gauge | Duration cluster state has been out of sync | Extended unsync = scheduling errors | — |

---

## 9. Cloud Provider Metrics (EC2 API)

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `karpenter_cloudprovider_duration_seconds` | Histogram | Duration of cloud provider API calls | EC2 API latency; high p99 = throttling or region issues | `controller`, `method`, `provider` |
| `karpenter_cloudprovider_errors_total` | Counter | Cloud provider errors | Non-zero = EC2 API failures (capacity, auth, limits) | `controller`, `method`, `provider` |
| `karpenter_cloudprovider_instance_type_offering_available` | Gauge | Instance type availability by zone/capacity_type | 0 = no capacity for that type (InsufficientInstanceCapacity) | `instance_type`, `capacity_type`, `zone` |
| `karpenter_cloudprovider_instance_type_offering_price_estimate` | Gauge | Estimated hourly price per offering | Cost optimization; Karpenter uses this for cheapest-first | `instance_type`, `capacity_type`, `zone` |
| `karpenter_cloudprovider_instance_type_cpu_cores` | Gauge | vCPUs per instance type | Reference for capacity calculations | `instance_type` |
| `karpenter_cloudprovider_instance_type_memory_bytes` | Gauge | Memory per instance type | Reference for capacity calculations | `instance_type` |
| `karpenter_cloudprovider_batcher_batch_size` | Histogram | Size of batched API requests | Large batches = efficient; small = frequent calls | `batcher` |
| `karpenter_cloudprovider_batcher_batch_time_seconds` | Histogram | Duration of batch window | Tuning batch efficiency vs latency | `batcher` |

---

## 10. AWS SDK Go Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `aws_sdk_go_request_total` | Counter | Total AWS SDK requests | API call volume | `service`, `operation` |
| `aws_sdk_go_request_duration_seconds` | Histogram | End-to-end AWS API request duration | Latency including retries | `service`, `operation` |
| `aws_sdk_go_request_attempt_total` | Counter | Total request attempts (includes retries) | High attempt/request ratio = throttling | `service`, `operation` |
| `aws_sdk_go_request_attempt_duration_seconds` | Histogram | Duration per individual attempt | Per-call latency (excludes retry wait) | `service`, `operation` |
| `aws_sdk_go_request_retry_count` | Histogram | Retry count per request | Non-zero = throttling or transient errors | `service`, `operation` |

---

## 11. Controller Runtime & Workqueue

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `controller_runtime_reconcile_total` | Counter | Total reconciliations per controller | Activity rate per reconciler | `controller`, `result` |
| `controller_runtime_reconcile_errors_total` | Counter | Failed reconciliations | Non-zero rate = controller bugs or API errors | `controller` |
| `controller_runtime_terminal_reconcile_errors_total` | Counter | Unrecoverable reconciliation errors | Requires manual intervention | `controller` |
| `controller_runtime_reconcile_time_seconds` | Histogram | Duration per reconciliation | Slow reconciles = complex logic or API latency | `controller` |
| `controller_runtime_active_workers` | Gauge | Currently busy workers per controller | Saturation; close to max_concurrent = bottleneck | `controller` |
| `controller_runtime_max_concurrent_reconciles` | Gauge | Max concurrent reconciles configured | Ceiling for active_workers | `controller` |
| `controller_runtime_reconcile_panics_total` | Counter | Panics during reconciliation | Any > 0 = code bug, investigate immediately | `controller` |
| `workqueue_depth` | Gauge | Items waiting in workqueue | Growing depth = processing can't keep up | `name` |
| `workqueue_adds_total` | Counter | Total items added to workqueue | Incoming work rate | `name` |
| `workqueue_queue_duration_seconds` | Histogram | Time items wait in queue before processing | Queuing latency; high = saturation | `name` |
| `workqueue_work_duration_seconds` | Histogram | Time to process a single item | Per-item processing cost | `name` |
| `workqueue_retries_total` | Counter | Retried items | High retries = transient failures | `name` |
| `workqueue_unfinished_work_seconds` | Gauge | Estimated in-progress work not yet observed | Stuck work detection | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Duration of longest-running item | Deadlock/hung processor detection | `name` |

---

## 12. Client Go & Leader Election

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `client_go_request_total` | Counter | K8s API requests by verb/code | API throttling detection (429s) | `method`, `code` |
| `client_go_request_duration_seconds` | Histogram | K8s API request latency | API server saturation impact | `verb`, `group`, `version`, `kind`, `subresource` |
| `leader_election_master_status` | Gauge | 1 = leader, 0 = standby | Confirm which replica is active | `name` |
| `leader_election_slowpath_total` | Counter | Slow leader lease renewals | Leader instability signal | `name` |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check | What to Look For |
|---------|------------------------|------------------|
| Pods stuck Pending (unschedulable) | `karpenter_scheduler_unschedulable_pods_count`, `karpenter_scheduler_queue_depth` | Non-zero unschedulable; check if nodes are being created |
| Nodes not being provisioned | `karpenter_nodeclaims_created_total`, `karpenter_cloudprovider_errors_total` | Zero creations + cloud errors = capacity or IAM issue |
| Slow pod scheduling | `karpenter_pods_startup_duration_seconds`, `karpenter_scheduler_scheduling_duration_seconds` | High p99 = complex NodePool constraints or EC2 launch delays |
| Excessive node churn | `karpenter_nodes_created_total` + `karpenter_nodes_terminated_total`, `karpenter_nodes_lifetime_duration_seconds` | Short lifetimes + high create/terminate = flapping |
| Consolidation not happening | `karpenter_voluntary_disruption_eligible_nodes`, `karpenter_nodepools_allowed_disruptions` | Eligible >0 but decisions =0 → PDB blocking; allowed=0 → budget exhausted |
| Consolidation stuck/timeout | `karpenter_voluntary_disruption_consolidation_timeouts_total` | Increasing = cluster too complex for simulation window |
| Spot interruptions | `karpenter_interruption_received_messages_total{message_type="SpotInterruption"}` | Frequency of spot reclaims; correlate with node terminations |
| EC2 API throttling | `aws_sdk_go_request_retry_count`, `karpenter_cloudprovider_duration_seconds` | High retry count + elevated p99 duration |
| InsufficientCapacity | `karpenter_cloudprovider_instance_type_offering_available` | 0 for desired types/zones = no EC2 capacity |
| Cluster state desync | `karpenter_cluster_state_synced`, `karpenter_cluster_state_unsynced_time_seconds` | synced=0 for extended time = scheduling on stale data |
| Controller saturation | `controller_runtime_active_workers` vs `max_concurrent_reconciles`, `workqueue_depth` | Workers at max + growing queue = scale replicas or tune concurrency |
| Leader failover issues | `leader_election_master_status`, `leader_election_slowpath_total` | Both replicas show 0 = no leader = no provisioning |
| Disruption budget exhausted | `karpenter_nodepools_allowed_disruptions`, `karpenter_nodepools_nodes_consuming_budgets` | allowed=0 + consuming>0 = budget fully consumed |
| NodePool at capacity limit | `karpenter_nodepools_usage` vs `karpenter_nodepools_limit` | usage ≈ limit = cannot provision more |

---

## Key PromQL / MetricsQL Queries

```promql
# Provisioning rate (nodes/min)
rate(karpenter_nodes_created_total[5m]) * 60

# Termination rate (nodes/min)
rate(karpenter_nodes_terminated_total[5m]) * 60

# Pod scheduling latency p99
histogram_quantile(0.99, rate(karpenter_pods_startup_duration_seconds_bucket[10m]))

# Disruption decisions rate by reason
rate(karpenter_voluntary_disruption_decisions_total{decision="disrupted"}[5m])

# EC2 API error rate
rate(karpenter_cloudprovider_errors_total[5m])

# Spot interruption frequency
rate(karpenter_interruption_received_messages_total{message_type="SpotInterruption"}[1h]) * 3600

# NodePool utilization %
karpenter_nodepools_usage / karpenter_nodepools_limit * 100

# Cluster utilization (from built-in metric, if present in v1.8)
karpenter_cluster_utilization_percent

# Controller reconcile error rate
rate(controller_runtime_reconcile_errors_total[5m])

# Workqueue saturation
workqueue_depth{name=~"karpenter.*"} > 0
```

---

## Complements

- `skills/infrastructure/karpenter-consolidation` — operational patterns: why consolidation blocks, PDB issues, spotToSpot thresholds, instance type broadening. This skill provides the **metrics** to diagnose those issues.
- `skills/apm-metrics/go-apm-metrics` — Go runtime metrics (`go_goroutines`, `go_memstats_*`, `go_gc_*`) also emitted by the Karpenter controller binary.
- `skills/apm-metrics/k8s-workload-metrics` — container-level resource metrics for the Karpenter pods themselves.
- `steering/k8s-best-practices.md` — scheduling constraints and anti-affinity that can cause `maxReplicas` to be ineffective (same principle applies to Karpenter-provisioned nodes with topology constraints).

---

## Sources

- **Deployed chart**: `public.ecr.aws/karpenter/karpenter` v1.8.3 via helmfile at `k8s-setup/karpenter/karpenter/`
- **Official metrics reference**: https://karpenter.sh/v1.11/reference/metrics/ (stable metrics unchanged since v1.0; ALPHA metrics marked where possibly absent in v1.8.3)
- **Metrics port**: `:8080/metrics` (default `METRICS_PORT`)
- **ServiceMonitor**: enabled in Helm values (`serviceMonitor.enabled: true`)
- **Scrape target**: `karpenter.kube-system.svc.cluster.local:8080`
