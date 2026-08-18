---
name: victoriametrics-tuning
description: "VictoriaMetrics cluster performance tuning. Use when adjusting vminsert/vmselect/vmstorage flags, diagnosing slow inserts, goroutine scheduling latency, RPC saturation, or cache warm-up issues."
keywords: [victoriametrics-tuning, victoriametrics, tuning, "victoriametrics tuning", vminsert, vmselect, vmstorage, rpc]
---
# VictoriaMetrics Cluster — Performance Tuning

Operational tuning guide based on real cluster metrics and documented behavior.

## CRITICAL: Flags are per-component

Each binary has its own flag set. ALWAYS verify in the component-specific docs:
- `vmstorage_common_flags.md`
- `vminsert_common_flags.md`
- `vmselect_common_flags.md`

Examples of flags that DON'T exist everywhere:
- `-sortLabels` → vminsert and single-node ONLY (not vmstorage, not vmselect)
- `-search.*` in vmstorage refers to internal queries FROM vmselect, not user queries
- `-remoteWrite.*` → vmagent only

## fs.maxConcurrency

Controls max concurrent goroutines for filesystem operations.

**Default**: `min(16 × cgroup.AvailableCPUs(), 256)`

**Trade-off**:
- Higher → more parallel IO → better ingestion on high-latency storage (NFS, Ceph)
- Lower → less goroutine contention → better scheduling latency

**Decision framework**:
| Scenario | Recommended value |
|----------|------------------|
| `go_sched_latencies p99 > 100ms` AND CPU < 50% | Reduce (half of default) |
| `vm_slow_row_inserts > 0` AND disk is fast (NVMe/gp3) | Don't reduce — problem is RAM, not IO |
| High-latency storage | Increase beyond default |

**Metrics to monitor**:
- `histogram_quantile(0.99, sum(rate(go_sched_latencies_seconds_bucket[5m])) by (le, instance))`
- `rate(vm_slow_row_inserts_total[5m])`

## Slow Row Inserts

The #1 indicator of insufficient RAM for the active series working set.

**What happens**: vmstorage needs to look up series metadata (TSID) in the index. If the index cache doesn't have it (evicted due to RAM pressure), it goes to disk → slow insert.

**From the docs**:
> "If VictoriaMetrics works slowly and eats more than a CPU core per 100K ingested data points per second, then it is likely you have too many active time series for the current amount of RAM."

**Recommended RAM usage**: ≤ 50% of `vm_available_memory_bytes`. Above 50% → cache evictions → slow inserts.

**Fix (in order)**:
1. Increase RAM (direct fix)
2. Reduce active series cardinality (less working set)
3. Add more vmstorage replicas (distribute working set)

**NOT fixed by**: adjusting `fs.maxConcurrency`, merge concurrency, or any other flag.

## Merge Concurrency

| Flag | Component | Purpose |
|------|-----------|--------|
| `bigMergeConcurrency` | vmstorage | Parallel big merges (background compaction) |
| `smallMergeConcurrency` | vmstorage | Parallel small merges (recent data) |

**Trade-off**: Higher → faster compaction → less parts → faster lookups, but more CPU during merges.

**After restart**: merges are critical for warm-up. Reducing merge concurrency slows down convergence to stable state.

## Warm-up After Restart

Vmstorage caches are populated passively (no preload mechanism exists).

**Timeline** (for ~4M active series per pod, 8-12 GB RAM):
- 0-15min: RAM at ~35%, slow inserts spike (10-15x normal)
- 15-60min: RAM climbing, slow inserts decreasing
- 1-3h: RAM reaches stable ~55%, slow inserts at baseline

**Impact during warm-up**:
- RPC saturation vminsert→vmstorage increases (vmstorage slower to accept)
- Goroutine scheduling latency spikes (blocked on slow IO)
- Recording rules may miss iterations (vmselect queries slow)

**What helps**: nothing accelerates cache warming. Avoid making additional changes during warm-up that cause more restarts.

## RPC Saturation (vminsert → vmstorage)

**Metric**: `rate(vm_rpc_send_duration_seconds_total[5m])` — labeled by `addr` (target vmstorage)

**Interpretation**:
- < 0.5: healthy
- 0.5-0.9: getting warm
- > 0.9: saturated — vminsert blocked waiting for vmstorage

**Find the bottleneck vmstorage**:
```promql
rate(vm_rpc_send_duration_seconds_total{job=~".*vminsert.*"}[5m])
# Group by addr label to see which vmstorage is slow
```

**Causes**: slow vmstorage (RAM/cache issues), merge activity, or disk IO.

## maxConcurrentInserts (vminsert)

**What it does**: limits parallel goroutines handling insert requests.

**Sizing**: set to 1.5-2x the observed `max(vm_concurrent_insert_current)`. Too low = inserts queue; too high = wasted memory.

**Monitor**: if `vm_concurrent_insert_current` consistently equals the configured value, increase it.

## search.maxConcurrentRequests (vmselect)

**What it does**: limits parallel queries vmselect processes.

**Sizing**: proportional to available CPU. Each query can use ~1 core. With 2 CPU → 4-8 is reasonable. With 8 CPU → 16-32.

**Monitor**: `increase(vm_concurrent_select_limit_timeout_total[5m]) > 0` means queries are being rejected.

## Rightsizing — NEVER trust instantaneous metrics

ALWAYS use historical data (7d minimum, 30d preferred) for resource decisions:
```promql
# p99 over 7 days
quantile_over_time(0.99, max(rate(container_cpu_usage_seconds_total{...}[5m]))[7d:5m])
quantile_over_time(0.99, max(container_memory_working_set_bytes{...})[7d:5m])

# Max absolute over 7 days
max_over_time(max(container_memory_working_set_bytes{...})[7d:5m])
```

Instantaneous metrics during warm-up or low-traffic periods give false sense of over-provisioning.

## Reference

- Local docs: `01-DEVOPS/EXTERNAL-DOCS/VictoriaMetrics/docs/victoriametrics/`
- Per-component flags: `*_common_flags.md`
- Source: `lib/fs/fsutil/concurrency.go` (fs.maxConcurrency default)
- Troubleshooting: https://docs.victoriametrics.com/victoriametrics/troubleshooting/
