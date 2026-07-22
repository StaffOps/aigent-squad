---
spec: 38-model-tier-escalation
status: done
completed: 2026-07-22
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Feature: Model-tier routing + response-driven escalation

Pick the agent model per query **complexity** (fast / standard / deep) and
escalate a higher tier only when a quality/confidence signal is low or the loop
exhausts — "acertivo **and** eficiente". Reuses the classifier confidence,
`model_tier.py`, `ResponseQualityGuard` (spec 35), and the budget tracker. Stays
Bedrock-direct (distinct from spec 28 provider abstraction).

## Problem

All agents run on a **fixed** model (Sonnet 4.5). Simple factual queries
("quantos pods?") pay the Sonnet tax (latency + cost) unnecessarily; the hardest
RCA queries get no more capability than a trivial lookup. There is no complexity-
or quality-driven model selection — only role-based (`resolve_model(role)`).

## User stories

WHEN the classifier scores a query as SIMPLE with high confidence THEN the agent
SHALL run on the FAST tier (Haiku) — lower latency + cost, accuracy preserved.

WHEN the query is STANDARD THEN the agent SHALL run on the STANDARD tier
(Sonnet, default).

WHEN the query is COMPLEX (RCA / multi-signal / low confidence) THEN the agent
SHALL run on the DEEP tier (Opus), or standard with escalation enabled.

WHEN the agentic loop exhausts without resolving, OR `ResponseQualityGuard` flags
a structural defect, OR classifier confidence is below a threshold THEN the system
SHALL escalate ONE tier and retry — bounded (max 1), budget-tracked, emitted as a
FinOps-visible metric + a trace attribute.

WHEN an operator tunes behavior THEN the tier→model map, complexity thresholds,
and escalation on/off SHALL be env-configurable.

## Acceptance criteria

- [ ] Simple query routes to FAST (Haiku) — measurable latency + cost drop; eval still correct.
- [ ] Standard → STANDARD (Sonnet); complex → DEEP (Opus) or escalation.
- [ ] Escalation fires on loop-exhaustion / quality-defect / low-confidence; bounded to 1; logged + FinOps metric + trace attr.
- [ ] Never silently DOWNGRADES a query that needs accuracy (default = standard; downshift only on confident-simple).
- [ ] `compute_cost` reflects the actual tier used per query.
- [ ] Golden-query eval stays 6/6 (no accuracy regression from downshifting simple queries).
- [ ] ≥90% coverage; independent tests; code-review.

## Out of scope

- Provider abstraction / local + API-key models (spec 28).
- Multi-hop escalation beyond 1 step (Phase 2).
- Per-agent fixed tier overrides (`agent.yaml` `model.tier` is a hint, not this mechanism).
