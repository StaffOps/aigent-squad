---
name: oomkill-investigation
description: How to investigate OOMKilled pods in Kubernetes
keywords: [oomkill, oomkilled, oom, "out of memory", "memory limit", evicted, "137"]
---

# Investigating OOMKilled Pods

When a pod is `OOMKilled`, the kernel terminated a container for exceeding its
memory limit (or node memory pressure). Exit code is `137` (128 + SIGKILL 9).

## Triage order

1. **Confirm the kill reason** — look for `OOMKilled` in the pod's
   `lastState.terminated.reason`, and `reason: Evicted` in events.
2. **Limit vs usage** — compare `resources.limits.memory` against the working
   set at the time of kill. A limit set too low is the most common cause.
3. **Trend, not snapshot** — a steadily climbing working set points to a leak;
   a sudden spike points to a workload burst (large request, batch job).
4. **Node pressure** — if multiple pods on the same node are evicted together,
   the node itself is under memory pressure, not just one pod.

## Common root causes

| Signal | Likely cause |
|--------|--------------|
| Single pod, limit << usage | Limit too low for normal workload |
| Monotonic memory growth | Application memory leak |
| Spike correlated with traffic | Unbounded buffering / large payloads |
| Many pods evicted on one node | Node memory pressure / overcommit |
| JVM/.NET without heap awareness | Runtime not respecting cgroup limits |

## What to recommend (read-only — never patch directly)

- Right-size `resources.requests/limits` based on observed p99 working set.
- For runtimes, ensure cgroup-aware memory settings (e.g. JVM
  `-XX:+UseContainerSupport`, .NET `DOTNET_GCHeapHardLimit`).
- If it's a leak, capture a heap profile before restart; restart only buys time.
- Changes go through GitOps (Helm values → ArgoCD), not `kubectl edit`.
