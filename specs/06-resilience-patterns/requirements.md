---
spec: 06-resilience-patterns
status: done-with-deferrals
completed: null
superseded_by: null
depends_on: []
deferred: ["T11 formal smoke"]
---

# Feature: Resilience Patterns + Async-First

**Spec**: `06-resilience-patterns`
**Severity**: 🔴 Critical (velocity engine + failure survival)
**Origin**: `../ANALYSIS.md` CONV-1 (synchronous I/O), CONV-2 (fail-closed), CONV-3 (classifier SPOF), CONV-5 (retry), sre R1–R9, dev F1–F12
**Depends on**: `02-unify-agent-architecture`

Two things in the same package because they go together: (1) **async-first** — all I/O (Bedrock, DynamoDB, Redis, HTTP, GitLab) stops being synchronous inside `async` handlers, unlocking real parallelism; (2) **resilience** — fail-open on non-critical dependencies, circuit breaker, timeouts, classifier fallback. Without (1), fan-out (spec 17) and RCA (spec 18) **don't parallelize** — it's the prerequisite for all of the product's velocity.

## User Stories

WHEN N agents are invoked in an investigation THEN the N Bedrock calls SHALL occur **concurrently** (total time ≈ the slowest, not the sum).

WHEN the code performs I/O (Bedrock, DynamoDB, Redis, HTTP, GitLab) inside an `async` handler THEN it SHALL **not block the event loop** (via async client or `asyncio.to_thread`); `time.sleep` SHALL become `asyncio.sleep`.

WHEN Redis is unavailable THEN the system SHALL treat it as a cache miss and continue (**fail-open**), logging the error.

WHEN DynamoDB is unavailable THEN the system SHALL proceed with empty history (**fail-open**), without crashing the query.

WHEN the classifier (Bedrock) fails THEN the system SHALL apply a **fallback** (keyword/rule OR last session agent OR ask the user to choose), never failing 100% of queries.

WHEN a specialist agent is down/slow THEN the **circuit breaker** SHALL open after N failures and return fast ("agent unavailable"), without waiting for the full timeout on each query.

WHEN a Bedrock call is retried THEN the backoff SHALL have **jitter** and respect botocore's adaptive retry (without stacking retries).

## Acceptance Criteria

- [ ] Bedrock, DynamoDB, Redis, GitLab, docs portal accessed in a **non-blocking** manner (async client or `asyncio.to_thread`); zero `time.sleep` in async path.
- [ ] `asyncio.gather` proven parallel: test with mocked clients (sleep) shows time ≈ max, not sum.
- [ ] Redis fail-open (get→None, set→swallow+log) — tested with Redis unavailable.
- [ ] DynamoDB fail-open (fetch→[], save→log) — tested with table inaccessible.
- [ ] Deterministic classifier fallback when Bedrock fails — tested.
- [ ] Circuit breaker per agent (closed→open→half-open) with **configurable** thresholds (spec 19/22).
- [ ] Configurable timeouts per call (agent, Bedrock); httpx pool per agent (bulkhead).
- [ ] Retry with jitter + botocore adaptive (`mode=adaptive`); no stacked retry.
- [ ] Graceful shutdown (SIGTERM): drain in-flight, flush OTel, close connections (FastAPI `lifespan`).
- [ ] Tests (test-author ≠ author, ≥90%): parallelism, fail-open Redis/DynamoDB, classifier fallback, circuit breaker, retry+jitter.

## Out of scope

- Health endpoints `/healthz`+`/ready` → spec 07 (complementary).
- Model choice (Haiku for classifier) → spec 11.
- Multi-agent / fan-out itself → spec 17 (this spec only ensures it actually parallelizes).
