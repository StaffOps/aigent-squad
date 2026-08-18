# Tasks: Multi-Tenant Concurrency

## Phase 1 — Distributed state (Redis-backed)

- [x] T1: Migrate `CircuitBreaker` to Redis backend with in-memory fallback
- [x] T2: Create `src/core/session_lock.py` (SETNX with TTL + Lua release)
- [x] T3: Wire `session_lock` into `supervisor.process_request`

## Phase 2 — Rate limit + budget

- [x] T4: Create `src/core/rate_limiter.py` (sliding window + budget counter)
- [x] T5: Add cost estimator (input + max output tokens × pricing per model)
- [x] T6: Wire rate check + budget check in supervisor before classify
- [x] T7: Return 429/503 with proper headers (`X-RateLimit-Remaining`, `X-Budget-Remaining-USD`)

## Phase 3 — Bedrock semaphore

- [x] T8: Add `asyncio.Semaphore` to `BedrockClient.invoke` (configurable via env)
- [x] T9: Add metrics `aigent.bedrock.queue_depth` + `queue_wait`

## Phase 4 — Load testing

- [x] T10: Create `tests/load/scenario_basic.js` (k6, 50 users)
- [x] T11: Create `tests/load/scenario_burst.js` (k6, 200 users, stress)
- [x] T12: Create `.github/workflows/load.yml` (manual trigger)
- [ ] T13: ~~Add Grafana dashboard for load test KPIs~~ — DEFERRED (out of scope for this phase; will revisit when baseline numbers exist)

## Phase 5 — Documentation + milestone gate

- [x] T14: `docs/MULTI-TENANCY.md` (isolation, scaling, limits)
- [x] T15: `docs/LOAD-TESTING.md` (how to run, baselines)
- [x] T16: Update `docs/METRICS.md` with new metrics
- [ ] T17: ~~Update `helm-charts/charts/aigent-squad/values.yaml` with rate limit config~~ — already done in previous session
- [ ] T18: Tests by separate agent (≥80% coverage) — circuit breaker distributed, session lock, rate limiter, semaphore
- [ ] T19: Smoke test: run k6 basic scenario locally, verify p99<10s

## Suggested order

T1 → T2 → T3 (lock into the flow); T4/T5/T6/T7 (rate); T8/T9 (semaphore — can be in parallel); T10–T13 (load); T14–T17 (docs); T18 (test gate); T19 (smoke).

## Notes

- **Fail-open everywhere**: Redis down → CircuitBreaker in-memory, SessionLock no-op, RateLimiter allows.
- **Do not use HSET/HGET** for circuit breaker — separate keys with their own TTL are simpler.
- **Cost estimate**: pessimistic (assumes max_output_tokens). False positives accepted.
- **Load test**: runs against local docker compose first; CI nightly against HML environment.
- **Spec 18 (RCA workflow)** can be done BEFORE this — but if 18 explodes in real usage, this becomes urgent.
