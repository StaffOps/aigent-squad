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
- [ ] T19b: Cutover from single-process to two-tier via standard Deployment rolling update — deploy gateway + supervisor side-by-side, switch the Ingress/Service backend to the gateway, then drop the supervisor's public exposure. Rollback = point the Ingress back. (No Argo Rollout — plain rolling update.)
- [ ] T19c: Rewire `mcp-server/mcp-server.py` default `SUPERVISOR_URL` to the **gateway** `/query` (was the supervisor :8000/query, which no longer exists — supervisor is :8001 `/internal/*` only). Local docker-compose: add gateway service, point mcp-server + LibreChat at it.
- [ ] T19d: (hardening) Make `AdmissionGuard.check_budget` atomic via an EVAL/Lua check-and-reserve (closes the GET→compare→INCR TOCTOU; code-review 2026-06-22). Deferred from L3 because the test fakeredis lacks `eval`; needs a real-Redis integration test.

## Phase 5 — Docs + validation
- [ ] T20: `docs/ARCHITECTURE.md` (or site) — two-tier topology + contract + 3-layer link security + concurrency model (local vs global)
- [ ] T21: Reuse spec 25 k6 load test against the gateway; confirm backpressure (503s under burst, no OOM) + p99
- [ ] T22: Update `docs/METRICS.md` with gateway metrics
- [ ] T23: Code review (independent) of the whole gateway

## Phase 1 status table (update when implementing)
| Task | State | Note |
|------|-------|------|
| T1 (gateway package) | ✅ done | `src/gateway/` — main, worker_pool, supervisor_client, auth; `setup_telemetry()` first |
| T2 (`/internal/process`) | ✅ done | supervisor, `require_internal_token` gated, fail-closed, guardrail 403 preserved |
| T3 (move /query + /v1) | ✅ done | moved to gateway; supervisor public surface = `/internal/*` + health + kb/alerts; supervisor now on :8001 |
| T4 (supervisor_client) | ✅ done | httpx, `is_supervisor_ready` preflight, `process`, `list_agents`, `max_connections=max+5` |
| T5 (edge auth) | ✅ done | `require_edge_auth` — `INTERNAL_API_TOKEN` or `GATEWAY_API_KEYS` allowlist, fail-closed |
| T5b (internal token) | ✅ done (app) | `SUPERVISOR_INTERNAL_TOKEN` config + `internal_auth`; ExternalSecret manifest pending (L4) |
| T5c (NetworkPolicy) | ⬜ L4 | manifest pending (Helm phase) |
| T6 (tests L1) | ✅ done | independent author; covered in gateway suite |
| T7/T7b (worker pool) | ✅ done | `Semaphore(20)`, `PoolFullError`, first-byte(15s)/idle(10s)/job(45s), cancel via Redis poll |
| T8/T8b (backpressure) | ✅ done | 503 + dynamic Retry-After (jitter); `service_overloaded`/`backend_unavailable`; httpx pool sized |
| T9 (job lifecycle) | ✅ done | Redis `job:<id>`, fail-open to log + `redis_fallback_active` metric |
| T10 (`/jobs/{id}/cancel`) | ✅ done | 202 cancelling / 404 not-found |
| T11 (metrics) | ✅ done | `gateway.pool_rejections`/`pool_depth`/`queue_wait`/`redis_fallback_active` |
| T12 (tests L2) | ✅ done | 62 tests, **92% coverage** (internal_auth 100%, auth 100%, main 93%, client 97%, pool 89%); code-review APPROVE-WITH-NITS (nits fixed) |
| T13–T15 (admission L3) | ✅ done | `src/core/rate_limiter.py` (`AdmissionGuard` + `estimate_cost`, fail-open, global rate+budget); wired into both gateway routes before pool/preflight; 429 rate / 503 budget with headers; `rate_limit.blocks` metric. 100% coverage on rate_limiter; main.py 99%. Independent author + review (APPROVE-WITH-NITS). Budget TOCTOU hardening → T19d |
| T16–T19b (deploy L4) | 🔶 mostly done | Helm `helm/aigent-squad/` — 2 Deployments + 2 Services (gateway public, supervisor internal), KEDA per tier, NetworkPolicy (supervisor:8001 ⇐ gateway only), ExternalSecret for `SUPERVISOR_INTERNAL_TOKEN`. docker-compose two-tier + mcp-server repointed to gateway (T19c). helm lint clean, 13 manifests. Pending: real-cluster install test; CostCenter is a placeholder default (confirm value) |
| T20–T23 (docs/validation L5) | ⬜ pending | architecture docs, k6, metrics doc, final review |

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
