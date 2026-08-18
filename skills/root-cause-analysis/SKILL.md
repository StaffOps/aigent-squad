---
name: root-cause-analysis
description: "Use when investigating production incidents, diagnosing failures, or performing RCA. Covers 5 Whys, fault tree, cross-signal correlation, timeline construction, empirical validation, and common failure patterns in K8s/cloud-native systems."
keywords: [root-cause-analysis, root, cause, analysis, "root cause", "cause analysis", rca]
---
# Root Cause Analysis

Techniques and patterns for incident investigation in cloud-native distributed systems.

---

## RCA Techniques

### 5 Whys (adapted for distributed systems)

```
Symptom: Service X returns 500
  Why 1: Pod X is crashlooping
  Why 2: OOMKilled (exceeded memory limit)
  Why 3: Heap grows indefinitely after deploy Y
  Why 4: Deploy Y introduced cache without eviction
  Why 5: PR review did not catch absence of TTL in cache → PROCESS GAP
  
Root Cause: Cache without eviction policy (technical)
Systemic Cause: Review checklist does not include "cache memory behavior" (process)
```

Always seek the **systemic cause** beyond the technical one — what would prevent recurrence.

### Fault Tree Analysis

```
                    [Service Down]
                   /              \
          [Pod crash]          [Network issue]
          /        \                  |
    [OOMKill]  [Liveness fail]  [DNS timeout]
        |           |                 |
  [Memory leak] [Deadlock]     [CoreDNS overload]
        |           |                 |
  [Cache no TTL] [Lock ordering]  [ndots:5 + large cluster]
```

Useful when the symptom can have multiple causes. Work each branch until confirmed or eliminated.

### Elimination Method

```
Possible causes: [A, B, C, D, E]

Test 1: If A were the cause, we'd see X. X present? → NO → eliminate A
Test 2: If B were the cause, we'd see Y. Y present? → YES → B is a candidate
Test 3: If C were the cause, we'd see Z. Z present? → NO → eliminate C
Test 4: If D were the cause, we'd see W. W present? → YES → D is a candidate
Test 5: Can B and D coexist? → NO → one refutes the other → decisive test

Result: B confirmed, D refuted by test 5
```

---

## Cross-Signal Correlation

### Signal matrix for common problems

| Problem | Metric | Log | Trace | Event | Deploy |
|----------|---------|-----|-------|-------|--------|
| Memory leak | `container_memory_working_set_bytes` growing | OOMKill | Increasing latency (GC) | Pod restart | Yes (introduced leak) |
| Connection leak | Open connections growing, pool exhausted | "connection pool exhausted" | Timeout on DB call | — | Yes (changed pool config) |
| DNS issue | Request duration spike | "lookup: i/o timeout" | Gaps between spans | — | No (infra) |
| Certificate expiry | — | "x509: certificate has expired" | TLS handshake fail | — | No (cert rotation) |
| Resource starvation | CPU throttle, pending pods | — | — | FailedScheduling | Scaling event |
| Cascading failure | Multiple services error_rate up | Circuit breaker open | Cross-service error propagation | — | Single service deploy |

### Validation pattern: 3 signals agree

```
VALID (3 signals agree):
  Metric: error_rate up at 14:03 ✅
  Log: first error at 14:03:12 ✅  
  Deploy: rollout finished at 14:02:58 ✅
  → Strong causal correlation

INVALID (signals disagree):
  Metric: error_rate up at 14:03
  Log: first error at 13:45 (18 min BEFORE!)
  Deploy: none in the period
  → Correlation with deploy REFUTED — seek another cause
```

---

## Timeline Construction

### Sources for building a timeline

| Source | Command/Query | Granularity |
|--------|---------------|-------------|
| K8s events | `kubectl get events --sort-by=.lastTimestamp` | second |
| Pod restarts | `kubectl get pods -o json \| jq '.items[].status.containerStatuses[].restartCount'` | — |
| ArgoCD syncs | ArgoCD UI / `argocd app history <app>` | minute |
| Alertmanager | `/api/v2/alerts?active=true` | second |
| VictoriaMetrics | `changes(metric[5m])` to detect step changes | 15s-1min |
| Loki | `{namespace="X"} \| level="error" \| first_over_time` | second |
| Git | `git log --since="2h ago" --oneline` | commit |

### Timeline format

```
[2026-06-01 14:00:00] BASELINE: all metrics normal
[2026-06-01 14:02:58] CHANGE: ArgoCD sync completed (app=service-x, image=v1.2.3→v1.2.4)
[2026-06-01 14:03:05] SIGNAL: first error log "connection refused" (pod service-x-abc)
[2026-06-01 14:03:12] SIGNAL: error_rate metric crosses threshold (0.1% → 12%)
[2026-06-01 14:03:30] SIGNAL: trace shows timeout on redis call (span_id=xyz)
[2026-06-01 14:04:00] ALERT: ErrorBudgetBurn fired (service=service-x)
[2026-06-01 14:05:00] ALERT: PodCrashLooping fired
[2026-06-01 14:10:00] ACTION: rollback initiated
[2026-06-01 14:11:30] RESOLVED: error_rate back to baseline after rollback
```

---

## Failure Patterns in K8s/Cloud-Native

### Pattern 1: Deploy → Crash

```
Signal: CrashLoopBackOff after deploy
Investigate: OOMKill? Liveness fail? Startup crash?
  - OOM → check memory requests/limits vs actual usage
  - Liveness → check timeout, path, startup delay
  - Crash → check container logs (Previous: kubectl logs --previous)
```

### Pattern 2: Cascading failure

```
Signal: Multiple services failing simultaneously
Investigate: Which failed FIRST? (timeline)
  - Upstream dependency (DB, cache, queue) degraded
  - Circuit breakers not configured → thundering herd
  - Shared resource (node, network) saturated
```

### Pattern 3: Slow degradation

```
Signal: Latency grows linearly over hours/days
Investigate: Memory? Connections? Queue depth?
  - Memory leak (no GC or cache without eviction)
  - Connection pool leak (open connections not returned)
  - Queue backlog growing (consumer < producer rate)
```

### Pattern 4: Intermittent failures

```
Signal: Sporadic errors, not consistent
Investigate: Scheduling? DNS? Certs? Specific nodes?
  - Problems on specific nodes (hardware, network)
  - DNS resolution flapping (CoreDNS saturation)
  - Certificate renewal window (valid on some pods, expired on others)
  - Race conditions (timing-dependent, hard to reproduce)
```

### Pattern 5: "Nothing changed" failures

```
Signal: Failure without deploy or visible change
Investigate: What changed that is NOT a deploy?
  - Certificate expiry (automated rotation failed)
  - Secret rotation (External Secrets sync delay)
  - AWS service degradation (verify status page + CloudWatch)
  - Karpenter node rotation (new node, different config)
  - Spot interruption
  - DNS TTL expired + endpoint moved
  - Dependency SLA change (upstream rate limit hit)
```

---

## Empirical Validation

### Confirmation tests

| Type | How | When to use |
|------|-----|-------------|
| **Rollback** | Revert deploy, observe recovery | Deploy-related issues |
| **Reproduction** | Deliberately trigger the same condition | Logic bugs, race conditions |
| **Isolation** | Disconnect suspected component, observe | Cascading failures |
| **Canary** | Apply fix to 1 pod, compare with remainder | Validate fix without blast radius |
| **Counter-factual** | Compare pod/node WITH and WITHOUT the condition | Environment-specific issues |

### Checklist before declaring "resolved"

- [ ] Symptom stopped? (not just decreased)
- [ ] Metrics returned to baseline?
- [ ] No active related alerts?
- [ ] Fix makes causal sense? (not coincidence)
- [ ] Monitored for adequate period (≥15min for intermittent issues)?
- [ ] Prevention proposed? (alert, test, guardrail)
- [ ] Investigation documented? (timeline + evidence + conclusion)
