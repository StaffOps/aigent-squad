# Feature: Edge Gateway + Worker Pool (multi-replica supervisor front door)

**Spec**: `31-edge-gateway-worker-pool`
**Severity**: 🟠 High (prerequisite for scaling the supervisor beyond a single replica)
**Depends on**: `25-multi-tenant-concurrency` (distributed state), `29-openai-compat-bridge` (the `/v1` surface this gateway will front)
**Reference architecture**: `staffops-chaitops` (`agent-api/` — gateway-only FastAPI + `WorkerPool` + channel adapters). ChaitOps is a BDC-internal project; reuse of its patterns/code is explicitly permitted by the user (exception to `licensing-clean-room`).

---

## Thesis (why a separate edge)

Today `src/supervisor/server.py` is **both** the HTTP front door **and** the
orchestration engine, in one process. That is fine for a single replica. The
moment the supervisor becomes multi-replica (an explicit goal), coupling the two
creates problems:

- **No backpressure**: a flood of requests has no admission control — the pod
  accepts work until it OOMs or the event loop stalls, instead of shedding load
  with a clean `503 + Retry-After`.
- **Client protocol bleeds into orchestration**: the OpenAI `/v1` shape, channel
  quirks (Slack/Teams), auth, and rate-limit all live next to the routing/fan-out
  logic. Each new client touches the orchestrator.
- **Scaling unit is coarse**: the edge (cheap, stateless, IO-bound) and the
  orchestrator (Bedrock-bound, expensive) scale together even though their load
  profiles differ.

This spec introduces a **thin edge gateway** in front of the supervisor:
admission control (worker pool + backpressure), auth, rate-limit, client protocol
translation (OpenAI `/v1`, native), and clean degradation. The supervisor becomes
a **backend service** the gateway calls — and can scale independently.

> ChaitOps already runs exactly this shape (`agent-api` is "gateway only — no
> CLIs"; orchestration lives behind it). We adopt its `WorkerPool`, preflight,
> and backpressure patterns, adapted to our Bedrock-direct (not sidecar) backend.

## Relationship to specs 25 and 29 (boundaries — avoid overlap)

| Concern | Owner | Note |
|---------|-------|------|
| OpenAI `/v1` request/response shaping | **29** (done) | The gateway hosts these routes; shaping stays in `openai_compat.py` |
| Distributed circuit breaker (Bedrock) | **25** | Stays in the supervisor backend (closest to Bedrock) |
| Session lock per `session_id` | **25** | Stays in the supervisor backend (guards history writes) |
| **Global** budget/rate (account-wide $/TPS) | **25 + 31** | Logic from 25; **enforced at the gateway** (admission) per this spec |
| **Local** concurrency cap (pod resource) | **31** | New: worker pool semaphore at the gateway |
| Backpressure (`503` when full) | **31** | New |
| Per-replica admission + job lifecycle | **31** | New: `WorkerPool` (ChaitOps pattern) |
| Edge auth (API key / token) | **31** | Consolidated at the gateway; supervisor trusts the gateway |

This spec does **not** re-implement the circuit breaker or session lock (those
are 25, backend-side). It owns the **front door**: admission, backpressure,
protocol, auth, and the gateway↔supervisor contract.

## User Stories

WHEN the gateway is at its local concurrency capacity THEN it SHALL reject new
work with `503 Service Unavailable` + `Retry-After` (backpressure), NOT queue
unboundedly nor OOM.

WHEN a request would exceed the **global** daily budget or account TPS ceiling
THEN the gateway SHALL refuse at admission (`429`/`503`) BEFORE forwarding to the
supervisor — coordinated across all gateway replicas via Redis.

WHEN a client speaks the OpenAI protocol (LibreChat) THEN the gateway SHALL
translate to/from the supervisor's native contract without the supervisor knowing
about OpenAI.

WHEN the supervisor scales to N replicas THEN the gateway SHALL distribute work
across them (stateless forwarding; no sticky sessions) and survive a replica
going unhealthy (readiness-gated).

WHEN an in-flight job must be cancelled THEN the gateway SHALL signal cancellation
(Redis key) and the worker SHALL stop within ~500ms.

WHEN the gateway or supervisor restarts THEN no session state is lost (state lives
in Redis/DynamoDB, per spec 25), and in-flight jobs fail cleanly (no zombie work).

## Acceptance Criteria

- [ ] **Gateway service** (`src/gateway/`): FastAPI front door, no orchestration
      logic. Hosts native `/query`, OpenAI `/v1/*` (delegating to spec 29 shaping),
      health probes, and admission control.
- [ ] **WorkerPool** (ChaitOps pattern): bounded local concurrency (semaphore),
      cancellation via Redis key polling, per-job timeout, `PoolFullError → 503`.
- [ ] **Preflight checks** before streaming starts (return clean HTTP errors, not
      mid-stream failures): supervisor reachable (503), agent known (404), budget
      ok (429/503), pool has capacity (503).
- [ ] **Global admission guards** at the gateway: per-user rate + global daily
      budget (Redis, from spec 25 logic), enforced before forwarding.
- [ ] **Local concurrency cap**: in-memory semaphore for pod resource protection
      (distinct from the global guard — see design Rationale).
- [ ] **Gateway↔supervisor contract**: the supervisor exposes an internal
      `process` endpoint; the gateway forwards (HTTP, streaming-capable). Supervisor
      trusts the gateway's internal token (mesh/mTLS later).
- [ ] **Job lifecycle in Redis**: `job:<id>` status, `cancel:<id>` signal, `audit`
      trail (ChaitOps shape).
- [ ] **Backpressure metric**: `aigent.gateway.pool_rejections` (counter),
      `aigent.gateway.pool_depth` (gauge), `aigent.gateway.queue_wait` (histogram).
- [ ] **Health**: `/healthz` (liveness, no deps), `/ready` (readiness — checks
      Redis + ≥1 healthy supervisor replica).
- [ ] **Helm**: two Deployments (gateway, supervisor) with independent HPA; gateway
      Service fronts the supervisor Service.
- [ ] **Read-only + fail-closed preserved**: guardrail (spec 14) stays in the
      supervisor backend (closest to Bedrock); the gateway never weakens it.
- [ ] **≥90% test coverage** on new gateway code (per `dev-environment`).
- [ ] **Docs**: `docs/ARCHITECTURE.md` (or site) updated with the two-tier topology
      + the gateway↔supervisor contract.

## Out of scope

- Replacing the supervisor's internal orchestration (routing/fan-out/investigation
  unchanged — the gateway forwards to it).
- True token streaming (spec 06) — gateway forwards whatever the supervisor emits.
- Channel adapters themselves (Slack/Teams/Discord) — separate spec; this spec only
  guarantees they can sit in front of the gateway (HTTP client, like ChaitOps
  `channels/common/agent_api_client`).
- A persistent job queue (Kafka/SQS) — backpressure via bounded pool is enough for
  this load profile (same call ChaitOps made).
- Distributed **local** semaphore — local cap is intentionally per-replica (see
  Rationale); only budget/TPS is global.

## Declared trade-offs (to be confirmed by the user in design review)

- **Extra network hop** gateway→supervisor (+latency, +1 failure point) in exchange
  for independent scaling, backpressure, and protocol isolation. Justified only
  because the supervisor is going multi-replica; for a single replica it would be
  over-engineering.
- **Local concurrency cap is per-replica, not global** — protects pod resources,
  not account limits. Global limits (budget/TPS) are enforced separately via Redis.
- **Two services to operate** instead of one — more Helm/CI surface, justified by
  the scaling-unit separation.
