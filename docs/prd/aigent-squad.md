# PRD — AIgent-squad: consultative multi-agent AI SRE (read-only RCA)

> Initiative-level PRD (template: `template-project/templates/prd.template.md`).
> It answers "what problem, for whom" — one initiative, many specs. Detailed
> execution lives in `specs/` (spec-driven workflow).

| Field | Value |
|---|---|
| **Status** | `aprovado` (2026-07-03) |
| **Author(s)** | Carlos Felipe Gomes |
| **Stakeholders** | StaffOps / <ORG> platform team (devops-core operators) |
| **Date** | 2026-07-03 |
| **Related specs** | `specs/ROADMAP.md` (all); positioning: `specs/ECOSYSTEM.md`, `docs/COMPETITIVE-ANALYSIS.md`; security thesis: `specs/14-security-hardening/` |

## 1. Problem

Incident investigation and RCA in AWS/EKS environments is manual, slow and
cross-domain. When something breaks, a senior engineer spends **30–60 minutes**
(≈ $50–100 of engineering time per incident) correlating evidence scattered
across domains that don't share a pane of glass: metrics/logs/traces
(observability), K8s events/restarts/OOMKills, recent deploys/MRs (devops), AWS
infra health, and cost anomalies (FinOps). The evidence exists; the
*correlation* is the expensive part.

The emerging "AI SRE" market answers this with **autonomy-first** agents
(Datadog Bits, Aurora, PagerDuty, Azure SRE Agent) that *act* on production —
and retrofit guardrails afterwards (Aurora added NeMo + Sigma after the fact;
Azure SRE Agent's anti-injection only covers English). For a platform team, an
agent that can mutate prod on the strength of an LLM decision is a trust
problem, not a productivity win.

## 2. Objective and expected outcomes

A **consultative, read-only multi-agent AI SRE** the operator can trust in
production from day one:

- Given a symptom (chat question or Alertmanager webhook), the system
  investigates in parallel across 5 domain specialists with **direct datasource
  access** (boto3, K8s API, Prometheus, GitLab, Athena/CE) and returns an RCA
  with **hypothesis, evidence, timeline, confidence and prevention proposal** —
  never a mutation.
- Security is the product, not a feature: **defense-in-depth anti-prompt-
  injection (multi-language) built BEFORE any autonomy**, so that when
  execution is eventually enabled it ships with guardrails competitors added
  late. Fail-closed for security, fail-open for availability.
- Runs on the team's own plane (EKS + Bedrock + IRSA — inference and data never
  leave the AWS account), with **per-agent cost attribution** (FinOps-grade
  showback) and an operating cost per investigation ~2 orders of magnitude
  below human investigation.
- Extensible without code: an agent is a directory (`agent.yaml` + `prompt.md`),
  auto-discovered — platform teams add domain specialists via config/GitOps.

## 3. Success metrics

| Metric | Baseline (manual / pre-project) | Target | Measured by |
|---|---|---|---|
| Time to RCA (single round) | 30–60 min (senior engineer) | ≤ 30 s end-to-end | `aigent.investigation.duration` |
| Cost per investigation | $50–100 (human time) | ≤ $0.20 (Level 1) | `aigent.cost.estimated` + AIP/Cost Explorer |
| Routing quality | n/a | confidence ≥ 0.9 sustained (0.98 measured 2026-07-01) | classifier confidence + fallback-parse rate |
| Injection defense | none | 100% of attack-suite classes blocked (403), incl. non-English + obfuscations | `tests/test_attack_suite.py` CI gate + cluster probes |
| Monthly LLM spend (200 q/day profile) | ~$207 naive | ≤ ~$99 (tiering + caching) | spec 33 cost reconciliation vs `ANALYSIS.md` model |
| Quality gate | 0 tests (2026-05-30) | ≥ 90% coverage enforced in CI (93% current) | `pytest --cov-fail-under=90` |
| Adoption | 0 | baseline to be set by first operational review | gateway `aigent.requests.total` per week |

## 4. Target audience / personas

- **SRE / DevOps operator** — asks via LibreChat (OpenAI-compatible `/v1`) or
  HTTP; consumes RCAs and inventory/cost answers; today's primary user.
- **On-call responder** — receives investigation results triggered by
  Alertmanager (`/alerts/incoming`) with optional Slack post-back.
- **FinOps analyst** — per-agent Bedrock cost showback (AIP tags + token
  metrics); cost-trend questions via the finops agent.
- **Platform team (<ORG>)** — deploys/extends the squad (Helm chart, agents by
  config, skills), integrates in-cluster callers (anomaly-detection, Falco).

## 5. Scope

- **In:**
  - 1 supervisor + 5 in-process specialists (aws, kubernetes, finops, devops,
    observability), config-driven, classifier-routed, fan-out + synthesis.
  - Edge gateway (auth, backpressure, rate/budget admission) fronting the
    supervisor; OpenAI-compatible bridge; MCP server.
  - RCA workflow Phase 1 (single-round) + alert ingestion; incident memory
    (pgvector KB + RAG injection).
  - Defense-in-depth L1–L6 (Bedrock Guardrail, InputScanner, context isolation,
    output filter, canary, rate/budget) — multi-language, fail-closed.
  - Model tiering (Haiku classifier / Sonnet agents), prompt caching, per-agent
    cost attribution, ≥1 custom metric per feature.
- **Out (explicitly):**
  - **Auto-remediation / execution** — open future, gated on spec 14 complete
    (incl. entry-point findings) + mandatory human-in-the-loop (`docs/READ_ONLY_POLICY.md`).
  - Distributed per-agent topology (chart renders it; deferred per ADR — reopen
    trigger in `specs/ROADMAP.md` backlog).
  - Bedrock Knowledge Bases / OpenSearch RAG (negative ROI below ~2000
    queries/day — `ANALYSIS.md` finops F7).
  - Multi-tenant orgs / contractual chargeback (showback only).
  - RCA Levels 2–4 (iterative/shared-context/autonomous) — promoted ONLY by
    measured triggers (`specs/ROADMAP.md` vision + spec 33).

## 6. Risks and assumptions

- **Assumed**: Bedrock availability/quotas suffice for the load profile;
  read-only IAM/RBAC actually enforce what the prompt promises (validated via
  IRSA in devops-core); the <ORG> cluster remains the primary deploy target.
- **Risk — market speed**: Aurora/HolmesGPT (CNCF) mature fast; our security
  differentiator is only real while it stays *implemented and validated* —
  the 3 open entry-point findings (A/B/D) are exactly that gap.
- **Risk — guardrail false positives** block legitimate ops queries →
  mitigation path recorded (own detector as L1 fallback; spec 14 triggers).
- **Risk — cost model drift**: tiering/caching assumptions vs real usage —
  bounded by spec 33's reconciliation (>30% divergence ⇒ model update).
- **Risk — bus factor**: solo maintainer; mitigated by spec-driven records +
  process specs 32–34.

## 7. Open questions

| Question | Owner | Forum |
|---|---|---|
| When to enable execution (read-only → act with HITL)? | product owner | requires spec 14 findings closed + a dedicated execution spec (threat model rewrite) |
| Wire a live LibreChat instance end-to-end? | platform | HANDOFF pending #4 |
| Resurrect spec 25 (session lock, distributed CB, k6)? | ops review | only via spec 33 trigger (gateway saturation signals) |
| Slack surface v2 (`src/api/server.py` rewrite)? | product owner | Phase 3 (`specs/ROADMAP.md`) |
