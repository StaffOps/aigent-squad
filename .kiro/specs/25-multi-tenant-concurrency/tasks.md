# Tasks: Multi-Tenant Concurrency

## Phase 1 — Distributed state (Redis-backed)

- [ ] T1: Migrate `CircuitBreaker` to Redis backend with in-memory fallback
- [ ] T2: Create `src/core/session_lock.py` (SETNX with TTL + Lua release)
- [ ] T3: Wire `session_lock` into `supervisor.process_request`

## Phase 2 — Rate limit + budget

- [ ] T4: Create `src/core/rate_limiter.py` (sliding window + budget counter)
- [ ] T5: Add cost estimator (input + max output tokens × pricing per model)
- [ ] T6: Wire rate check + budget check in supervisor before classify
- [ ] T7: Return 429/503 with proper headers (`X-RateLimit-Remaining`, `X-Budget-Remaining-USD`)

## Phase 3 — Bedrock semaphore

- [ ] T8: Add `asyncio.Semaphore` to `BedrockClient.invoke` (configurable via env)
- [ ] T9: Add metrics `aigent.bedrock.queue_depth` + `queue_wait`

## Phase 4 — Load testing

- [ ] T10: Create `tests/load/scenario_basic.js` (k6, 50 users)
- [ ] T11: Create `tests/load/scenario_burst.js` (k6, 200 users, stress)
- [ ] T12: Create `.github/workflows/load.yml` (manual trigger)
- [ ] T13: Add Grafana dashboard for load test KPIs

## Phase 5 — Documentation + milestone gate

- [ ] T14: `docs/MULTI-TENANCY.md` (isolation, scaling, limits)
- [ ] T15: `docs/LOAD-TESTING.md` (how to run, baselines)
- [ ] T16: Update `docs/METRICS.md` with new metrics
- [ ] T17: Update `helm-charts/charts/aigent-squad/values.yaml` with rate limit config
- [ ] T18: Tests by separate agent (≥80% coverage) — circuit breaker distributed, session lock, rate limiter, semaphore
- [ ] T19: Smoke test: run k6 basic scenario locally, verify p99<10s

## Ordem sugerida

T1 → T2 → T3 (lock no fluxo); T4/T5/T6/T7 (rate); T8/T9 (semaphore — pode em paralelo); T10–T13 (load); T14–T17 (docs); T18 (test gate); T19 (smoke).

## Notas

- **Fail-open everywhere**: Redis caiu → CircuitBreaker em-memória, SessionLock no-op, RateLimiter permite.
- **Não usar HSET/HGET** para circuit breaker — keys separadas com TTL próprio são mais simples.
- **Estimativa de custo**: pessimista (assume max_output_tokens). False positives aceitos.
- **Load test**: roda contra docker compose local primeiro; CI nightly contra ambiente HML.
- **Spec 18 (RCA workflow)** pode ser feita ANTES desta — mas se a 18 explode em uso real, esta vira urgente.
