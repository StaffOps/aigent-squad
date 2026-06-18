# StaffOps Ecosystem — Integration and Reuse

**Date**: 2026-06-02
**Scope**: analysis of `staffops-chaitops`, `staffops-anomaly-detection` and
`01-DEVOPS/LABS/anomaly-detection` for reuse/integration into AIgent-squad.

> **Central finding**: there is a StaffOps ecosystem where **much of what we plan
> for AIgent-squad (specs 14, 17, 18, 19, 21) is already designed — and in
> several cases more mature — in `staffops-chaitops`.** The flow you want (RCA +
> learning) **is already the product** that chaitops + anomaly-detection form
> together. This requires a positioning decision before continuing to implement
> AIgent-squad in isolation.

---

## The three repos

### 1. `staffops-chaitops` — agent platform (v0.2.0, MATURE)
FastAPI **agent-api** (gateway, no CLIs) + **sidecar runners** (isolated
claude/kiro/opencode CLIs). 186 tests, 83% coverage, GitHub Actions CI, MkDocs,
OTel Collector already in compose. Principles: **no LangGraph, adapter-based,
MCP-first, OTel day one, CLI-agnostic** (the same we adopted in AIgent-squad).

Specs already written (`.kiro/specs/`):

| chaitops spec | What it is | Overlaps our spec |
|---------------|------------|-------------------|
| **multi-agent-coordinator** | `CoordinatorAdapter`: @mention fast-path OR LLM routing → `asyncio.gather` fan-out → stream aggregation with provenance → `BudgetGuard` (hard caps), cycle detection, sub-sessions in Redis, `specialists.yaml` with personas | **= our 17** (fully designed, with budget/cycle/cancellation) |
| **alert-triggered-squad** | Headless: Alertmanager webhook → squad by severity → parallel rounds with **convergence** → synthesis → Slack. Dedup, rate-limit, daily budget, own SLO | **= our 17+18 combined** (headless RCA, better than ours) |
| **conversation-distillation** | Ephemeral conversations → PII redaction → LLM distill → `KbDelta` schema → confidence thresholds per type → Slack `#kb-review` approval → Postgres+pgvector + RAG injection | **= our 21** (learning), much more complete |
| **agent-extensibility** | Auto-discovered YAML manifests, `required_env` filtering, `/ready` checks | **= our 19** (config-driven) |
| **api-authentication** | API-key behind a flag → JWT/Istio mTLS roadmap, rate-limit | **= our 14** (security-hardening) |
| **sidecar-architecture**, **worker-pool**, **mcp-integration**, **observability**, **storage-abstraction**, **openai-compat-bridge**, **knowledge-platform-rag**, **deferred-tasks**, **platform-agent-directives**, **ci-cd-pipeline** | platform infra | largely cover our 06/07/08/09/10/12 |

It also has `.kiro/skills/`: `kiro-cli-auth`, `mcp-deployment`, `documentation-audit`.

### 2. `staffops-anomaly-detection` — source of the RCA trigger (Go + Python ML)
Controller + gRPC workers (static/adaptive Z-score/log/events detection),
**correlation engine** (workload extraction, dedup cooldown, ≥3 sibling pods →
workload-level alert, metric+log+ML severity escalation), enrichment, replay-mode,
leader election, vmrules. ML: Prophet + Isolation Forest.

Spec **agent-api-integration**: when `severity>=warning AND (ml_score>=0.7 OR
corr_group>=3)`, it fires an **enriched** payload (enrichment bundle + ML score +
correlation + Grafana/Tempo/Loki links) to the chaitops Agent API —
fire-and-forget, circuit breaker, dedup, 64KB cap. **It's the RCA trigger that
delivers ~80% of the diagnosis ready.**

### 3. `01-DEVOPS/LABS/anomaly-detection` — lab/old version of #2 (ignore; use #2).

---

## The finding that matters

The **RCA + learning product flow you described already exists in the ecosystem's
design**:

```
anomaly-detection (detects + correlates + enriches + ML)
        │  enriched webhook (≥80% of the diagnosis)
        ▼
chaitops alert-triggered-squad (headless, parallel, rounds + convergence)
        │  diagnosis → Slack
        ▼
chaitops conversation-distillation (distills the investigation → KB → RAG)
        │
        ▼
   next investigations start with the accumulated knowledge
```

Our specs 17 (multi-agent), 18 (RCA), 19 (config), 21 (learning) and 14 (security)
**reimplement pieces of this**. Continuing AIgent-squad as an isolated product =
**duplicating more mature work** (chaitops has tests, CI, OTel, MkDocs;
AIgent-squad doesn't even build today — see `AUDIT.md`).

---

## What AIgent-squad has that is UNIQUE (and chaitops does NOT)

chaitops runs **generic CLIs** (claude/kiro/opencode) in sidecars. It **has no
domain expertise nor direct datasource access**. AIgent-squad has exactly that:

| AIgent-squad unique asset | Why chaitops needs it |
|---------------------------|------------------------|
| **5 read-only specialists** (aws, k8s, finops, devops, observability) with domain personas | chaitops' `specialists.yaml` has only *persona prompts* — no agents that actually **query** EC2/Prometheus/Athena/GitLab |
| **Datasource integrations** (boto3 EC2/CE/Athena, kubernetes-client, Prometheus, GitLab, docs portal) | RCA evidence collection needs this; chaitops doesn't have it |
| **4-layer read-only policy** (consultative, never mutates) | Critical for an RCA product that investigates prod without risk |
| **MCP server** already exposed | Integration point |

In other words: **AIgent-squad is the "internal domain agents" layer that
chaitops' own README lists as `Future > Internal Agents`** — a piece it designed
but didn't build.

---

## DECISION MADE (2026-06-02)

The user decided: **keep AIgent-squad as a SEPARATE product** (no merge, not
becoming a chaitops collector). Integration with the ecosystem is via **HTTP**
when needed; **gRPC out**; docs portal in **English**.

> Impartiality note: this is Option C (the one I had classified as "worst" for
> duplicating a platform). The decision is legitimate and product-driven — but it
> comes with an **assumed cost**: AIgent-squad will have to maintain its own auth,
> config, OTel, CI and portal, in parallel with what chaitops already has. To
> mitigate, **reuse chaitops' patterns/code by copy** (not by dependency) where
> possible — see "Concrete reuse" below.

### The real differentiator (validated in the efficiency discussion)

AIgent-squad's differentiator is **not inter-agent communication** (HTTP vs gRPC
= ~3ms over ~5s model calls = noise). The differentiator is: **lightweight
specialists with direct access to data sources** (boto3, Prometheus, Athena,
GitLab), which make an RCA's **parallel fan-out** fast and cheap — without the
2–15s boot of a CLI-sidecar per agent. RCA speed comes from: **parallelism (17) >
pre-ready evidence (18) > lightweight agent > fast model in routing (11)**.
Communication is not on the list.

### Impact on the specs (decision = keep separate)

| AIgent-squad spec | Verdict (keep separate) |
|-------------------|-------------------------|
| 01–05 (AUDIT) | **Keep** — fixes of the current code |
| 06 resilience / 07 probes | **Keep** — inherit chaitops patterns by copy (lifespan, /healthz+/ready, circuit breaker) |
| 08 CI/CD, 09 OTel, 12 terraform | **Keep** — copy the chaitops approach, don't redesign from scratch |
| 11 bedrock-cost (Haiku) | **Keep** — high impact on cost+latency |
| 14 security | **Keep** — copy the `api-authentication` pattern (auth via flag) |
| 17 multi-agent (fan-out) | **Keep — it's THE speed engine.** Reuse `BudgetGuard`+cycle detection from `multi-agent-coordinator` by copy |
| 18 RCA | **Keep — the differentiator.** Consume the anomaly-detection payload contract (don't invent another) |
| 19 config | **Keep** — copy the `AgentRegistry` (already our design). 22 replaces/extends it |
| **20 gRPC** | **❌ REMOVED** — irrelevant latency gain (~3ms over 5s). Don't build |
| 21 learning | **Keep** — reuse the `KbDelta` schema + thresholds from `conversation-distillation` by copy |
| 22 capability-manifest | **Keep** — open roster + collaboration via metadata |
| 23 test-harness / 24 docs | **Keep** — docs in **English** |

### Concrete reuse available NOW (by COPY, given "keep separate")

1. **`AgentRegistry`** (agent-extensibility) — YAML manifests + `required_env` +
   `/ready`. Base of our 19/22.
2. **`BudgetGuard` + cycle detection** (multi-agent-coordinator) — cost/cycle
   control of our 17.
3. **Round convergence** (alert-triggered-squad) — model for the "1 round vs
   iterative" question of 18.
4. **`KbDelta` schema + thresholds + training mode** (conversation-distillation)
   — learning design of 21.
5. **anomaly-detection payload contract** (agent-api-integration) — 18 should
   **consume** this contract.
6. **`kiro-cli-auth` skill** — API key vs SSO vs IRSA auth.
7. **Platform patterns**: worker-pool, OpenAI-compat bridge, OTel Collector in
   compose, GitHub Actions CI with coverage.

> Clean-room note: "by copy" here means reusing the *patterns/design* learned
> from our own StaffOps repos. For any third-party code, follow
> `steering/licensing-clean-room.md` (no copying; declared dependencies only).
