# Architecture Decision Records

ADR conventions from `template-project` (`templates/adr.template.md`): one
decision per record, **immutable** — a changed decision gets a NEW ADR that
supersedes the old one. New ADRs land here as `NNNN-slug.md`.

> ADR-0001 predates this directory and lives at its original path
> (`specs/ADR-001-bedrock-direct-vs-strands.md`) — kept there because ~10 docs
> link to it. New numbering continues from it.

| ADR | Decision | Status | Date |
|---|---|---|---|
| [0001](../../../specs/ADR-001-bedrock-direct-vs-strands.md) | Bedrock-direct orchestration; no agent framework (LangGraph removed, Strands rejected) | accepted | 2026-06-16 |
| [0002](0002-in-process-config-driven-agents.md) | Specialists are in-process `GenericAgent`s defined by config directories — not per-agent services | accepted | 2026-06-14 (recorded 2026-07-03) |
| [0003](0003-read-only-security-posture.md) | Read-only is the security posture of the current phase; execution is gated, not ruled out | accepted | 2026-06-16 (recorded 2026-07-03) |
| [0004](0004-fail-closed-security-fail-open-availability.md) | Security layers fail closed (403); availability layers fail open | accepted | 2026-06-22 (recorded 2026-07-03) |
| [0005](0005-two-tier-edge-gateway.md) | A thin edge gateway fronts the supervisor (two-tier); supervisor becomes backend-only | accepted | 2026-06-22 (recorded 2026-07-03) |
| [0006](0006-standalone-product-reuse-by-copy.md) | AIgent-squad stays a standalone product; chaitops ecosystem patterns are reused by copy, never by dependency | accepted | 2026-06-02 (recorded 2026-07-03) |
| [0007](0007-internal-first-vs-oss-product.md) | Internal-first tool vs OSS product — pick a lane (A recommended) | **accepted** | 2026-07-04 |
| [0008](0008-agentic-tool-calling.md) | Agentic tool-calling (LLM-driven) — supersedes ADR-001 "Caminho A" (Path A) | accepted | 2026-07-19 |

Backfilled 2026-07-03 from decisions already recorded in spec designs
(sources cited in each ADR). Product-level "why" lives in
[`docs/prd/aigent-squad.md`](../../prd/aigent-squad.md).
