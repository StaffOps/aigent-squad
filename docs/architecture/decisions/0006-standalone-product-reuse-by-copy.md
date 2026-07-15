# ADR-0006: AIgent-squad stays a standalone product; ecosystem patterns are reused by copy, never by dependency

| Field | Value |
|---|---|
| **Status** | accepted |
| **Date** | 2026-06-02 (backfilled 2026-07-03) |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | `specs/ECOSYSTEM.md` (full analysis + decision record), `steering/licensing-clean-room.md`, specs 14/17/18/21/31 |

## Context

The StaffOps ecosystem already contained `staffops-chaitops` (mature agent
platform: gateway, worker pool, budget guard, distillation→KB flow, 186
tests, CI, OTel) and `staffops-anomaly-detection` (correlation engine + ML
that produces enriched RCA triggers). Much of what AIgent-squad planned
(specs 14/17/18/19/21) existed in more mature *design* form in chaitops.
Continuing standalone meant consciously duplicating platform work; merging
meant abandoning a distinct product identity. What chaitops does NOT have:
domain specialists with **direct datasource access** (boto3, K8s API,
Prometheus, GitLab, Athena) and a read-only-by-architecture posture — exactly
AIgent-squad's core.

## Decision

Keep AIgent-squad a **separate product**. Integrate with the ecosystem over
plain HTTP when needed. Reuse chaitops assets **by copy of pattern/design,
never by dependency** — no shared libraries, no runtime coupling. Concrete
reuses executed since: WorkerPool/backpressure shape and gateway-only pattern
(spec 31), `KbDelta` schema + confidence thresholds (spec 21), auth-via-flag
pattern (spec 14/04), test-harness approach (fakeredis/respx, spec 23). For
third-party code the clean-room rule applies: learn patterns, implement from
scratch, dependencies only via package manager.

## Alternatives considered

- **Merge into chaitops** (squad becomes its "internal agents" layer) — the
  analysis itself rated standalone as the costlier option; rejected by
  product decision: distinct identity, deploy surface and pace matter more
  than deduplication.
- **Become a chaitops collector/plugin** — rejected: reduces the squad to a
  data source, killing the supervisor/classifier/RCA product core.
- **Reuse by dependency (shared libs)** — rejected: couples release cadence
  and failure modes of two products; a copy diverges freely and cheaply.

## Consequences

- **Positive:** full product autonomy (own auth, CI, portal, chart — all since
  built and cluster-validated); proven pattern reuse without coupling; the
  differentiator (lightweight specialists with direct datasource access +
  read-only posture) stays the center of the product.
- **Negative / trade-offs:** assumed duplication cost — the squad maintains
  its own platform layer in parallel with chaitops' (explicitly accepted in
  the decision record).
- **To watch:** the anomaly-detection → RCA trigger integration should
  **consume** that project's existing enriched-payload contract (spec 18 /
  `/alerts/incoming`) rather than inventing a second one; divergence there is
  the first sign this decision needs revisiting.
