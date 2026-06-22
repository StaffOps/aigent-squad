# Tasks: Edge Gateway + Worker Pool

Order by structural risk. L1 is the big move (no new behavior); later layers add
admission, scaling, and validation. Reuses `staffops-chaitops` patterns (authorized).

## Phase 1 — Extract the gateway service (structural, behavior-neutral)
- [ ] T1: Create `src/gateway/` package (FastAPI app, config, health probes)
- [ ] T2: Add `POST /internal/process` to the supervisor wrapping `process_request` (internal-token gated)
- [ ] T3: Move `/query` + `/v1/*` routes from `supervisor/server.py` to the gateway; import spec 29 `openai_compat` shaping in the gateway
- [ ] T4: `gateway/supervisor_client.py` — HTTP+SSE client with preflight (supervisor reachable? agent known?)
- [ ] T5: `gateway/auth.py` — edge auth (API key allowlist / internal token); supervisor trusts gateway token
- [ ] T6: Tests T1–T5 (independent author) — routes forward correctly, preflight returns clean 404/503

## Phase 2 — Worker pool + backpressure (ChaitOps pattern)
- [ ] T7: `gateway/worker_pool.py` — local `asyncio.Semaphore`, `PoolFullError`, cancel via `cancel:<job_id>` Redis poll, `wait_for` timeout
- [ ] T8: Wire pool into the forward path; `PoolFullError → 503 + Retry-After`
- [ ] T9: Job lifecycle in Redis (`job:<id>`, `audit`) — ChaitOps shape
- [ ] T10: `POST /jobs/{id}/cancel` endpoint
- [ ] T11: Metrics `aigent.gateway.pool_rejections`/`pool_depth`/`queue_wait`
- [ ] T12: Tests (independent author) — backpressure 503, cancel within ~500ms, timeout

## Phase 3 — Global admission guards (wire spec 25)
- [ ] T13: `gateway/admission.py` — per-user rate + global daily budget pre-check (Redis; reuse spec 25 `RateLimiter`/`BudgetGuard`)
- [ ] T14: Enforce at admission BEFORE forward; `429`/`503` with `X-RateLimit-Remaining`/`X-Budget-Remaining-USD`
- [ ] T15: Tests (independent author) — rate block, budget block, fail-open on Redis down

## Phase 4 — Deploy (two-tier)
- [ ] T16: Helm — split into `gateway` + `supervisor` Deployments + Services; gateway fronts supervisor
- [ ] T17: Independent HPA/KEDA per tier (gateway on RPS, supervisor on Bedrock concurrency)
- [ ] T18: `/ready` checks Redis + ≥1 healthy supervisor replica
- [ ] T19: NetworkPolicy — only the gateway may reach the supervisor's `/internal/*`

## Phase 5 — Docs + validation
- [ ] T20: `docs/ARCHITECTURE.md` (or site) — two-tier topology + gateway↔supervisor contract + concurrency model (local vs global)
- [ ] T21: Reuse spec 25 k6 load test against the gateway; confirm backpressure (503s under burst, no OOM) + p99
- [ ] T22: Update `docs/METRICS.md` with gateway metrics
- [ ] T23: Code review (independent) of the whole gateway

## Phase 1 status table (update when implementing)
| Task | State | Note |
|------|-------|------|
| T1–T23 | ❌ not started | spec written; implementation pending design-review sign-off |

## Dependencies / sequencing
- **Must follow** (or co-deliver with) spec 25 Phase 1–2 — the gateway's global
  admission guard reuses spec 25's `RateLimiter`/`BudgetGuard` and Redis circuit
  breaker. If 25 isn't done, T13–T15 build the Redis guards here and 25 reuses them.
- Spec 14 (guardrail) stays backend-side — no change needed here, just don't regress.
- Spec 29 (`/v1`) routes are **moved**, not rewritten.

## Open questions for design review (confirm with user)
1. Co-deliver with spec 25, or land 31 first and treat 25's guards as gateway-owned?
2. Internal supervisor auth now (shared token) vs. defer to Istio mTLS (when mesh lands)?
3. Local pool size + job timeout defaults (ChaitOps: pool=`max_concurrent_jobs`, timeout configurable).
