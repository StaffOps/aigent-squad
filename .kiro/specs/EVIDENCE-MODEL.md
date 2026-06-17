# Evidence Model — Cross-Signal Catalog & Correlation Rules

**Status**: Agreed by observability + sre + troubleshoot (3-round deliberation, 2026-06-02)
**Feeds**: spec 18 (evidence model), specs 09/10 (metric catalog)
**Respects**: `observability-principles.md` (cardinality), `investigation-protocol.md` (evidence hierarchy, causal ordering), `k8s-safety.md` (read-only)

This replaces the naive correlation rule (">=3 signals from different agents = high confidence"), which counted correlated effects of one cause as independent confirmation, producing confident-but-wrong RCAs.

---

## 1. Evidence Data Model

```python
@dataclass
class Evidence:
    id: str                       # unique within investigation
    source_agent: str             # observability|kubernetes|devops|aws|finops|anomaly-ctrl
    signal_id: str                # C1..C8, M1..M13, I1..I8, T1..T4, E1..E4
    signal_subtype: str           # for derivation checks (e.g. "OOMKill", "pod_restart")
    causal_layer: str             # change|mechanism|impact|temporal|elimination
    timestamp: str                # ISO 8601 with timezone (UTC)
    timestamp_precision: str      # ms|second|15s|60s
    strength: str                 # forte|media|fraca
    fault_domain: str             # app:<svc>|infra:<node>|dep:<backing-svc>|net:<component>
    summary: str
    raw_query: str                # query that produced this (audit)
    contradicts_hypothesis: bool
    contradiction_explained: bool
    trace_id: str | None          # exemplar-bridge detection
    derivation_source: str | None # id of evidence this was derived FROM
    is_recurring: bool            # K8s events: count > 1?
    first_occurrence: str | None  # for recurring: the real causal anchor
```

## 2. Causal Layers

```
CHANGE      what changed       deploy, config, node, dependency state
MECHANISM   how it caused      error trace, OOMKill, memory growth, conn refused
IMPACT      visible damage     error rate, latency, restarts
TEMPORAL    confirms after     rollback -> recovery
ELIMINATION narrows space      "no deploy in window" kills ~40% of hypotheses
```

Confidence tracks:
- **Track A (event-driven)**: CHANGE + MECHANISM + IMPACT + valid temporal order + 0 unexplained contradictions -> HIGH
- **Track B (degradation, no explicit change)**: continuous MECHANISM (e.g. monotonic memory growth) + confirming MECHANISM + IMPACT + no alternative CHANGE -> HIGH

---

## 3. Signal Catalog

### CHANGE (always collect, Phase 1)

| ID | Signal | Agent | Query | Precision | RCA |
|----|--------|-------|-------|-----------|-----|
| C1 | Deploy/sync event | devops | ArgoCD `/api/v1/applications/{app}/events`; `argocd app history` | second | HIGH |
| C2 | ConfigMap/Secret update | kubernetes | `kubectl get events --field-selector reason=Updated -n $ns` | 60s | HIGH |
| C3 | Node create/delete (Karpenter) | kubernetes | events `reason=Launched,Terminated` | 60s | MED |
| C4 | Dependency state change | aws | CloudWatch RDS `failover`, ElastiCache `Evictions`, health events | 60s | HIGH |
| C5 | Traffic change (>2σ) | observability | `rate(spanmetrics_apm_calls_total{service_name="$svc"}[5m])` vs 1d baseline | 15s | MED |
| C6 | Scaling event (KEDA) | kubernetes | events `involvedObject.kind=ScaledObject` | 60s | LOW |
| C7 | cert-manager/ExternalSecret sync fail | kubernetes | events `reason=SyncFailed` | 60s | HIGH |
| C8 | Anomaly-ctrl pre-correlated alert | anomaly-ctrl | controller API: score + linked evidence URLs | 5min | MED |

### MECHANISM (conditional, Phase 2-3)

| ID | Signal | Agent | Query | Precision | RCA | Trigger |
|----|--------|-------|-------|-----------|-----|---------|
| M1 | Error trace (root span) | observability | Tempo `{ resource.service.name="$svc" && status=error }` | ms | HIGH | error_rate>baseline |
| M2 | Dependency error trace (client) | observability | Tempo `{ ... && status=error && kind=client }` | ms | HIGH | dep suspected |
| M3 | First error log (temporal anchor) | observability | Loki `{service_workload="$svc"} \|= "ERROR"` `direction=forward&limit=1` | ms | HIGH | ALWAYS |
| M4 | NEW error pattern (not pre-T) | observability | Loki `count_over_time(... [T-24h,T]) == 0` | ms | HIGH | deploy in window |
| M5 | OOMKill | kubernetes | events `reason=OOMKilling` -> firstTimestamp/count | 30s | HIGH | ALWAYS |
| M6 | Memory monotonic growth | observability | `deriv(container_memory_working_set_bytes{container="$svc"}[30m])>0` 10m+ | 15s | HIGH | OOM/restarts |
| M7 | CPU throttle >50% | observability | `rate(container_cpu_cfs_throttled_periods_total[5m])/rate(...periods_total[5m])` | 15s | MED | latency w/o errors |
| M8 | Conn timeout/refused | observability | Loki `\|~ "connection refused\|connection timeout"` | ms | HIGH | dep suspected |
| M9 | TLS/auth error | observability | Loki `\|~ "x509.*expired\|401\|403\|authentication failed"` | ms | HIGH | no deploy+no infra change |
| M10 | DNS error | observability | Loki `\|~ "lookup.*timeout\|no such host\|NXDOMAIN"` | ms | HIGH | multi-svc simultaneous |
| M11 | Circuit breaker change | observability | Loki `\|~ "circuit.*open\|breaker.*state"` | ms | MED | cascading |
| M12 | Pod scheduling failure | kubernetes | events `reason=FailedScheduling` | 60s | MED | pending pods |
| M13 | Node condition (Mem/Disk pressure) | observability | `kube_node_status_condition{condition=~"MemoryPressure\|DiskPressure",status="true"}` | 15s | HIGH | multi-svc same node |

### IMPACT (always collect, Phase 1)

| ID | Signal | Agent | Query | Precision | RCA |
|----|--------|-------|-------|-----------|-----|
| I1 | Error rate (RED-E) | observability | 5xx rate / total rate via spanmetrics | 15s | HIGH |
| I2 | Latency p99 (RED-D) | observability | `histogram_quantile(0.99, ...http_server_request_duration...)` | 15s | HIGH |
| I3 | Request rate (RED-R) | observability | `sum(rate(spanmetrics_apm_calls_total{service_name="$svc"}[5m]))` | 15s | MED |
| I4 | Container restarts | kubernetes | `increase(kube_pod_container_status_restarts_total[15m])` | 15s | HIGH |
| I5 | Error budget burn | observability | `slo:burn_rate:5m{service="$svc"}` (needs recording rule) | 15s | MED |
| I6 | Log error volume spike | observability | Loki `sum(count_over_time(... \|= "ERROR" [5m]))` | ms | MED |
| I7 | Multi-service blast radius | observability | `sum by (service_name)(rate(...status_code=~"5..")[5m])` | 15s | LOW |
| I8 | AWS backing health | aws | CloudWatch RDS/ElastiCache CPU/Mem/Connections | 60s | MED |

### TEMPORAL (strengtheners, never sufficient alone)

| ID | Signal | Proves | RCA |
|----|--------|--------|-----|
| T1 | Rollback -> recovery | reverted change WAS the cause | HIGH (modifier) |
| T2 | cause_ts < first_effect_ts | temporal ordering valid | required constraint |
| T3 | Recovery ~ scaling completion | autoscaling resolved saturation | MED |
| T4 | Multi-svc sequential first-error order | cascade direction | HIGH (cascade) |

### ELIMINATION (narrows hypotheses, not counted for confidence)

| ID | Eliminates | Query |
|----|-----------|-------|
| E1 | Deploy regression (~40%) | ArgoCD empty history for window |
| E2 | Dependency outage | CloudWatch normal ranges |
| E3 | Infra failure | no `NodeNotReady` events |
| E4 | Traffic spike | C5 < 2σ |

---

## 4. Root-Cause Signatures (14 classes)

Each: CHANGE + MECHANISM + IMPACT + timing window + contra-evidence guards.

1. **Deploy regression** — C1 + (M1|M4) + (I1|I4|I2); C1->M3 [0,120s]; HIGH needs error pattern absent in [T-30m,T-1m] + scope match. Guards: pre-existing error -> LOW; ineffective rollback -> LOW; scope mismatch -> LOW; >1 deploy -> need discriminator else MEDIUM.
2. **Memory leak** — (Track B) M6+M5+(I4|I1); M6 sustained 10m -> M5 -> I4 [0,5s]; HIGH needs growth not proportional to traffic. Guards: node MemoryPressure -> reclass #5; flat-then-spike -> not leak; proportional to traffic -> under-provisioned.
3. **Dependency outage** — C4 + (M2|M8) + (I1|I2); C4->M8 [0,60s] (+120s CW lag); other consumers affected. Guards: app outbound spiked first -> invert causality; only some pods -> pod-specific; dep metrics normal -> LOW.
4. **Cascading failure** — patient-zero + (M1/M2 trace propagation|M11|T4) + I7; propagation [1,60s] via LOG timestamps. Guards: all first-errors within 15s -> ambiguous (need trace parentage); upstream recovered/downstream stuck -> independent.
5. **Node/infra failure** — C3|node NotReady + (M13|M12|multi-pod same node) + I4 same node; [0,120s]; other nodes healthy. Guard: other-node pods also failing -> not node-specific.
6. **Cert/secret expiry** — C7|cert_expiry==T0 + M9 + I1(~100%); [0,300s]. Guard: auth errors correlate with restart not expiry -> restart is cause.
7. **Traffic spike (organic)** — C5(>3σ) + (M12|M7) + (I2|I1); even distribution. Guards: concentrated on error endpoints -> retry storm; small caller set -> cascade; no impact -> not an incident.
8. **DNS failure** — CoreDNS event + (M10 + `coredns ... SERVFAIL` spike) + I1 multi-svc; [0,60s]. Guard: CoreDNS healthy -> app-level caching/ndots.
9. **Config drift** — ArgoCD OutOfSync|C2 + functional-diff-matches-failure + (I1|I2). Guard: cosmetic diff -> LOW.
10. **NetworkPolicy/Istio** — policy change + M8 "REFUSED" + I1 to specific dest; [0,60s]. Guard: timeout not refused -> not policy.
11. **Slow query/DB** — C1|data growth + M1 long DB span(>1s) + I2 (usually no I1). Guard: all endpoints slow -> resource exhaustion. (RDS PI lags 120s.)
12. **Secret rotation sync fail** — C7 + M9 only in new pods + I1 partial; pod-age correlation. Guard: all pods failing -> full expiry (#6).
13. **Thundering herd on restart** — mass restart + outbound spike>5x + dep saturation AFTER restart + impact after readiness; [5,120s]. Guard: dep sick before restart -> #3.
14. **Queue backlog/async stall** — (Track B) consumer stall + queue depth monotonic + producer impact much later. Guard: queue stable -> not it; simultaneous producer errors -> shared root cause.

Sub-variants (per user decision):
- **GC/JIT warmup** = transient deploy regression (#1): if impact self-resolves <10min without intervention, flag TRANSIENT, recommend monitoring not rollback.
- **Rate limiting (429)** = dependency sub-variant (#3): mechanism is HTTP 429 (not 500/timeout); remediation is reduce traffic, not fix dependency.

---

## 5. Confidence Scoring

Layer-based (not count-based). Pseudocode:

```
unexplained contra-evidence            -> LOW (blocker)
temporal order invalid                 -> LOW (blocker)
Track B: continuous+confirming MECH + IMPACT + no alt CHANGE -> HIGH
no CHANGE (and not Track B)             -> LOW
CHANGE+MECHANISM+IMPACT + >=3 independent -> HIGH
CHANGE+MECHANISM+IMPACT + <3 independent  -> MEDIUM
CHANGE+IMPACT+TEMPORAL(rollback)       -> HIGH
2 of 3 layers                          -> MEDIUM
else                                   -> LOW
```

Modifiers (move ±1 level, output stays 3 discrete levels):
- down: indirect mechanism; cause->effect gap >60s; multiple CHANGE candidates; impact <2%; single pod.
- up: rollback confirmed recovery; code diff matches error path; anomaly-ctrl score >0.8; ≥2 fault domains same mechanism.

### Timing tolerances (causal-order validation)

| cause prec / effect prec | max gap (s) |
|--------------------------|-------------|
| second / ms | 5 |
| second / 15s | 30 |
| second / 60s | 120 |
| 60s / ms | 125 |
| 15s / ms | 20 (metric is always LATE) |
| ms / ms | 5 (NTP) |

**CloudWatch lags up to 120s — never use it to anchor temporal order. Metrics (15s) cannot resolve sub-15s cascade ordering — use Loki forward-limit-1 per service.**

---

## 6. Independence Test (resolves the naive ≥3 rule)

Two signals are independent iff: given hypothesis H, knowing S1 occurred does NOT make S2 certain.

Rules:
1. Same `trace_id` or derivation link -> NOT independent (exemplar bridge = same observation, different zoom).
2. Same layer + same fault_domain -> NOT independent (default).
3. Same fault_domain + in DERIVATION_PAIRS -> NOT independent.
4. Different layer + different fault_domain -> INDEPENDENT.
5. Different layer + same fault_domain + not derivable -> INDEPENDENT.
6. Same layer + different fault_domain -> INDEPENDENT (two services failing = independent observations of shared cause).

```python
DERIVATION_PAIRS = {
  ("OOMKill","pod_restart"), ("error_rate_spike","error_log"),
  ("error_rate_spike","error_trace"), ("error_log","error_trace"),
  ("latency_spike","slow_trace"), ("memory_growth","OOMKill"),
  ("node_NotReady","pod_eviction"), ("node_MemoryPressure","OOMKill"),
  ("cpu_throttle","latency_spike"),
}
```

Static table is the DEFAULT (covers ~90%); the synthesizer may override per-hypothesis (mechanism TBD in implementation — see open disagreement).

---

## 7. Collection Phases

| Phase | Timing | What | Agents (parallel) |
|-------|--------|------|-------------------|
| 1 | 0-3s | CHANGE + IMPACT (metrics+events) | obs: I1-I6,C5 / k8s: C2,C3,C6,C7,M5,M12,M13 / devops: C1 / aws: C4,I8 / anomaly: C8 |
| 2 | 3-8s | temporal anchor (ALWAYS) | obs: M3 |
| 3 | 5-15s | deep mechanism (conditional) | triggered by Phase 1 (see trigger column) |

Matching: rule engine narrows to 1-3 candidate recipes (deterministic) -> triggers Phase 3 -> LLM synthesizer writes narrative. **LLM confidence is a ceiling (cannot upgrade) + soft floor (can downgrade with logged justification).**

---

## 8. Resolved Decisions (2026-06-02)

| # | Decision | Choice |
|---|----------|--------|
| 1 | "No cause found" follow-up | **Allow 1 targeted follow-up** when Track B conditions met (monotonic mechanism present), focused on expanded CHANGE search only. |
| 2 | LLM vs confidence score | **Ceiling + soft floor** — LLM cannot upgrade confidence; can downgrade with logged justification. |
| 3 | Output format | **Primary + up to 2 alternatives** (matches SRE incident-review practice). |
| 4 | Anomaly-ctrl trust | **score >0.8 -> fast-path** (skip Phase 1, go to Phase 3 verify); **≤0.8 -> one MECHANISM evidence, full Phase 1.** |
| 5 | GC/JIT warmup | **Sub-variant of #1** — transient deploy regression; flag, don't rollback. |
| 6 | Rate limiting (429) | **Sub-variant of #3** — mechanism=429; remediation=reduce traffic. |

## 9. Open disagreement (deferred to implementation)

Static vs contextual independence: static `DERIVATION_PAIRS` table as default (observability), LLM override per-hypothesis (sre). Override mechanism TBD when implementing the correlator.

## 10. Implementation checklist

**spec 18 (evidence model)**: adopt `Evidence` dataclass; `score_confidence()`; `validate_temporal_order()` with per-source tolerances; `count_independent()` with derivation pairs; scratchpad carries `causal_layer`+`timestamp_precision`; 3-phase collection; contra-evidence guards as executable checks.

**specs 09/10 (metric catalog)**: register all signals with exact queries; cardinality — never `investigation_id`/`exception_type` as VM labels; recording rule `slo:burn_rate:5m` per service (I5).

**anomaly-ctrl integration**: fast-path threshold 0.8; map controller output to Evidence (MECHANISM, strength from score); "no anomaly" = weak contra-evidence (non-blocking).
