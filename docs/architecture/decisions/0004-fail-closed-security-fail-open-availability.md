# ADR-0004: Security layers fail closed; availability layers fail open

| Field | Value |
|---|---|
| **Status** | accepted |
| **Date** | 2026-06-22 (round-table; backfilled 2026-07-03) |
| **Deciders** | Carlos Felipe Gomes (round-table: dev + security + sre + gitops personas) |
| **Related to** | spec 14 Decision 2 (fail-closed), spec 06 (fail-open resilience), spec 31 L3 (admission guards), `docs/SECURITY.md` §S4 |

## Context

Two shipped principles collided. Spec 06 made the system **fail-open**: Redis
or DynamoDB down must degrade (cache miss / empty history), never outage —
"dependencies fail closed" was the original CONV-2 defect. Spec 14 demanded
the opposite for its guardrail: bypassing security under failure destroys the
product's trust guarantee. Spec 31 then added Redis-backed rate/budget guards
at the gateway, forcing the question: which failure semantics apply to which
component?

## Decision

Split by what the component protects:

- **Security components fail CLOSED** → HTTP 403, never bypass: Bedrock
  Guardrail (L1), InputScanner (L2), OutputFilter (L4), canary detection (L5),
  and both auth tokens (edge + supervisor-internal). A blocked or *unavailable*
  security layer refuses the request. Security refusals do NOT trip the
  Bedrock circuit breaker (a refusal is not a fault).
- **Availability components fail OPEN** → degrade and continue: datasource
  cache, conversation history, rate limit + daily budget (L6), worker-pool job
  lifecycle in Redis (falls back to log-only + `redis_fallback_active` metric).

## Alternatives considered

- **Fail-open everywhere (availability-first, the competitors' posture)** —
  rejected: a guardrail that bypasses under failure is a guarantee that
  evaporates exactly under attack (DoS the guardrail → free pass). We are
  consultative, not the critical remediation path — unavailability is
  survivable, silent bypass is not.
- **Fail-closed everywhere** — rejected: re-creates the self-inflicted-outage
  defect (CONV-2); losing Redis would take down a system whose cache/history
  are accelerators, not sources of truth.
- **Fail-closed budget/rate** — considered for L6; rejected: over-spend during
  a Redis outage is bounded and monetary, blocking all traffic is not
  proportional. Explicitly reconciled at the 2026-06-22 round-table.

## Consequences

- **Positive:** each failure mode matches what the component protects; the
  contrast is documented in-module (`rate_limiter.py` vs `guardrail.py`) and
  is testable (both paths covered in CI).
- **Negative / trade-offs:** reduced availability under guardrail outage
  (accepted, spec 14); possible bounded over-spend under Redis outage
  (accepted, spec 31).
- **To watch:** guardrail false-positive rate — sustained FP blocking
  legitimate ops queries triggers the documented fallback (own detector as
  L1 alternative). Homologation findings A/B/D (spec 14 tasks) are gaps in
  *where* fail-closed applies (entry points), not in this principle.
