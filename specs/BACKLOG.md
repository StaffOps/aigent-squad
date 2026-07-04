# BACKLOG — single home for findings, product items and dormant work

> Seeded 2026-07-04 (ahead of spec 32 T7, which formalizes structure + the deferred
> register). Reasoning behind every item: `specs/PRODUCT-REVIEW-2026-07.md` (PR-NN ids).
> Rule: items leave this file by becoming a spec/task or being closed with a one-line
> reason — they never silently vanish.

## Findings (defects observed in the running system)

| ID | Finding | Severity | Action | Status |
|----|---------|----------|--------|--------|
| F-001 | aws agent echoes raw `<use_mcp_tool>…` XML in responses instead of executing the tool (homologation 2026-07-03) | 🔴 | Fix scaffolding leak; add spec 35 T1 regression fixture | open |
| F-002 | finops surfaces `AccessDeniedException` + ~15s latency in EVERY answer (athena datasource vs `enable_athena_finops=false` IRSA) | 🔴 | Short-term: **drop the athena datasource** from `agents/finops/agent.yaml` until a real Athena target exists (decision recorded in ROADMAP dormant note); spec 35 T1 fixture | open |

## Product items (from PRODUCT-REVIEW-2026-07)

| ID | Item | Ref | Suggested shape |
|----|------|-----|-----------------|
| B-01 | **Evidence-adapters spec** — query-capable collection: PromQL builder (service/window), Loki LogQL (first-error, pattern), Tempo, GitLab deploy-history/MRs, ArgoCD events. Prereq for EVIDENCE-MODEL value (audit first: spec 18 T12a) | PR-02 | spec `37-evidence-adapters` (write when picked up) |
| B-02 | Real token streaming (or progress events as step 1) — pairs with the interface decision | PR-03 | extend spec 06 promise / new spec with interface work |
| B-03 | User feedback loop — `feedback` field on responses + `aigent.feedback.score` + spec 33 trend | PR-04 | small spec or task batch |
| B-04 | Selective adapter collection by query relevance (no tool-loop; ADR-001 intact) | PR-06 | task in evidence-adapters spec or own mini-spec |
| B-05 | ~~Primary trigger via anomaly-detection~~ **AMENDED by D-03**: integration is OPTIONAL — if adopted, its enriched alerts arrive at `/alerts/incoming` like any other trigger (and benefit from B-22 alert comprehension), consuming their contract (ADR-0006). CASE-001 (spec 18 T15) may use a replayed real alert meanwhile | PR-07 → PR-33 | optional; no dependency |
| B-06 | Skills content sprint — 5–10 SKILL.md from real BDC runbooks (zero code) | PR-08 | task batch, no spec needed |
| B-07 | Decide + document history retention (24h TTL is silent today) | PR-09 | decision → config + docs line |
| B-08 | Measure security-layer overhead (ms + $/request) | PR-10 | TRIGGERS.md row (spec 33 T1) |
| B-09 | Nightly integration smoke in CI (compose up → `make smoke`) | PR-11 | task after spec 36 T3/T9 |
| B-10 | Model-version refresh as standing review item | PR-12 | spec 33 TEMPLATE line |
| B-11 | Interface UX: hide internal agent taxonomy from end users when interface ships | PR-13 | note for interface spec |
| B-12 | Data classification/retention paragraph in SECURITY.md (history + KB hold real infra data) | PR-14 | docs task |
| B-13 | **Gap-directed round 2** — rule-triggered (correlator detects missing causal layer), 1–2 targeted agents, hard cap 2 rounds. The cost-shaped Level-2 MVP | PR-25 | spec (extend 18) — after B-01 + T12 |
| B-14 | **Classifier → planner**: same Haiku call emits `sub_queries[]` (per-agent sub-question + time window); GenericAgent collects against ITS sub-question | PR-26 | small spec/task batch — high leverage, near-zero cost |
| B-15 | **Cost-aware execution tiers**: triage class → model + collection depth (trivial = Haiku, no adapters); guardrail input-eval once per unique text per request (hash-keyed). THE savings engine that funds B-13/B-17 | PR-27 | spec (extends triage + bedrock chokepoint) |
| B-16 | **Calibrated honesty**: `confidence` + `unverified_claims[]` in the response contract, fed by the groundedness check at answer time | PR-28 | task batch with spec 35 machinery |
| B-17 | **Environment snapshot** (world-model lite): periodic cached summary of inventory + baselines (reuse anomaly-detection baselines), injected as compact context | PR-29 | small spec |
| B-18 | **Diff primitive** in adapters: `collect_diff(query, window_a, window_b)` computed in code, compact delta to the LLM | PR-30 | task in evidence-adapters spec (B-01) |
| B-19 | **Feedback → KbDelta**: thumbs-down + correction routed through the existing spec-21 pipeline (validator + budget guard intact) | PR-31 | extends B-03 |
| B-20 | **Prevention-as-code** (alert-rule YAML / runbook stub artifacts, propose-never-apply) + synthesizer conflict adjudication | PR-32 | prompt/schema tasks |
| B-21 | **Entity registry** — config-driven alias map (entity → k8s workload + Prometheus identity + cost tag + tier + owner + declared blind spots); entry #1 = the squad itself (PR-38). Interpretation knowledge, NOT a watch schedule | PR-34/38 | phase 1 of spec `38-on-trigger-comprehension` |
| B-22 | **On-trigger baseline verdicts** — at request/alert time, compute in code: now vs baseline (4 methods: static/robust-z/seasonal/derivative) → `normal/borderline/abnormal/no-baseline` verdict contract injected as context AND as `Evidence[]`; `/alerts/incoming` parses alert → entity + signal + current verdict BEFORE investigating. Zero LLM cost; optional Redis cache for computed baselines. **Nothing polls, watches or pages** | PR-33/35/37 | spec `38-on-trigger-comprehension` (requirements seed = `docs/BEHAVIOR-BASELINES.md`) |
| B-23 | **Comprehension quality metrics** — verdict precision via feedback (≥80%), verdict coverage, honesty rate (`no-baseline` surfaced, 100%, eval-checked), baseline freshness → TRIGGERS.md rows + spec-35 golden questions with known verdicts | PR-36 | with B-22; review via spec 33 |
| B-24 | **EVIDENCE-MODEL catalog extension** — add ids for disk usage, cost anomaly, quota headroom (verdicts need them; catalog grows with entity profiles) | PR-37 | doc task with B-22 |

## Decisions pending (owner: Carlos)

| ID | Decision | Where |
|----|----------|-------|
| D-01 | Internal-first vs OSS product | ADR-0007 (proposto; A recommended) |
| D-02 | Interface: LibreChat live vs Slack v2 (choose ONE) | PRD open questions; priced by D-01 |
| ~~D-03~~ | ✅ **DECIDED 2026-07-04 (refined same day)**: the squad is REACTIVE by design — acts only when triggered; proactive watching = separate product (anomaly-detection's territory, integration optional). Core = **on-trigger comprehension**: entity resolution + baseline verdicts when triggered | PRODUCT-REVIEW §F + `docs/BEHAVIOR-BASELINES.md` |

## Dormant (do NOT resurface until the blocker-trigger fires)

> Moved formally from ROADMAP by spec 32 T7; listed here for completeness.

- **finops ↔ Athena (enable path)** — trigger: someone actually needs Kubecost/CUR
  through the agent. (Until then F-002's short-term fix applies: drop the datasource.)
- **Distributed topology (RemoteAgent client)** — trigger: real need for independent
  per-agent scaling/isolation. ADR-001/0002 favor in-process.

## Deferred register (tails of "done" specs — completed by spec 32 T7)

- specs 06/17/18: T11 formal smoke (superseded in practice by B-09 + spec 36 smoke)
- spec 08: T7 demo stage
- spec 21: Opus enricher activation; Slack approval flow
- spec 31: T21 k6 load test; T23 final independent review
