---
spec: 38-model-tier-escalation
status: in-progress
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Tasks: Model-tier routing + escalation

Harness: `dev` implements → `dev` tests (independent) → `code-review` → gate ≥90%.
Round-table (code-review + finops + sre) on the spec BEFORE implementation
(cost + reliability of escalation).

- [ ] T1 Config: `BEDROCK_TIER_{FAST,STANDARD,DEEP}_MODEL_ID`, `AIGENT_TIER_ROUTING_ENABLED`, `AIGENT_TIER_ESCALATION_ENABLED`, `AIGENT_TIER_CONFIDENCE_{HIGH,LOW}`, `AIGENT_TIER_MAX_ESCALATIONS`.
- [ ] T2 `model_tier.py`: `resolve_model_for_tier(tier)` + tier↔family validation (fail loud on misconfig).
- [ ] T3 Classifier emits `complexity` (simple|standard|complex) in JSON + heuristic fallback.
- [ ] T4 Dispatch in `supervisor/agent.py`: (complexity, confidence) → tier → thread tier model_id into the agentic loop.
- [ ] T5 Escalation: after loop, retry-once to next tier on exhaustion / quality-defect / low-confidence; metric `agent_tier_escalations_total` + trace attrs; budget-tracked.
- [ ] T6 Independent tests: tier selection matrix, downshift-simple, escalate triggers, bound=1, routing-off=always-standard, cost attribution per tier. ≥90% cov.
- [ ] T7 `code-review` + refute (finops: cost blowup; sre: escalation latency/loops).
- [ ] T8 Verify Opus inference-profile access in the Bedrock account (blocking for `deep`).
- [ ] T9 Build + deploy + homologate: simple query → Haiku (latency/cost drop); complex → Sonnet/Opus; escalation fires + is bounded; eval 6/6.
- [ ] T10 Docs (CHANGES/BACKLOG/AGENTS/spec) + ROADMAP regen + gate.

## Status
Phase 1 — spec drafted, awaiting harness round-table before implementation.
