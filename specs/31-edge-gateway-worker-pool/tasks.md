# Tasks: Edge Gateway + Worker Pool

Order by structural risk. L1 is the big move (security ships WITH it, day-1); later
layers add admission, scaling, validation. Reuses `staffops-chaitops` patterns
(authorized). Round-table sign-off 2026-06-22 (dev + security + sre + gitops).

## Phase 1 — Extract the gateway service (structural + security day-1)
- [ ] T1: Create `src/gateway/` package (FastAPI app, config, health probes). Call `setup_telemetry()` BEFORE any `src.core.*` import (silent no-export trap otherwise)
- [ ] T2: Add `POST /internal/process` to the supervisor wrapping `process_request` (gated by `SUPERVISOR_INTERNAL_TOKEN`, fail-closed)
- [ ] T3: Move `/query` + `/v1/*` routes from `supervisor/server.py` to the gateway; import spec 29 `openai_compat` shaping in the gateway
- [ ] T4: `gateway/supervisor_client.py` — HTTP+SSE client with preflight (supervisor reachable? agent known?)
- [ ] T5: `gateway/auth.py` — edge auth (API key allowlist / `INTERNAL_API_TOKEN`)
- [ ] T5b: (day-1 security) `SUPERVISOR_INTERNAL_TOKEN` as ExternalSecret, distinct from `INTERNAL_API_TOKEN`, mounted as file (mode 0400)
- [ ] T5c: (day-1 security) NetworkPolicy — supervisor `/internal/*` ingress only from gateway; gateway ingress only from ingress-controller
- [ ] T6: Tests T1–T5c (independent author) — routes forward, preflight returns clean 404/503, supervisor refuses without internal token

## Phase 2 — Worker pool + backpressure (ChaitOps pattern)
- [ ] T7: `gateway/worker_pool.py` — global `asyncio.Semaphore(20)`, `PoolFullError`, cancel via `cancel:<job_id>` Redis poll (500ms), `wait_for` job timeout (45s)
- [ ] T7b: Two-timeout model — first-byte (15s) + idle-stream (10s) on top of the 45s hard backstop (idle timeout is the real stall guard)
- [ ] T8: Wire pool into forward path; `PoolFullError → 503 + dynamic Retry-After (jitter)`; body distinguishes `service_overloaded` vs `backend_unavailable`
- [ ] T8b: httpx client `max_connections = max_concurrent + 5` (avoid hidden backpressure)
- [ ] T9: Job lifecycle in Redis (`job:<id>`, `audit`) — ChaitOps shape; log-based fallback + `aigent.gateway.redis_fallback_active` metric when Redis down
- [ ] T10: `POST /jobs/{id}/cancel` endpoint
- [ ] T11: Metrics `aigent.gateway.pool_rejections`/`pool_depth`/`queue_wait`
- [ ] T12: Tests (independent author) — backpressure 503, cancel within ~500ms, first-byte/idle/job timeouts

## Phase 3 — Global admission guards (shared module in src/core/)
- [ ] T13: `src/core/rate_limiter.py` + `budget_guard.py` (Redis sliding window + daily counter). In `src/core/` so spec 25 reuses, either landing order
- [ ] T14: Enforce at gateway admission BEFORE forward; `429`/`503` with `X-RateLimit-Remaining`/`X-Budget-Remaining-USD`; fail-open on Redis down
- [ ] T15: Tests (independent author) — rate block, budget block, fail-open on Redis down

## Phase 4 — Deploy (two-tier)
- [ ] T16: Helm — split into `gateway` + `supervisor` Deployments + Services (same image, `command` override per tier); gateway fronts supervisor
- [ ] T17: Independent HPA/KEDA per tier (gateway on RPS, supervisor on Bedrock concurrency)
- [ ] T18: `/ready` checks Redis + pool functional ONLY (NOT supervisor health — decoupled to avoid cascade)
- [ ] T19: (NetworkPolicy already day-1 via T5c)
- [ ] T19b: Argo Rollout canary Ingress cutover (20→50→100%); rollback = revert Ingress backend

## Phase 5 — Docs + validation
- [ ] T20: `docs/ARCHITECTURE.md` (or site) — two-tier topology + contract + 3-layer link security + concurrency model (local vs global)
- [ ] T21: Reuse spec 25 k6 load test against the gateway; confirm backpressure (503s under burst, no OOM) + p99
- [ ] T22: Update `docs/METRICS.md` with gateway metrics
- [ ] T23: Code review (independent) of the whole gateway

## Phase 1 status table (update when implementing)
| Task | State | Note |
|------|-------|------|
| T1–T23 | ❌ not started | spec + round-table sign-off (2026-06-22); ready for L1 |

## Dependencies / sequencing
- **Land spec 31 first (L1–L2)** — round-table consensus (4/4). No spec-25 dependency
  in L1–L2. Spec 25 can land before/during/after; shared guards live in `src/core/`.
- Spec 14 (guardrail) stays backend-side — no change needed, just don't regress.
- Spec 29 (`/v1`) routes are moved, not rewritten.

## Open questions — RESOLVED (round-table 2026-06-22)
1. **Co-deliver with spec 25?** → No. Land 31 first (L1–L2). Shared admission modules
   go in `src/core/` so order doesn't matter. (4/4 consensus)
2. **Internal auth?** → NetworkPolicy + dedicated `SUPERVISOR_INTERNAL_TOKEN` day-1
   (file-mounted, fail-closed). Promote to Istio mTLS later (additive); keep the token
   post-mTLS (defense-in-depth). (4/4 + security elevation)
3. **Pool defaults?** → Global pool, `max_concurrent=20`, immediate-reject (no queue),
   `job_timeout=45s` + `first_byte=15s` + `idle=10s`. Per-agent pools deferred.
   (resolved dev-vs-sre: 45s wins; idle timeout is the real stall guard)

## Estimated effort (round-table): ~10.5 dev-days (L1=4, L2=2, L3=1.5, L4=2, L5=1)
