---
spec: 25-multi-tenant-concurrency
status: done
completed: 2026-08-18
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Multi-Tenant Concurrency

**Spec**: `25-multi-tenant-concurrency`
**Severity**: 🟠 High (required for real production with >5-10 simultaneous users)
**Origin**: analysis of gaps in multi-conversation handling (chat session 2026-06-14)
**Depends on**: `06-resilience-patterns` (async already done), `17-multi-agent-collaboration` (fan-out already done)

The current system handles 5-10 simultaneous users in the same pod well, but has gaps for real scale: in-memory circuit breaker (not multi-replica safe), no session locking (race on fast messages), no rate limit / budget guard, no semaphore on Bedrock (can exceed TPS limit), and no load test.

This spec addresses the 5 gaps to make the system **multi-tenant production-ready**.

---

## User Stories

WHEN there are N replicas of the supervisor THEN the circuit breaker SHALL have **shared** state (one replica opens → all respect it immediately).

WHEN a user sends 2 messages very quickly in the same session THEN the second SHALL wait for the first to complete (locking by session_id), avoiding a race on the history.

WHEN a user exceeds the queries/min limit OR cost/day THEN the system SHALL return error `429 Too Many Requests` with a note of when to retry.

WHEN the total daily cost exceeds the global budget THEN new queries SHALL be blocked (`503 Service Unavailable`) until the daily reset.

WHEN there are many simultaneous Bedrock calls THEN the system SHALL serialize via semaphore (default: 10 concurrent) — prevents exceeding TPS.

WHEN N simultaneous users send queries THEN the system SHALL process all without cross-contamination of context (isolation already tested; now a **load test** with k6 confirms).

WHEN the load test runs 100 users × 10 simultaneous queries THEN p99 < 10s and zero 5xx errors **NOT caused by Bedrock** (model rate limit is acceptable and falls into retry).

---

## Acceptance Criteria

### 1. Distributed circuit breaker
- [ ] CircuitBreaker state moved to Redis (key: `cb:<name>:state`, `cb:<name>:failures`, `cb:<name>:last_failure`)
- [ ] Atomic lock via Redis SETNX on state transitions
- [ ] Fail-open maintained: if Redis goes down, breaker works in local memory
- [ ] TTL on the key (auto-cleanup after recovery_timeout * 2)

### 2. Session locking
- [ ] Lock in Redis (key: `lock:session:<session_id>`) via SETNX with TTL=30s
- [ ] Wait up to 5s if another request holds the lock
- [ ] Released in finally (always) + TTL as fallback
- [ ] If Redis goes down: lock is skipped (accepted degradation)

### 3. Rate limit / budget
- [ ] Per-user rate: 60 queries/min (configurable) — Redis sliding window
- [ ] Global daily budget in USD (default: $50/day) — Redis counter with TTL=24h
- [ ] Per-user daily soft cap: 20% of global budget by default
- [ ] Response headers: `X-RateLimit-Remaining`, `X-Budget-Remaining-USD`
- [ ] Estimated cost **before** the call (max_tokens + system prompt) for pre-check
- [ ] Metric `aigent.rate_limit.blocks` (counter, labels: reason=user/global)

### 4. Bedrock semaphore
- [ ] `asyncio.Semaphore(10)` (configurable) in `BedrockClient`
- [ ] Metric `aigent.bedrock.queue_depth` (gauge)
- [ ] Metric `aigent.bedrock.queue_wait_ms` (histogram)
- [ ] Acquisition timeout: 30s (raise before waiting forever)

### 5. Load testing
- [ ] Script `tests/load/scenario_basic.js` (k6) — 50 users × varied queries (single + cross-domain)
- [ ] Script `tests/load/scenario_burst.js` — 200 users × 30s (stress)
- [ ] CI workflow `.github/workflows/load.yml` (manual / nightly)
- [ ] Grafana dashboard with KPIs: p50/p99 latency, error rate, throughput
- [ ] Documentation in `docs/LOAD-TESTING.md` with expected baseline

### 6. Documentation
- [ ] `docs/MULTI-TENANCY.md` explaining isolation, scaling, limits
- [ ] `docs/METRICS.md` updated with new metrics (queue, rate_limit)

---

## Out of scope

- Complex per-organization/tenant quotas (multi-org with billing) — future
- LLM response caching (cache of identical queries) — separate
- Persistent queue for queries (Kafka/SQS) — overkill for this usage profile
- Auto-scaling of the supervisor (HPA) — already comes from the Helm chart, unchanged here
