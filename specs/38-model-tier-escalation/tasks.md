---
spec: 38-model-tier-escalation
status: in-progress
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Tasks: Model-tier PRE-ROUTING (post round-table — escalation dropped)

Harness: `dev` implements → `dev` tests (independent) → `code-review` → gate ≥90%.
Round-table done (code-review + finops + sre) → **NO-GO on escalation-as-retry;
GO on pre-routing (HC1–HC6, see design.md).**

- [ ] T1 Config: `BEDROCK_TIER_{FAST,STANDARD,DEEP}_MODEL_ID`, `AIGENT_TIER_ROUTING_ENABLED` (default true), `AIGENT_TIER_CONFIDENCE_HIGH` (0.85). NO escalation knobs.
- [ ] T2 `model_tier.py`: `resolve_model_for_tier(tier)` + **startup validation (HC5)** — fail loud on invalid/empty tier model IDs; probe Opus access before `deep` is usable.
- [ ] T3 Classifier emits `complexity` (simple|standard|complex) + heuristic fallback; prompt teaches what "complex" means (RCA/multi-signal is NOT simple).
- [ ] T4 Dispatch (`supervisor/agent.py`): (complexity, confidence) → tier, ONE-SHOT (HC2). Downshift to `fast` only for provably-simple + high-confidence (HC3); confident-complex → `deep` directly; else `standard`. Thread tier model_id into the loop.
- [ ] T5 Transient-only retry (HC4): keep/confirm same-tier retry + backoff + per-tier circuit breaker for Bedrock 429/5xx. Quality-guard defect → current behavior (no tier bump).
- [ ] T6 Metrics (HC6): `model_tier_cost_usd{tier}`, tier distribution, per-tier latency; trace attr `tier`. Latency SLO per tier + wall-clock kill.
- [ ] T7 Independent tests: tier-selection matrix, provably-simple downshift only, RCA-looks-simple stays ≥standard, routing-off = always standard, startup-validation fail-loud, transient retry same-tier. ≥90% cov.
- [ ] T8 `code-review` — confirm HC1–HC6; refute residual mis-tiering cost.
- [ ] T9 Verify Opus inference-profile access in the Bedrock account (blocking for `deep`).
- [ ] T10 Build + deploy + homologate: simple → Haiku (latency/cost drop); RCA → Sonnet/Opus (NOT Haiku); eval 6/6; per-tier cost visible.
- [ ] T11 Docs (CHANGES/BACKLOG/AGENTS/spec) + ROADMAP regen + gate.

## Status
Phase 1 — spec **reshaped by harness to pre-routing** (escalation dropped). Awaiting
user go/no-go on implementation.
