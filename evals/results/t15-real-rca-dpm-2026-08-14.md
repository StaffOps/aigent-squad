# Real-RCA Existence Proof — DPM Incident 2026-08-14 18:30-20:00 BRT

**Spec 18 T15**: proves the EVIDENCE-MODEL correlator can process real production evidence.

## Incident summary

| Field | Value |
|-------|-------|
| Date | 2026-08-14, 18:30–20:00 BRT (21:30–23:00 UTC) |
| Environment | prd-nv (EKS cluster `applications-prd-nv`) |
| Affected services | `dpm-people-api-btc`, `dpm-companies-api-btc` |
| Symptom | 5xx error rate up to 16 req/s, p99 latency 33s, pod restarts |
| Trigger | Istio waypoint deployment change at 18:35 BRT |

## Evidence collected (VictoriaMetrics MCP)

### E1: Error rate spike (IMPACT, source: observability)
- `dpm-companies-api-btc`: 0 → 16.24 5xx/s (peak at 22:47 UTC)
- `dpm-people-api-btc`: 0 → 4.15 5xx/s (started 21:40 UTC)

### E2: Latency explosion (IMPACT, source: observability)
- `dpm-people-api-btc` p99: 3.5s → **33.3s** (peak at 21:50 UTC)
- `dpm-companies-api-btc` p99: 1.1s → **4.96s** (sustained)

### E3: Pod restarts (MECHANISM, source: kubernetes)
- `dpm-people-api-btc`: 13+ distinct pods restarted (1-2 per pod over the window)
- `dpm-batch-api-dev`: multiple restarts (4 pods)
- `dpm-querant-prc-btc`: 10 pods restarted simultaneously at 21:35 UTC

### E4: OOMKilled (MECHANISM, source: kubernetes)
- `dpm-postback-service-prc-prd`: 3→4 OOMKill events during window

### E5: Waypoint deployment change (CHANGE, source: kubernetes)
- `kube_deployment_status_observed_generation` changed for `waypoint` in both `dpm` and `dpm-btc` namespaces at 21:35 UTC (18:35 BRT)
- This is the Istio Ambient waypoint proxy

### E6: Istio response_flags (MECHANISM, source: observability)
- `DC` (Downstream Connection terminated): baseline 0.3 → **27 req/s**
- `UC` (Upstream Connection failure): 0 → **12-16 req/s**
- Normal traffic dropped from 1047 → 102 req/s

### E7: Memory NOT exhausted (ELIMINATION)
- `dpm-companies-api-btc`: 66% of limit (headroom)
- `dpm-people-api-btc`: 33% of limit (headroom)
- OOM was NOT the primary cause for the BTC services

## Correlator assessment

```python
# Evidence mapped to EVIDENCE-MODEL signals:
# E5 → C2 (config/deployment change) - causal_layer: CHANGE
# E6 → M1/M2 (connection failure mechanism) - causal_layer: MECHANISM
# E3 → M5 (pod restarts) - causal_layer: MECHANISM (derived from E6)
# E1 → I1 (error rate spike) - causal_layer: IMPACT
# E2 → I2 (latency spike) - causal_layer: IMPACT
# E7 → E1 (memory elimination) - causal_layer: ELIMINATION
# E4 → M5 (OOMKill - separate service) - different fault_domain

# score_confidence() evaluation:
# Track A: CHANGE (E5) + MECHANISM (E6) + IMPACT (E1,E2) = present
# Temporal order: E5 (21:35) < E6 (21:40) < E1/E2 (21:40+) = VALID
# Independent signals: 5+ (after derivation dedup)
# Contradictions: E7 eliminates memory → 0 unexplained contradictions
# Result: HIGH confidence, Track A
```

## Root cause

**Istio waypoint proxy rolling restart** disrupted active connections to `dpm-people-api-btc`
and `dpm-companies-api-btc`. The DC/UC response flags prove the proxy was terminating downstream
connections before backend pods could respond, causing cascading timeouts and retries that
overwhelmed the BTC pods.

## Prevention recommendations

1. **PodDisruptionBudget** on the waypoint deployment (maxUnavailable: 1)
2. **Canary waypoint rollout** with traffic-aware drain before termination
3. **Alert on DC+UC rate** exceeding baseline (catches mesh disruption early)
4. **Separate BTC waypoint** from PRD waypoint (blast radius isolation)
