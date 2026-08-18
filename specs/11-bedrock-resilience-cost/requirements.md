---
spec: 11-bedrock-resilience-cost
status: done
completed: 2026-07-02
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Bedrock Cost & Model Tiering

**Spec**: `11-bedrock-resilience-cost`
**Severity**: 🟠 High (cost + routing latency)
**Origin**: `../ANALYSIS.md` CONV-3, finops F1/F2, aws F11
**Depends on**: `06-resilience-patterns`

Every query makes **2 Bedrock calls** (classifier + agent), both with Sonnet 4.5. Using Sonnet for *routing* is ~10–13× more expensive than necessary, and **prompt caching is disabled** (`bedrock.py:28-29` commented out), resending ~6400 system prompt tokens on every call. This spec applies **model tiering** (right model per role) + **prompt caching** + budget. This isn't "over-saving" — it's not wasting on routing and cutting latency where possible.

## User Stories

WHEN the classifier routes THEN SHALL use a **fast/cheap** model (Haiku) — routing is a simple task.

WHEN an agent responds / the RCA synthesizes THEN SHALL use a **strong** model (Sonnet) — quality matters.

WHEN the same large system prompt is resent THEN SHALL use **prompt caching** from Bedrock (~90% discount on cached input).

WHEN a session consumes too many tokens THEN SHALL respect a **configurable budget** (cuts before cost explodes).

WHEN the model per role is chosen THEN SHALL come from **config** (`model_tier` from spec 22 / config from spec 19), not hardcoded.

## Acceptance Criteria

- [ ] Model per role configurable: `classifier`→Haiku, `agent`/`synthesis`→Sonnet (aligns with specs 19/22).
- [ ] Prompt caching re-enabled (`cache_control: ephemeral` in system block) + validated against the model.
- [ ] Token budget per session (configurable hard cap) — cuts with a clear message.
- [ ] History truncation by tokens (not just by message count).
- [ ] Cost/token metrics emitted (input/output per agent+model) — coordinates with observability (future spec 10).
- [ ] Tests (test-author ≠ author, ≥90%): model selection per role, caching applied, budget cuts, truncation.

## Out of scope
- Cross-region Bedrock failover — future (over-engineering pre-MVP).
- Cost dashboards → spec 10 (observability).
- Retraining/quality evaluation of Haiku for routing — validate empirically in use.
