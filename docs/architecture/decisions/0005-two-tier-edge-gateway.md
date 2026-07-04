# ADR-0005: A thin edge gateway fronts the supervisor (two-tier); the supervisor becomes backend-only

| Field | Value |
|---|---|
| **Status** | accepted |
| **Date** | 2026-06-22 (round-table; cluster-validated 2026-07-01; backfilled 2026-07-03) |
| **Deciders** | Carlos Felipe Gomes (round-table: dev + security + sre + gitops personas, 4/4) |
| **Related to** | spec 31 (edge gateway + worker pool), spec 29 (OpenAI bridge), spec 25 (framing corrected), `docs/site/architecture.md` |

## Context

`supervisor/server.py` was simultaneously the public HTTP front door and the
orchestration engine. Fine single-replica; broken for the explicit
multi-replica goal: no admission control (a flood is accepted until OOM
instead of a clean 503), client protocols (OpenAI `/v1`, future Slack/Teams)
bleeding into the orchestrator, and one coarse scaling unit for two very
different load profiles (cheap IO-bound edge vs Bedrock-bound orchestration).
The sibling `staffops-chaitops` `agent-api` had already proven the
gateway-only shape, and reuse of its patterns was authorized.

## Decision

Introduce `src/gateway/` as a **separate thin service** (public `:8000`):
edge auth, WorkerPool backpressure (local `asyncio.Semaphore(20)`,
`PoolFullError → 503 + Retry-After`), global rate/budget admission (Redis),
protocol shaping (native `/query` + OpenAI `/v1/*` + `/jobs/{id}/cancel`).
The supervisor becomes backend-only (`:8001`) exposing `/internal/process` +
`/internal/agents`, gated by a **distinct** `SUPERVISOR_INTERNAL_TOKEN` +
NetworkPolicy. Key boundary rules: the gateway holds **no orchestration
logic** and **never evaluates the guardrail** (injection defense stays at the
supervisor, applied to every `/internal/process` call regardless of caller);
local concurrency caps are per-replica in-memory, account-wide limits
($ budget/TPS) are Redis-coordinated — two mechanisms on purpose.

## Alternatives considered

- **Keep one process** (gateway as a module) — rejected: admission competes
  with orchestration for the same event loop; every new client protocol
  touches the orchestrator; single HPA signal for two load profiles. Noted:
  for a permanently single-replica supervisor this split WOULD be
  over-engineering — the multi-replica goal is the justifying trigger.
- **Message queue between tiers (Kafka/SQS)** — rejected: request/response
  workload (a human waits); bounded pool + `503 Retry-After` is sufficient;
  a durable queue adds latency and ops weight (same call chaitops made).
- **Global (Redis) semaphore for local concurrency** — rejected: pod
  self-protection must survive a Redis outage; only shared-resource limits
  need coordination (this also corrected spec 25's framing, whose in-memory
  semaphore could not bound a global TPS).

## Consequences

- **Positive:** clean backpressure before the expensive tier; independent
  scaling (gateway on RPS, supervisor on Bedrock concurrency); protocol
  isolation (new clients touch only the gateway); supervisor public surface
  shrinks to an internal contract. Cluster-validated end-to-end 2026-07-01.
- **Negative / trade-offs:** one extra network hop and failure mode (handled
  by preflight + decoupled readiness — gateway `/ready` deliberately does NOT
  check supervisor health, avoiding a removed-from-LB cascade); two services
  to deploy and operate.
- **To watch:** per-agent pool lanes deferred (if ever needed: a 2-lane
  priority pool, not N pools); true token streaming still pending (gateway
  forwards whatever the supervisor emits).
