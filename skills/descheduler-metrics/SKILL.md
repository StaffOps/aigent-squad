---
name: descheduler-metrics
description: "Use when diagnosing Kubernetes Descheduler health — eviction counts by strategy/namespace/result, loop duration, strategy execution time, and build info. Covers descheduler_pods_evicted_total, descheduler_loop_duration_seconds, descheduler_strategy_duration_seconds, descheduler_build_info, plus go_*/process_*. Grounded on Helm chart descheduler/descheduler 0.35.1 (appVersion v0.35.1)."
keywords: [descheduler]
---
# Kubernetes Descheduler Metrics

Prometheus metrics emitted by the **Kubernetes Descheduler** — the controller that
evicts pods to rebalance workloads based on configurable strategies (node affinity
violations, topology spread, pod lifetime, duplicate pods, etc.).

**Grounded on**: Helm chart `descheduler/descheduler` **0.35.1** (appVersion
**v0.35.1**), deployed via helmfile across all clusters (core-devops, dev, prd).

---

## Scrape Pipeline

```
Descheduler Deployment (2 replicas, leader-elected)
  └── :10258/metrics
        └── ServiceMonitor (serviceMonitor.enabled: true)
              └── vmagent scrape
                    └── VictoriaMetrics
```

**Deployment model** (this environment): the descheduler runs as a **Deployment**
with `replicas: 2` and `leaderElection: true`, cycling every `deschedulingInterval:
5m`. Because it is long-running (not a CronJob), scraping is continuous and reliable.

> If deployed as a **CronJob** (alternative mode), the pod is short-lived and
> vmagent may miss scrapes between runs. This environment uses Deployment mode —
> no short-lived scrape gap concern.

**Metrics endpoint**: `:10258/metrics` (k8s component-base default metrics handler).

---

## Metric Rename Transition (v0.34 → v0.35)

In v0.34.0, several metrics were deprecated and replaced. In v0.35.x **both old
and new names are emitted simultaneously**. The old names will be removed in a
future release. Prefer the **new** names in queries and alerts.

| Deprecated Name (still emitted) | New Canonical Name |
|-----|-----|
| `descheduler_pods_evicted` | `descheduler_pods_evicted_total` |
| `descheduler_descheduler_loop_duration_seconds` | `descheduler_loop_duration_seconds` |
| `descheduler_descheduler_strategy_duration_seconds` | `descheduler_strategy_duration_seconds` |

The double-prefix (`descheduler_descheduler_*`) was a naming bug (subsystem prefix
applied twice) fixed by the rename.

---

## 1. Eviction Metrics

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `descheduler_pods_evicted_total` | Counter | Total pods evicted (or attempted), by result/strategy/namespace/node | **Primary signal** — rate of successful vs failed evictions per strategy; sudden spike = aggressive policy; `result=error` = PDB/budget blocking evictions | `result`, `strategy`, `profile`, `namespace`, `node` |
| `descheduler_pods_evicted` | Counter (deprecated) | Same as above (deprecated in 0.34, still emitted in 0.35) | Use `_total` variant in new queries | `result`, `strategy`, `profile`, `namespace`, `node` |

### Label values

| Label | Values |
|---|---|
| `result` | `success` — pod evicted; `error` — eviction failed (e.g., PDB constraint) |
| `strategy` | Plugin name: `RemoveDuplicates`, `PodLifeTime`, `RemoveFailedPods`, `RemovePodsHavingTooManyRestarts`, `RemovePodsViolatingNodeAffinity`, `RemovePodsViolatingNodeTaints`, `RemovePodsViolatingInterPodAntiAffinity`, `RemovePodsViolatingTopologySpreadConstraint`, `LowNodeUtilization` (if enabled) |
| `profile` | Profile name from deschedulerPolicy (this env: `default`) |
| `namespace` | Namespace of evicted pod |
| `node` | Node from which pod was evicted |

---

## 2. Loop & Strategy Duration

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `descheduler_loop_duration_seconds` | Histogram | Time to complete one full descheduling cycle (all strategies) | Slow loops = too many pods to evaluate or API throttling; correlate with kube-apiserver latency | — |
| `descheduler_strategy_duration_seconds` | Histogram | Time to execute a single strategy within a loop | Identify which strategy is slow (e.g., `RemoveDuplicates` scanning many ReplicaSets) | `strategy`, `profile` |
| `descheduler_descheduler_loop_duration_seconds` | Histogram (deprecated) | Same as `descheduler_loop_duration_seconds` | Legacy name — migrate queries | — |
| `descheduler_descheduler_strategy_duration_seconds` | Histogram (deprecated) | Same as `descheduler_strategy_duration_seconds` | Legacy name — migrate queries | `strategy`, `profile` |

**Histogram buckets** (both):
`0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100` seconds
(loop also includes 250, 500).

---

## 3. Build Info

| Metric Name | Type | What It Measures | Troubleshooting Use | Labels |
|---|---|---|---|---|
| `descheduler_build_info` | Gauge (always 1) | Descheduler binary version metadata | Confirm deployed version; detect inconsistent versions across replicas after upgrade | `GoVersion`, `AppVersion`, `DeschedulerVersion`, `GitBranch`, `GitSha1` |

---

## 4. Go Runtime & Process Metrics

Standard `client_golang` collectors (see `go-apm-metrics` skill for full catalog):

| Metric Prefix | What It Covers |
|---|---|
| `go_goroutines` | Goroutine count — leak detection |
| `go_memstats_*` | Heap, GC, alloc stats |
| `go_gc_duration_seconds` | GC pause duration |
| `process_cpu_seconds_total` | CPU consumption of the descheduler process |
| `process_resident_memory_bytes` | RSS memory usage |
| `process_open_fds` | File descriptor usage |

---

## Troubleshooting Quick-Reference

| Symptom | First Metrics to Check | Likely Cause |
|---|---|---|
| No evictions happening | `rate(descheduler_pods_evicted_total[5m]) == 0` | All strategies finding nothing to evict (healthy state) OR leader election lost — check `descheduler_build_info` across replicas |
| High eviction error rate | `descheduler_pods_evicted_total{result="error"}` | PodDisruptionBudget blocking evictions; minAvailable/maxUnavailable too tight |
| Aggressive evictions disrupting services | `sum by (strategy)(rate(descheduler_pods_evicted_total{result="success"}[5m]))` | Strategy misconfiguration (e.g., `PodLifeTime.maxPodLifeTimeSeconds` too low, or `RemovePodsHavingTooManyRestarts.podRestartThreshold` too low) |
| Descheduling loop very slow | `histogram_quantile(0.99, rate(descheduler_loop_duration_seconds_bucket[5m]))` | Too many pods to evaluate; kube-apiserver throttling; check `strategy_duration` to isolate which plugin |
| One strategy dominating loop time | `histogram_quantile(0.99, rate(descheduler_strategy_duration_seconds_bucket[5m])) by (strategy)` | Strategy scanning too many resources (e.g., `RemoveDuplicates` with many ReplicaSets) |
| Descheduler OOMKilled | `process_resident_memory_bytes`, `go_memstats_heap_inuse_bytes` | Large cluster with thousands of pods; increase memory limit or reduce scope via namespace filters |
| Leader election issues (no active leader) | Both replicas show 0 eviction rate | Check `kube-system` lease objects; network partition between replicas |

---

## Useful Queries

```promql
# Eviction rate per strategy (last 5m)
sum by (strategy) (rate(descheduler_pods_evicted_total{result="success"}[5m]))

# Eviction error rate by namespace
sum by (namespace) (rate(descheduler_pods_evicted_total{result="error"}[5m]))

# p99 loop duration
histogram_quantile(0.99, sum(rate(descheduler_loop_duration_seconds_bucket[5m])) by (le))

# p99 per-strategy duration
histogram_quantile(0.99, sum by (strategy, le)(rate(descheduler_strategy_duration_seconds_bucket[5m])))

# Confirm version deployed
descheduler_build_info
```

---

## Deployed Configuration (this environment)

| Setting | Value |
|---|---|
| Mode | Deployment (long-running, NOT CronJob) |
| Replicas | 2 (leader-elected) |
| Interval | 5 minutes (`deschedulingInterval: 5m`) |
| Metrics port | 10258 |
| ServiceMonitor | Enabled |
| Profile | `default` |

**Active plugins (balance)**: `RemoveDuplicates`, `RemovePodsViolatingTopologySpreadConstraint`

**Active plugins (deschedule)**: `PodLifeTime` (14400s / 4h max for Pending/PodInitializing/Succeeded), `RemoveFailedPods`, `RemovePodsHavingTooManyRestarts` (threshold: 10), `RemovePodsViolatingNodeTaints`, `RemovePodsViolatingNodeAffinity`, `RemovePodsViolatingInterPodAntiAffinity`

---

## Complements

- `go-apm-metrics` — full Go runtime metrics catalog (`go_*`, `process_*`)
- `k8s-workload-metrics` — pod scheduling, eviction events from kube-state-metrics
- `argocd-metrics` — if rollouts trigger descheduler activity post-deploy

## Sources

- [Descheduler metrics source (v0.35.0)](https://github.com/kubernetes-sigs/descheduler/blob/v0.35.0/metrics/metrics.go) — authoritative metric definitions
- [Descheduler Helm chart](https://github.com/kubernetes-sigs/descheduler/tree/master/charts/descheduler) — chart 0.35.1
- Deployed helmfile: `k8s-setup/descheduler/helmfile.yaml.gotmpl` (version 0.35.1, all environments)
- Deployed values: `k8s-setup/descheduler/descheduler/values.yaml.gotmpl`
