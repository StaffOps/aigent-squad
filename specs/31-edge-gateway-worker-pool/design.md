# Design: Edge Gateway + Worker Pool

## Architecture (two-tier)

```
        Clients
   ┌───────┬──────────┬───────────┐
LibreChat  Slack    native      (Teams/Discord later)
 (/v1)     adapter   curl
   └───────┴────┬─────┴───────────┘
                ▼
   ┌─────────────────────────────────────────────┐
   │  GATEWAY (N replicas, stateless, IO-bound)   │
   │  src/gateway/                                │
   │   • auth (X-API-Key / internal token)        │
   │   • protocol: native /query + OpenAI /v1/*   │  ── shaping delegated
   │   • global admission (rate + budget) ◄─Redis │     to spec 29 module
   │   • WorkerPool (LOCAL semaphore) ── backpressure → 503
   │   • preflight (supervisor up? agent known?)  │
   │   • forward (HTTP + SSE) ───────────┐        │
   └─────────────────────────────────────┼────────┘
                                          ▼
   ┌─────────────────────────────────────────────┐
   │  SUPERVISOR (M replicas, Bedrock-bound)      │
   │  src/supervisor/  (existing orchestration)   │
   │   • POST /internal/process (new contract)    │
   │   • classify / fan-out / investigation       │
   │   • guardrail (spec 14) — fail-closed        │
   │   • circuit breaker + session lock (spec 25) ◄─Redis
   │   • bedrock.invoke                           │
   └─────────────────────────────────────────────┘
                    │                    │
                    ▼                    ▼
                 Bedrock          Redis + DynamoDB
                                  (state, sessions, history)
```

Both tiers are stateless (state in Redis/DynamoDB), so both scale horizontally
and survive restarts. The gateway is cheap to replicate (no Bedrock, no heavy
deps); the supervisor scales on Bedrock throughput.

## Components

| # | Component | Where | Responsibility |
|---|-----------|-------|----------------|
| G1 | `gateway/main.py` | gateway | FastAPI app, routes, lifespan |
| G2 | `gateway/worker_pool.py` | gateway | Local bounded concurrency + cancel + timeout + backpressure (ChaitOps pattern) |
| G3 | `gateway/admission.py` | gateway | Global rate + budget pre-check (Redis; reuses spec 25 `RateLimiter`) |
| G4 | `gateway/supervisor_client.py` | gateway | HTTP/SSE client to the supervisor backend (preflight + forward) |
| G5 | `gateway/auth.py` | gateway | Edge auth (API key allowlist / internal token) |
| S1 | `supervisor/server.py` `POST /internal/process` | supervisor | Internal contract the gateway calls; wraps existing `process_request` |

The OpenAI `/v1` shaping (`openai_compat.py`, spec 29) **moves to / is imported
by** the gateway — it is a protocol concern, not orchestration. The supervisor's
`process_request` is unchanged.

## Rationale (decisions and trade-offs)

### Decision 1: Local concurrency cap is per-replica; global limits are Redis-coordinated

**Choice**: the `WorkerPool` semaphore that gates concurrent jobs is an
**in-memory, per-replica** `asyncio.Semaphore` (ChaitOps pattern). Account-wide
limits (daily $ budget, Bedrock TPS ceiling) are enforced **separately** via Redis
counters at admission.

**Justification, in order of strength**:
1. **The two limits protect different things.** The local cap protects *this pod's*
   resources (event loop, memory, file descriptors) — inherently a per-replica
   concern; coordinating it globally adds a Redis round-trip to every job for zero
   benefit. The budget/TPS cap protects a *shared account resource* — it is
   meaningless per-replica (3 replicas each thinking they have the full TPS will
   collectively blow it), so it **must** be global.
2. **Fail-open story differs.** A local semaphore never depends on Redis, so pod
   self-protection survives a Redis outage. The global guard degrades to fail-open
   on Redis loss (per spec 25) — acceptable for budget (you might slightly
   overspend during an outage) but never silently drops pod protection.
3. **Matches the reference.** ChaitOps proved the per-replica pool in production
   shape; we add the global guard it lacks, rather than rebuilding its pool.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Local cap doesn't bound *total* in-flight jobs across replicas | That's the global budget/TPS guard's job — covered separately |
| Two distinct concurrency mechanisms to understand | They have distinct purposes; conflating them is the actual mistake (spec 25 currently does this — see "fixes" below) |
| Global guard adds ~1-5ms Redis latency per request | Negligible vs. multi-second Bedrock calls |

**When this would be wrong**: if total in-flight work (not $/TPS) must be hard-capped
across the fleet (e.g. a downstream with a strict global connection limit). Then a
distributed semaphore (Redis token bucket) becomes necessary — added as an L2 guard,
not a replacement for the local one.

**Fix to spec 25**: spec 25 uses an in-memory `asyncio.Semaphore` for Bedrock but
justifies it as "avoids exceeding TPS limit" — that justification is wrong under
multi-replica (in-memory can't bound a global limit). This spec corrects the framing:
the local semaphore is pod self-protection; TPS/budget enforcement is the Redis guard.

### Decision 2: Separate gateway service vs. keep one process

**Choice**: a distinct gateway service in front of the supervisor (two
Deployments), not a module inside the supervisor process.

**Justification, in order of strength**:
1. **Independent scaling units.** The edge is cheap/stateless/IO-bound; the
   supervisor is expensive/Bedrock-bound. Separate Deployments let each HPA on its
   own signal (gateway on RPS, supervisor on Bedrock concurrency).
2. **Backpressure needs an admission point that isn't doing the expensive work.**
   A pool inside the orchestrator competes with orchestration for the event loop;
   a thin gateway sheds load before the heavy process is touched.
3. **Protocol isolation.** New clients (OpenAI, Slack, Teams) touch only the
   gateway; the supervisor contract stays stable (`project.md`: server transport-only).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Extra network hop (+latency, +1 failure mode) | Negligible vs Bedrock latency; the failure mode is handled by preflight + readiness |
| Two services to deploy/operate | Justified only because the supervisor is going multi-replica; documented as the trigger |

**When this would be wrong**: while the supervisor is a **single replica**, this is
over-engineering — the current in-process `/v1` is enough. The split is gated on the
multi-replica decision (which the user confirmed is coming).

### Decision 3: Synchronous HTTP forward (gateway→supervisor), not a message queue

**Choice**: the gateway forwards over HTTP (streaming-capable), backed by the local
worker pool for admission. No Kafka/SQS between the tiers.

**Justification**:
1. The interaction is request/response (a user waits for an answer) — a durable
   queue adds latency and operational weight for a workload that isn't fire-and-forget.
2. Backpressure via bounded pool + `503 Retry-After` is sufficient for this load
   profile (same call ChaitOps made; spec 25 also rejects persistent queues as
   "exagero pra esse perfil de uso").

**When this would be wrong**: if requests become long-running async jobs (minutes)
that should survive a gateway restart — then a durable queue + job-result polling
is justified (a future spec).

### Decision 4: Reuse ChaitOps `WorkerPool` shape (cancel via Redis key polling)

**Choice**: adopt ChaitOps' cancellation model — a `cancel:<job_id>` Redis key the
worker polls every ~500ms, plus `asyncio.wait_for` timeout and `PoolFullError`.

**Justification**:
1. It's proven in a sibling <ORG> project with the same constraints (FastAPI + SSE +
   Redis), and the user authorized reuse.
2. Redis-key cancellation works across replicas (the cancel can arrive at any
   gateway replica), which a pure in-process `Task.cancel()` cannot.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| ~500ms cancellation latency (poll interval) | Acceptable for human-interactive cancel; tunable |
| A poll task per in-flight job | Cheap; bounded by the pool size |

## Invariants

- The gateway holds **no orchestration logic** (routing/fan-out/investigation live
  only in the supervisor).
- **The gateway SHALL NOT evaluate the guardrail nor hold guardrail credentials.**
  The supervisor evaluates the guardrail (spec 14, fail-closed) on **every**
  `/internal/process` call, regardless of caller identity — so even a request that
  somehow bypasses NP/token still gets guardrail-checked. The trust boundary for
  *injection defense* is the supervisor, not the gateway.
- Local pool semaphore never depends on Redis (pod self-protection survives Redis
  outage); global guard fails open per spec 25.
- Backpressure returns `503 + Retry-After`; the gateway never queues unboundedly.
- **Gateway readiness is decoupled from supervisor health**: `/ready` = Redis
  reachable + pool functional. Supervisor availability is handled at request-time
  (preflight → 503), NOT in the readiness probe — coupling them would turn a
  supervisor outage into a gateway-removed-from-LB cascade, defeating the split.
- State (sessions, jobs, locks, budget) lives in Redis/DynamoDB — both tiers
  stateless and restart-safe.
- `/healthz` never checks external deps; `/ready` does.

## Gateway ↔ supervisor contract

```
POST /internal/process            (supervisor, gateway-only, internal token)
  body: { user_input, user_id, session_id, mode?, force_agent? }
  resp: streamed SSE (forwarded as-is) | JSON {agent, response, confidence, ...}
```

The supervisor keeps `process_request` untouched; `/internal/process` is a thin
wrapper. The existing public `/query` and `/v1/*` routes **move to the gateway**;
the supervisor's public surface shrinks to the internal contract + health.

## Security: gateway ↔ supervisor link (round-table 2026-06-22)

Three independent layers — the link **trusts no single control alone**:

| Layer | Control | When |
|-------|---------|------|
| Network | NetworkPolicy: supervisor `/internal/*` ingress only from `app.kubernetes.io/name: gateway`; deny-all else | **Day 1** |
| AuthZ | `SUPERVISOR_INTERNAL_TOKEN` — a **distinct** secret from `INTERNAL_API_TOKEN`, mounted as file (mode 0400, per `cloud-security`/`12-factor`), fail-closed if missing | **Day 1** |
| Identity/encryption | Istio Ambient mTLS + `AuthorizationPolicy` restricting `/internal/*` to the gateway ServiceAccount (SPIFFE) | When mesh lands (additive) |

The token is **kept after** mTLS lands (defense-in-depth: mTLS = identity at L4;
token = authorization intent at L7). A ztunnel crash or mesh misconfig must not
silently open the privileged endpoint. NetworkPolicy is independent of mesh health.

## Resolved defaults — worker pool + timeouts (round-table 2026-06-22)

| Parameter | Value | Reason |
|-----------|-------|--------|
| `max_concurrent` (per replica) | **20** | IO-bound edge; ~matches 2 supervisor replicas × 10 Bedrock slots |
| Pool shape | **global, immediate-reject** | No per-agent pools, no queue — any queue adds tail latency. `PoolFullError → 503` |
| `job_timeout` (hard backstop) | **45s** | Investigation p99 ~30s → 50% headroom; 120s would hold zombie slots too long. Stalls caught earlier by idle timeout |
| `first_byte_timeout` | **15s** | Supervisor must start producing within 15s |
| `idle_stream_timeout` | **10s** | No data for 10s mid-stream → abort, free the slot (the real stall protection) |
| `cancel_poll_interval` | **500ms** | ChaitOps reference |
| `Retry-After` | **dynamic + jitter** `ceil(active/max × avg_latency) + random(0,3)` | Avoid thundering-herd retries |
| httpx `max_connections` | **`max_concurrent + 5`** | Prevent hidden backpressure below the semaphore |

503 response distinguishes subtypes in the body: `service_overloaded` (pool full,
self-healing) vs `backend_unavailable` (supervisor unreachable, needs action).

Per-agent pools are deferred; if ever needed, the shape is a **priority lane**
(2 pools), not N per-agent pools — a separate spec.

## Shared-module placement (avoids spec-25 coupling)

`RateLimiter` / `BudgetGuard` live in **`src/core/`** (not `src/gateway/` nor
`src/supervisor/`), so spec 31 and spec 25 can land in either order without
circular imports. The gateway imports them for admission; if spec 25 hasn't
landed, spec 31 L3 implements them in `src/core/` (~150 LoC) and spec 25 reuses.

## External dependencies

| Service | Purpose |
|---------|---------|
| Redis | Worker pool job state, cancel signal, global rate/budget counters, audit |
| Supervisor service | The orchestration backend the gateway forwards to |
| (DynamoDB, Bedrock) | Unchanged — used by the supervisor, not the gateway |

## Phases (incremental — see tasks.md)

L1: extract gateway service + move `/query` and `/v1/*` + internal contract (biggest
structural change, no new behavior). → L2: WorkerPool + backpressure. → L3: global
admission guards at the gateway (wire spec 25 RateLimiter). → L4: Helm two-tier +
independent HPA. → L5: docs + load-test validation (overlaps spec 25 k6).
