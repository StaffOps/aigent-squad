---
name: argocd-metrics
description: "Use when diagnosing Argo CD health \u2014 application sync failures, reconciliation saturation, Git/Redis latency, cluster cache staleness, workqueue backlog, kubectl throttling. Covers argocd_app_*, argocd_cluster_*, argocd_git_*, argocd_redis_*, argocd_kubectl_*, argocd_appset_*, workqueue_*, grpc_server_*. Grounded on argo-cd helm chart v10.0.1 (ArgoCD ~v2.14+), official docs, and live TUNING-CHANGES.md observations."
keywords: [argocd, argo, redis, cd, tuning]
---
# Argo CD Self-Metrics — Prometheus Catalog

Metrics emitted by the Argo CD control plane components for diagnosing GitOps
pipeline health: sync performance, reconciliation saturation, Git/Redis backend
latency, and Kubernetes API request patterns.

**Grounded on**: `argo-cd` helm chart **v10.0.1** (ArgoCD ~v2.14+), deployed via
helmfile in `k8s-setup/argo/argo-cd`. Official metrics reference:
https://argo-cd.readthedocs.io/en/stable/operator-manual/metrics/

---

## Scrape Pipeline

```
┌──────────────────────────────┐
│ application-controller :8082 │──┐
├──────────────────────────────┤  │
│ api-server             :8083 │──┤
├──────────────────────────────┤  │  ServiceMonitor     vmagent        VictoriaMetrics
│ repo-server            :8084 │──├─────────────────► scrape ────────►  vminsert
├──────────────────────────────┤  │
│ applicationset-ctrl    :8080 │──┤
├──────────────────────────────┤  │
│ redis (redis_exporter) :9121 │──┘
└──────────────────────────────┘
```

**How metrics are enabled** (from deployed `values.yaml.gotmpl`):
- `controller.metrics.enabled: true` + `serviceMonitor.enabled: true`
- `server.metrics.enabled: true` + `serviceMonitor.enabled: true`
- `repoServer.metrics.enabled: true` + `serviceMonitor.enabled: true`
- `applicationSet.metrics.enabled: true` + `serviceMonitor.enabled: true`
- `controller.metrics.applicationLabels.enabled: true` with labels `[type, environment]`
- `redis.metrics.enabled: true` + `serviceMonitor.enabled: true`

**Deployment topology** (this environment):
- Controller: **3 replicas** (StatefulSet, round-robin sharding, `dynamicClusterDistribution: true`)
- API Server: HPA 3–10 replicas
- Repo Server: HPA 3–5 replicas
- ApplicationSet Controller: 3 replicas
- Managed: **478 apps**, 3 clusters (in-cluster + dev-nv + prd-nv)

---

## 1. Application Controller Metrics (`:8082/metrics`)

### Application State & Sync

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_app_info` | Gauge | Application state (always 1 per app) | Filter by `sync_status`, `health_status` to find unhealthy/out-of-sync apps. `absent()` = controller down. | `name`, `namespace`, `project`, `dest_server`, `sync_status`, `health_status`, `operation` |
| `argocd_app_sync_total` | Counter | Cumulative sync operations per app | Rate = sync frequency; high rate on one app = sync-loop (drift + autosync). | `name`, `namespace`, `project`, `dest_server`, `phase` |
| `argocd_app_sync_duration_seconds_total` | Counter | Cumulative sync duration | `rate() / rate(sync_total)` = average sync duration per operation. | `name`, `namespace`, `project`, `dest_server` |
| `argocd_app_condition` | Gauge | Application condition count | Non-zero = conditions present (OrphanedResourceWarning, etc.). | `name`, `namespace`, `project`, `condition` |
| `argocd_app_labels` | Gauge | App labels as Prometheus labels | Join with `argocd_app_info` to group by business labels (`type`, `environment`). | `name`, `namespace`, `project`, `label_type`, `label_environment` |
| `argocd_app_orphaned_resources_count` | Gauge | Orphaned resources per app | Non-zero = resources in target namespace not tracked by app. | `name`, `namespace`, `project` |

### Reconciliation Performance

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_app_reconcile_bucket` | Histogram | Reconciliation duration distribution | p99 > 10s = controller overloaded or cluster slow. | `namespace`, `dest_server`, `le` |
| `argocd_app_reconcile_count` | Counter | Total reconciliation count | Rate = reconciliation throughput per shard. | `namespace`, `dest_server` |
| `argocd_app_reconcile_sum` | Counter | Cumulative reconciliation time | `sum/count` = average reconciliation latency. | `namespace`, `dest_server` |

### Kubernetes API Requests (from controller)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_app_k8s_request_total` | Counter | K8s API calls during reconciliation | High rate = expensive reconciliation; filter by `response_code` 429/5xx for throttling. | `project`, `server`, `response_code`, `verb`, `resource_kind` |

### Cluster Cache & State

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_cluster_api_resource_objects` | Gauge | Objects in cluster cache | Large numbers (>10K) = heavy watch pressure, memory cost. | `server` |
| `argocd_cluster_api_resources` | Gauge | Monitored K8s API resource types | Unexpected increase = new CRDs being watched. | `server` |
| `argocd_cluster_cache_age_seconds` | Gauge | Age of cluster cache | High value (>300s) = stale cache, possible network issue to target cluster. | `server` |
| `argocd_cluster_connection_status` | Gauge | Cluster connection state | 0 = disconnected (check IRSA token, network). | `server` |
| `argocd_cluster_events_total` | Counter | Processed K8s resource events | High rate = event storm (ScaleOps, Kyverno — confirmed in this env at 780K). | `server`, `group`, `kind` |
| `argocd_cluster_info` | Gauge | Cluster metadata | Informational — `k8s_version`, `server`. | `server`, `k8s_version` |

### Resource Event Processing

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_resource_events_processing_bucket` | Histogram | Batch event processing time | Slow processing = controller spending too long per event batch. | `le` |
| `argocd_resource_events_processed_in_batch` | Gauge | Events processed per batch | Spikes correlate with event storms. | — |

### Redis (from controller)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_redis_request_total` | Counter | Redis requests from controller | Rate indicates cache dependency load. | `initiator`, `hostname`, `failed` |
| `argocd_redis_request_duration_bucket` | Histogram | Redis request latency | p99 > 50ms = Redis overloaded or network issue. | `initiator`, `hostname`, `failed`, `le` |

### kubectl Execution (sync operations)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_kubectl_exec_pending` | Gauge | Pending kubectl execs | Growing = `controller.kubectl.parallelism.limit` too low (set to 40 in this env). | — |
| `argocd_kubectl_exec_total` | Counter | Total kubectl executions | Rate by `command` shows apply/create/replace mix. | `command` |
| `argocd_kubectl_request_duration_seconds_bucket` | Histogram | kubectl HTTP request latency | Slow = target cluster API server under pressure. | `host`, `verb`, `le` |
| `argocd_kubectl_rate_limiter_duration_seconds_bucket` | Histogram | Time spent in client-go rate limiter | Non-zero = hitting K8s API rate limits (client-side QPS). | `host`, `verb`, `le` |
| `argocd_kubectl_requests_total` | Counter | kubectl requests by result code | 429s = API throttling; 5xx = target API errors. | `host`, `code`, `method`, `verb` |
| `argocd_kubectl_request_retries_total` | Counter | kubectl request retries | High retries = flaky connectivity to target cluster. | `host`, `code`, `method` |
| `argocd_kubectl_transport_cache_entries` | Gauge | Cached HTTP transports | Should be stable per-cluster. | — |

### Controller Workqueue (client-go)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `workqueue_depth` | Gauge | Items pending in workqueue | >0 sustained = processing falling behind (confirmed: 42 items during saturation event). | `name` (`reconciliation_queue`, `app_operation_processing_queue`) |
| `workqueue_adds_total` | Counter | Items added to queue | Rate = incoming work volume. | `name` |
| `workqueue_queue_duration_seconds_bucket` | Histogram | Time items spend waiting in queue | p99 > 30s = severe processing delay. | `name`, `le` |
| `workqueue_work_duration_seconds_bucket` | Histogram | Time spent processing each item | Long work duration = expensive reconciliation per app. | `name`, `le` |
| `workqueue_retries_total` | Counter | Items retried | High retries = failing operations being requeued. | `name` |
| `workqueue_unfinished_work_seconds` | Gauge | How long unfinished items have been in progress | Very high value = stuck processing (deadlock, slow API). | `name` |
| `workqueue_longest_running_processor_seconds` | Gauge | Longest-running single item | If growing = single app blocking the queue. | `name` |

---

## 2. API Server Metrics (`:8083/metrics`)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_login_request_total` | Counter | Login attempts | Spike = brute-force or SSO misconfiguration. | — |
| `grpc_server_handled_total` | Counter | Completed gRPC RPCs | Rate by `grpc_code` = error rate. Filter `grpc_code!="OK"` for failures. | `grpc_service`, `grpc_method`, `grpc_code` |
| `grpc_server_msg_sent_total` | Counter | gRPC stream messages sent | High volume on watch streams = many UI/CLI watchers. | `grpc_service`, `grpc_method` |
| `argocd_proxy_extension_request_total` | Counter | Proxy extension calls (e.g., Rollout UI) | Errors = extension backend unavailable. | `extension`, `method`, `status` |
| `argocd_proxy_extension_request_duration_seconds_bucket` | Histogram | Extension proxy latency | Slow = extension backend (rollout-extension) degraded. | `extension`, `method`, `le` |

---

## 3. Repo Server Metrics (`:8084/metrics`)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_git_request_total` | Counter | Git operations performed | Rate by `request_type` shows clone vs fetch vs ls-remote mix. | `repo`, `request_type` |
| `argocd_git_request_duration_seconds_bucket` | Histogram | Git operation latency | p99 > 30s = Git server slow, large repo, or network issue. | `repo`, `request_type`, `le` |
| `argocd_git_fetch_fail_total` | Counter | Failed Git fetches | Non-zero sustained = credential issue, repo unavailable, or rate-limited. | `repo` |
| `argocd_repo_pending_request_total` | Gauge | Requests waiting for repo lock | Growing = repo-server serialization bottleneck (parallelism.limit=50 in this env). | — |
| `argocd_redis_request_total` (repo-server) | Counter | Redis requests from repo-server | Cache lookups for manifests. | `initiator`, `failed` |
| `argocd_redis_request_duration_seconds_bucket` (repo-server) | Histogram | Redis latency from repo-server | — | `initiator`, `failed`, `le` |

---

## 4. ApplicationSet Controller Metrics (`:8080/metrics`)

| Metric Name | Type | What It Measures | Troubleshooting Use | Key Labels |
|---|---|---|---|---|
| `argocd_appset_info` | Gauge | ApplicationSet state | `Resource_update_status` reflects sync state of generated apps. | `name`, `namespace`, `Resource_update_status` |
| `argocd_appset_reconcile_bucket` | Histogram | AppSet reconciliation duration | Slow = generator (SCM, Git file glob) taking long. | `name`, `namespace`, `le` |
| `argocd_appset_reconcile_count` | Counter | AppSet reconciliation count | — | `name`, `namespace` |
| `argocd_appset_owned_applications` | Gauge | Apps owned by each AppSet | Sudden change = generator producing unexpected apps. | `name`, `namespace` |

---

## 5. Go Runtime & Process Metrics (all components)

All Argo CD components expose standard `go_*` and `process_*` metrics. See
**`skills/apm-metrics/go-apm-metrics`** for the full catalog. Key ones for Argo CD:

| Metric Name | Troubleshooting Use (Argo CD context) |
|---|---|
| `go_goroutines` | Controller goroutine leak (informers, watches per cluster). |
| `go_memstats_heap_inuse_bytes` | Memory growth — large cluster cache = heap bloat. |
| `process_resident_memory_bytes` | OOM risk — compare with container `limits.memory`. |
| `process_cpu_seconds_total` | CPU saturation — rate should track requests; confirmed 99% saturation at 2985m/3000m. |

---

## Troubleshooting Quick Reference

| Symptom | First Metrics to Check |
|---|---|
| Apps stuck OutOfSync | `argocd_app_info{sync_status!="Synced"}`, `argocd_app_sync_total` rate (sync-loop?), `argocd_git_fetch_fail_total` |
| Slow syncs / reconciliation | `argocd_app_reconcile_bucket` p99, `workqueue_depth{name="reconciliation_queue"}`, `argocd_kubectl_exec_pending` |
| Controller CPU saturated | `process_cpu_seconds_total` rate vs limit, `workqueue_depth`, `argocd_cluster_events_total` rate (event storm?) |
| Cluster disconnected | `argocd_cluster_connection_status` = 0, `argocd_kubectl_requests_total{code="401|403"}` (IRSA token expired?) |
| Git fetch failures | `argocd_git_fetch_fail_total` rate, `argocd_git_request_duration_seconds` p99, `argocd_repo_pending_request_total` |
| Redis latency | `argocd_redis_request_duration_bucket` p99, `argocd_redis_request_total{failed="true"}` |
| Sync-loop on specific app | `argocd_app_sync_total{name="X"}` rate >> 0.01/s, check resource.customizations.ignoreDifferences |
| ApplicationSet not generating apps | `argocd_appset_owned_applications` = 0, `argocd_appset_reconcile_bucket` p99 |
| API server errors | `grpc_server_handled_total{grpc_code!="OK"}` rate, `argocd_login_request_total` spike |
| kubectl rate-limited (target cluster) | `argocd_kubectl_rate_limiter_duration_seconds` p99 > 0, `argocd_kubectl_requests_total{code="429"}` |
| Event storm consuming controller | `argocd_cluster_events_total` rate (filter by `kind`), check `resource.exclusions` in argocd-cm |
| Workqueue stuck | `workqueue_longest_running_processor_seconds` growing, `workqueue_unfinished_work_seconds` |

---

## Existing Alert Rules (from `prometheus.rules.yaml`)

| Alert | Expression | Severity | What It Catches |
|---|---|---|---|
| `ArgoAppMissing` | `absent(argocd_app_info) == 1` for 15m | critical | Controller completely down — no app data reported. |
| `ArgoAppNotSynced` | `argocd_app_info{sync_status!="Synced"} == 1` for 12h | warning | Long-standing drift (12h without sync). |
| `ArgocdServiceNotSynced` | `argocd_app_info{sync_status!="Synced"} != 0` for 15m | warning | Any app out-of-sync for 15m. |
| `ArgocdServiceUnhealthy` | `argocd_app_info{health_status!="Healthy"} != 0` for 15m | warning | Any app degraded/progressing for 15m. |

---

## Key Tuning Parameters (this environment)

From `TUNING-CHANGES.md` and `values.yaml.gotmpl`:

| Parameter | Value | Metric Impact |
|---|---|---|
| `controller.status.processors` | 50 | Limits concurrent status-processing goroutines |
| `controller.operation.processors` | 25 | Limits concurrent sync-operation goroutines |
| `controller.kubectl.parallelism.limit` | 40 | Max concurrent kubectl execs (`argocd_kubectl_exec_pending` capped here) |
| `reposerver.parallelism.limit` | 50 | Max concurrent manifest generations (`argocd_repo_pending_request_total` capped) |
| `timeout.reconciliation` | 30s | Base reconciliation interval (high frequency → more `workqueue_adds_total`) |
| `timeout.reconciliation.jitter` | 15s | Randomized spread to avoid thundering herd |
| `timeout.hard.reconciliation` | 0s | Disabled — relies on webhooks for refresh |
| Controller replicas | 3 | Round-robin sharding across shards |

---

## Complements

- **`skills/infrastructure/argocd-patterns`** — ApplicationSets, sync-waves, multi-cluster config (operational, NOT metrics).
- **`skills/apm-metrics/go-apm-metrics`** — Full `go_*` runtime metrics catalog (applies to all Argo CD Go components).
- **`steering/k8s-best-practices.md`** — GitOps-only deployment rules, ArgoCD as sole PRD deployment mechanism.

---

## Sources

- [Argo CD Official Metrics Documentation](https://argo-cd.readthedocs.io/en/stable/operator-manual/metrics/) (covers stable/latest, applicable to v2.14+)
- Deployed chart: `argo/argo-cd` v10.0.1 (helmfile at `k8s-setup/argo/helmfile.yaml.gotmpl`)
- `k8s-setup/argo/argo-cd/TUNING-CHANGES.md` — real observed metric values (2026-07-02) confirming `workqueue_depth`, `cluster_api_resource_objects`, `cluster_events_total`, CPU saturation patterns.
- `k8s-setup/argo/argo-cd/prometheus.rules.yaml` — deployed alerting rules.
- `k8s-setup/argo/argo-cd/values.yaml.gotmpl` — metrics enablement, tuning parameters, topology.
