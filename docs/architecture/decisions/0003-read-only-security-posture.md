# ADR-0003: Read-only is the security posture of the current phase — execution is gated, not ruled out

| Field | Value |
|---|---|
| **Status** | accepted |
| **Date** | 2026-06-16 (backfilled 2026-07-03) |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | spec 14 (security hardening — thesis + STRIDE), spec 04, `docs/READ_ONLY_POLICY.md`, `docs/COMPETITIVE-ANALYSIS.md`, ADR-0001 |

## Context

The AI SRE market is autonomy-first: Datadog Bits, Aurora, PagerDuty and Azure
SRE Agent **act** on production (rollback/scale/restart), using human-in-the-
loop as the safety crutch — and retrofit guardrails after incidents (Aurora
added NeMo + Sigma later). In an agent that acts, a successful prompt
injection is a production incident. STRIDE analysis (spec 14) shows
"Elevation" is the dominant threat exactly when an agent can mutate state.

## Decision

The squad is **read-only in the current phase**, enforced by 4 independent
layers: system prompts, IAM explicit deny, K8s RBAC (get/list/watch only), and
response templates. This is a **posture, not a permanent lock**: enabling
execution is an open roadmap item, conditional on (a) spec 14 implemented and
validated end-to-end (including its entry-point findings) AND (b) mandatory
human-in-the-loop for any mutating action AND (c) a rewritten threat model
(elevation returns as the dominant threat).

## Alternatives considered

- **Act with HITL from day one (market default)** — faster perceived value;
  rejected: HITL is a crutch, not a guarantee; injection blast radius stays
  high; the trust pitch ("the AI SRE you can run in prod today") evaporates.
- **Read-only forever** — simpler threat model permanently; rejected: closes
  the product's growth path; the roadmap explicitly keeps execution open.
- **Prompt-only read-only (original state)** — was the reality pre-spec-04
  (AUDIT SEC-D4: policy promised 4 layers, only the prompt existed); rejected
  as security theater.

## Consequences

- **Positive:** eliminates the mutation/elevation threat class at the
  architecture level (not per-request); worst case of a successful injection
  is exfiltration/manipulation/cost — which spec 14's L1–L6 then targets;
  differentiator: guardrail-before-acting vs competitors' acting-before-guardrail.
- **Negative / trade-offs:** no auto-remediation — the operator applies fixes
  manually (acceptable for a consultative product); "we don't compete on
  autonomy" limits some deals/use-cases until execution ships.
- **To watch:** if read-only is relaxed, spec 14 must be **rewritten** (its own
  documented reopen rule) and this ADR superseded by an execution ADR.
