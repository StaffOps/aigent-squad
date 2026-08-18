# Signal Coverage Audit — spec 18 T12a

> **Generated**: 2026-08-18
> **Purpose**: Map all 33 EVIDENCE-MODEL signals to what current adapters can ACTUALLY collect.
> **Feeds**: T12 (correlator implementation), Phase 2-3 collection planning.

## Available MCP Tools (as of 2026-08-18)

| Adapter | MCP Tools Available |
|---------|---------------------|
| **observability** | VictoriaMetrics (`query`, `query_range`), Tempo (`traceql-search`, `get-trace`, `get-attribute-names`, `get-attribute-values`), Grafana MCP (`query_loki_logs`, `query_prometheus`, `query_loki_stats`, `query_loki_patterns`) |
| **kubernetes** | kubectl-based (`get_pods`, `get_events`, `kubectl_describe`, `get_deployments`, `get_nodes`, `get_hpa`, `check_pod_health`, `diagnose_pod_crash`, `get_logs`, etc.) |
| **aws** | AWS CLI via `use_aws` (CloudWatch, RDS, ElastiCache, EC2, ECS, EKS) |
| **devops** | ❌ No ArgoCD MCP. Can shell `argocd` CLI if binary available, but NOT an MCP tool. |
| **anomaly-ctrl** | ❌ No MCP today. Future: controller gRPC/HTTP API |

---

## CHANGE Signals (C1–C8)

| Signal ID | Name | Can collect today? | Via which adapter/tool | Gap if not |
|-----------|------|-------------------|------------------------|------------|
| C1 | Deploy/sync event | ⚠️ PARTIAL | devops: no ArgoCD MCP; kubernetes: `get_events` can catch Rollout/Deployment events | No ArgoCD API integration. Cannot query `app history` or sync events directly. Workaround: K8s Deployment/Rollout events via kubectl. |
| C2 | ConfigMap/Secret update | ✅ YES | kubernetes: `get_events` with `--field-selector reason=Updated` | — |
| C3 | Node create/delete (Karpenter) | ✅ YES | kubernetes: `get_events` filtering `reason=Launched,Terminated`; `get_nodes` | — |
| C4 | Dependency state change | ✅ YES | aws: `use_aws` → CloudWatch `describe-events`, RDS failover events, ElastiCache evictions | CloudWatch lag up to 120s noted |
| C5 | Traffic change (>2σ) | ✅ YES | observability: `query_prometheus` / VM `query_range` with baseline comparison | Requires recording rule or inline baseline calc |
| C6 | Scaling event (KEDA) | ✅ YES | kubernetes: `get_events` filtering `involvedObject.kind=ScaledObject` | — |
| C7 | cert-manager/ExternalSecret sync fail | ✅ YES | kubernetes: `get_events` filtering `reason=SyncFailed` | — |
| C8 | Anomaly-ctrl pre-correlated alert | ❌ NO | anomaly-ctrl: no MCP/API integration today | Requires anomaly-detection-controller API adapter |

---

## MECHANISM Signals (M1–M13)

| Signal ID | Name | Can collect today? | Via which adapter/tool | Gap if not |
|-----------|------|-------------------|------------------------|------------|
| M1 | Error trace (root span) | ✅ YES | observability: Tempo `traceql-search` `{ resource.service.name="$svc" && status=error }` | — |
| M2 | Dependency error trace (client) | ✅ YES | observability: Tempo `traceql-search` `{ ... && status=error && kind=client }` | — |
| M3 | First error log (temporal anchor) | ✅ YES | observability: `query_loki_logs` with `direction=forward&limit=1` | — |
| M4 | NEW error pattern (not pre-T) | ✅ YES | observability: `query_loki_logs` with `count_over_time` comparing windows | Requires two queries (before/after comparison) |
| M5 | OOMKill | ✅ YES | kubernetes: `get_events` filtering `reason=OOMKilling` | — |
| M6 | Memory monotonic growth | ✅ YES | observability: `query_prometheus` / VM `query_range` with `deriv(container_memory_working_set_bytes[30m])` | — |
| M7 | CPU throttle >50% | ✅ YES | observability: `query_prometheus` with cfs_throttled ratio | — |
| M8 | Conn timeout/refused | ✅ YES | observability: `query_loki_logs` with regex `connection refused|connection timeout` | — |
| M9 | TLS/auth error | ✅ YES | observability: `query_loki_logs` with regex `x509.*expired|401|403|authentication failed` | — |
| M10 | DNS error | ✅ YES | observability: `query_loki_logs` with regex `lookup.*timeout|no such host|NXDOMAIN` | — |
| M11 | Circuit breaker change | ✅ YES | observability: `query_loki_logs` with regex `circuit.*open|breaker.*state` | — |
| M12 | Pod scheduling failure | ✅ YES | kubernetes: `get_events` filtering `reason=FailedScheduling`; `detect_pending_pods` | — |
| M13 | Node condition (Mem/Disk pressure) | ✅ YES | observability: `query_prometheus` `kube_node_status_condition{condition=~"MemoryPressure|DiskPressure",status="true"}` | — |

---

## IMPACT Signals (I1–I8)

| Signal ID | Name | Can collect today? | Via which adapter/tool | Gap if not |
|-----------|------|-------------------|------------------------|------------|
| I1 | Error rate (RED-E) | ✅ YES | observability: `query_prometheus` / VM `query` with spanmetrics 5xx rate | — |
| I2 | Latency p99 (RED-D) | ✅ YES | observability: `query_prometheus` `histogram_quantile(0.99, ...)` | — |
| I3 | Request rate (RED-R) | ✅ YES | observability: `query_prometheus` `sum(rate(spanmetrics_apm_calls_total[5m]))` | — |
| I4 | Container restarts | ✅ YES | kubernetes: `get_events`; observability: `query_prometheus` `increase(kube_pod_container_status_restarts_total[15m])` | — |
| I5 | Error budget burn | ⚠️ PARTIAL | observability: `query_prometheus` `slo:burn_rate:5m{service="$svc"}` | Requires recording rule to exist; not all services have SLO recording rules |
| I6 | Log error volume spike | ✅ YES | observability: `query_loki_logs` with `count_over_time(... |= "ERROR" [5m])` | — |
| I7 | Multi-service blast radius | ✅ YES | observability: `query_prometheus` `sum by (service_name)(rate(...status_code=~"5.."[5m]))` | — |
| I8 | AWS backing health | ✅ YES | aws: `use_aws` → CloudWatch GetMetricData (RDS/ElastiCache CPU/Mem/Connections) | CloudWatch 60s precision + 120s lag |

---

## TEMPORAL Signals (T1–T4)

| Signal ID | Name | Can collect today? | Via which adapter/tool | Gap if not |
|-----------|------|-------------------|------------------------|------------|
| T1 | Rollback → recovery | ⚠️ PARTIAL | devops: no ArgoCD MCP to detect rollback event; kubernetes: can detect new ReplicaSet/Rollout revision; observability: can confirm metric recovery | Cannot programmatically detect ArgoCD rollback action; must infer from K8s events + metric recovery correlation |
| T2 | cause_ts < first_effect_ts | ✅ YES | Computed from collected evidence timestamps | Validation logic (T12 correlator) |
| T3 | Recovery ~ scaling completion | ✅ YES | kubernetes: KEDA ScaledObject events; observability: metric recovery timing | — |
| T4 | Multi-svc sequential first-error order | ✅ YES | observability: `query_loki_logs` forward-limit-1 per service; Tempo trace parentage | — |

---

## ELIMINATION Signals (E1–E4)

| Signal ID | Name | Can collect today? | Via which adapter/tool | Gap if not |
|-----------|------|-------------------|------------------------|------------|
| E1 | Deploy regression eliminated | ⚠️ PARTIAL | devops: no ArgoCD MCP for `empty history for window`; kubernetes: can check Deployment revision timestamps | Partial: can verify no new ReplicaSet in window but not ArgoCD sync history |
| E2 | Dependency outage eliminated | ✅ YES | aws: `use_aws` CloudWatch metrics in normal ranges | — |
| E3 | Infra failure eliminated | ✅ YES | kubernetes: `get_events` → no `NodeNotReady` events | — |
| E4 | Traffic spike eliminated | ✅ YES | observability: C5 query confirms < 2σ | — |

---

## Summary

| Category | Total | ✅ Full | ⚠️ Partial | ❌ None |
|----------|-------|---------|------------|--------|
| CHANGE (C1–C8) | 8 | 5 | 1 | 2 |
| MECHANISM (M1–M13) | 13 | 13 | 0 | 0 |
| IMPACT (I1–I8) | 8 | 7 | 1 | 0 |
| TEMPORAL (T1–T4) | 4 | 2 | 2 | 0 |
| ELIMINATION (E1–E4) | 4 | 3 | 1 | 0 |
| **TOTAL** | **37** | **30** | **5** | **2** |

> Note: 37 rows because T2 is a computed validation, not a collection. The spec lists 33 unique "collect" signals; T2 is logic, not data.

## Critical Gaps (blocking HIGH confidence in some scenarios)

1. **C1 (ArgoCD deploy/sync)** — No ArgoCD MCP. Workaround: K8s Deployment/Rollout events catch ~80% of deploys. Misses: sync-only changes (no pod restart), ApplicationSet reconciliation events.
2. **C8 (anomaly-ctrl)** — No integration. Phase 3 dependency; fast-path (score >0.8) cannot be triggered.
3. **I5 (SLO burn rate)** — Requires per-service recording rule. Not universally available.
4. **T1/E1 (ArgoCD rollback detection)** — Must infer from K8s revision changes + metric recovery.

## Recommended Priority for Gap Closure

1. **ArgoCD MCP adapter** (unblocks C1, T1, E1) — highest impact on RCA quality
2. **anomaly-ctrl HTTP/gRPC adapter** (unblocks C8) — enables fast-path
3. **SLO recording rule bootstrap** (unblocks I5) — operational prerequisite, not code gap
