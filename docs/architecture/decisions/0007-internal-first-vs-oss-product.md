# ADR-0007: Internal-first tool vs open-source product — pick a lane

| Field | Value |
|---|---|
| **Status** | **proposto** (decision owner: Carlos Felipe Gomes — undecided) |
| **Date** | 2026-07-04 |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | `docs/prd/aigent-squad.md` (open questions), `docs/COMPETITIVE-ANALYSIS.md`, spec 36 (dev loop), ADR-0006 (standalone product) |

## Context

The repo currently straddles two identities and pays the costs of both while
collecting the benefits of neither:

- **OSS-product signals**: Apache-2.0 license, public MkDocs site
  (staffops.github.io/aigent-squad), competitive analysis vs commercial AI SREs,
  README written for external adopters.
- **Internal-tool reality**: a hard-private dependency (`otel-helper` from a
  private repo — external users **cannot even run the tests**), quickstart that
  requires provisioning a Bedrock Guardrail via Terraform to work with secure
  defaults, image on a personal Docker Hub namespace, deploys and homologation
  exclusively on the BDC devops-core cluster, zero community surface (no
  issues, no demo, no external users).

Every roadmap decision (interface choice, quickstart investment, dependency
policy, docs effort) prices differently depending on the lane. Not deciding is
the most expensive option: OSS overhead with zero OSS upside.

## Decision

**Pending.** Two candidate resolutions, with a recommendation:

- **Option A — Internal-first (recommended for now)**: declare BDC the sole
  target for the next phase. Consequences: drop OSS overhead from the critical
  path (site stays but stops gating releases), keep the private dep, prioritize
  the internal interface (Slack v2 / in-cluster callers), revisit OSS after the
  product has real internal users and the CASE-001 proof (spec 18 T15).
  Rationale: adoption evidence should precede packaging; OSS-ing an unvalidated
  product buys maintenance cost, not users.
- **Option B — OSS product**: commit to the 5-minute external path. Consequences
  (all become roadmap items): remove/optionalize `otel-helper` (make OTel wiring
  degrade gracefully without the private package), `docker compose up` demo mode
  with zero AWS-side provisioning (guardrail off + seeded fixtures), org-owned
  registry namespace, CONTRIBUTING + issue triage, and the quality eval (spec 35)
  as the public quality bar.

## Alternatives considered

- **Keep straddling (status quo)** — rejected by this ADR's existence: it taxes
  every decision and the tax is invisible until summed.
- **Commercial product** — premature; no users, no case study, solo maintainer.
  Revisit only after Option A produces internal adoption evidence.

## Consequences

- **Positive (either lane)**: roadmap pricing becomes coherent; spec 36 can
  implement the right quickstart (internal: IRSA-assumed; OSS: fixture demo).
- **Negative / trade-offs**: Option A shelves the OSS differentiation narrative
  (read-only + multilingual defense is a strong public story — COMPETITIVE-
  ANALYSIS); Option B costs weeks of packaging work before any product learning.
- **To watch**: if Option A is chosen, the reopen trigger for B is: CASE-001
  exists + ≥3 weekly internal users + spec 35 baseline stable — then the OSS
  packaging buys distribution for something proven.

> When decided: update Status to `aceito`, record the choice inline, and open
> the consequent spec(s). This ADR then becomes immutable per the convention.
