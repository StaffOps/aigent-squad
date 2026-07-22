---
spec: 38-model-tier-escalation
status: in-progress
completed: null
superseded_by: null
depends_on: ["37-agentic-tool-calling"]
deferred: []
---

# Design: Model-tier routing + escalation

## Tiers

| Tier | Model | When |
|------|-------|------|
| `fast` | Haiku 4.5 | simple + high-confidence |
| `standard` | Sonnet 4.5 (**default**) | everything unclassified |
| `deep` | Opus 4.x | complex / RCA, or escalation target |

`model_tier.py` gains `resolve_model_for_tier(tier)` alongside `resolve_model(role)`.
Config: `bedrock_tier_fast_model_id` (haiku), `bedrock_tier_standard_model_id`
(sonnet), `bedrock_tier_deep_model_id` (opus). Pricing already exists per family.

## Complexity signal — from the classifier (no extra model call)

The classifier (Haiku) already runs first. Extend its JSON to emit
`complexity: simple|standard|complex` (keeps `confidence`). Fallback heuristic if
the field is absent: fan-out ≥2 agents → complex; very short/factual single-agent
→ simple; else standard.

## Dispatch (supervisor/agent.py)

Map `(complexity, confidence)` → tier:
- `simple` AND `confidence ≥ HIGH` → **fast**
- `complex` OR `confidence < LOW` → **deep**
- else → **standard**

Run the agentic loop with the chosen tier's model (thread the tier's model_id into
the loop instead of the fixed agent model).

## Escalation (bounded, 1 step)

After the loop, escalate + retry ONCE to the next-higher tier
(`fast→standard→deep`, cap at `deep`) when ANY:
- loop exhausted (budget/steps) without a final answer,
- `ResponseQualityGuard` flags a structural defect,
- classifier `confidence < LOW` and the tier used was `fast`.

Emits metric `agent_tier_escalations_total{from,to}` + trace attrs
`tier`, `escalated`, `from_tier`, `to_tier`. Escalation tokens count toward the
session budget (FinOps-visible via `compute_cost`).

## Rationale

### Decision 1: complexity from the classifier (not a separate model call)
**Choice**: the Haiku classifier emits `complexity` in its existing JSON.
1. Zero extra latency/cost — the classifier call already happens.
2. Haiku is adequate for a 3-way bucket.
3. One decision point (classifier = the router).
**Trade-off**: Haiku's complexity guess is imperfect → escalation is the safety net.
**Reopen**: if simple-misclassified-as-complex inflates cost, add a heuristic guard.

### Decision 2: default = standard; downshift only on confident-simple
1. Accuracy is the priority — a wrong (too-low) tier on a hard query is worse than
   the Sonnet tax. Escalation covers under-tiering; there is no mid-answer de-escalation.
2. Downshift to `fast` only when clearly simple AND high-confidence.
**Trade-off**: some Haiku-capable standard queries still pay Sonnet — acceptable (safe side).

### Decision 3: escalation bounded to 1 step
1. Cost control — Opus is 5x Sonnet / 15x Haiku; unbounded retries blow FinOps.
2. Tied to the budget tracker; 1 hop recovers most under-tiering.
**Reopen**: if eval shows 2 hops materially help RCA → raise the cap (Phase 2).

## Invariants
- Never escalate beyond `deep`.
- Escalation tokens count toward the session budget (no bypass).
- Classifier stays Haiku.
- No accuracy regression: eval 6/6 must hold after downshifting simple queries.

## Config

| Env | Default | Meaning |
|-----|---------|---------|
| `BEDROCK_TIER_FAST/STANDARD/DEEP_MODEL_ID` | haiku / sonnet / opus | tier→model |
| `AIGENT_TIER_ROUTING_ENABLED` | `true` | master switch (off = always standard) |
| `AIGENT_TIER_ESCALATION_ENABLED` | `true` | escalation switch |
| `AIGENT_TIER_CONFIDENCE_HIGH` / `_LOW` | `0.85` / `0.5` | downshift / escalate thresholds |
| `AIGENT_TIER_MAX_ESCALATIONS` | `1` | bound |

## Harness round-table outcomes (BINDING — reshape the design)

code-review + finops + sre returned REQUEST-CHANGES with a unified conclusion:
**runtime escalation-as-retry is the wrong mechanism — PRE-ROUTE to the correct
tier instead.** Binding:

- **HC1 — Eliminate runtime tier escalation.** Impossible on the streaming path
  (bytes already flushed — can't retract a partial answer); restarts the whole loop
  (doubles cost/latency; the failed attempt already spent the token budget so a
  pricier retry is DOA); and masks real bugs (bad tool schema, missing context,
  guard false-positive).
- **HC2 — Tier from the classifier, ONE-SHOT:** confident-simple → `fast` (Haiku);
  confident-complex → `deep` (Opus) DIRECTLY (skip Sonnet); else → `standard`
  (Sonnet). A wrong tier = a classifier bug to fix, not a runtime retry.
- **HC3 — Downshift to `fast` ONLY for a provably-simple class** (factual lookup,
  high confidence, expected ≤1 tool call). Do NOT downshift investigative
  single-agent queries ("why is X slow?" looks simple but is complex). When in
  doubt → `standard`.
- **HC4 — Retry survives ONLY as same-tier retry for TRANSIENT errors** (Bedrock
  429/5xx) with backoff + a per-tier circuit breaker. NOT a tier bump.
  Quality-guard defects keep current behavior (refuse/403 or same-tier reprompt) —
  a bigger model does not fix format defects.
- **HC5 — Startup validation (fail loud):** validate each tier model_id at startup
  (settings validator / describe probe); no silent fallback. Confirm Opus
  inference-profile access before enabling `deep`.
- **HC6 — FinOps framing = QUALITY investment, not cost savings.** Haiku savings
  are dwarfed by Opus cost. Add per-tier cost attribution
  (`model_tier_cost_usd{tier}`), tier-distribution metric, and a latency SLO per
  tier (fast<3s, standard<10s, deep<20s) + wall-clock kill.

The escalation sections above are SUPERSEDED by HC1–HC4. The core deliverable is
now **complexity-aware pre-routing** (classifier → tier, one-shot) — simpler,
faster, more debuggable than escalation.

## Phase 1
Complexity-from-classifier + tier map + downshift-simple + escalate-once. NOT
multi-hop, NOT per-agent tier overrides. Opus must be enabled/available in the
Bedrock account (verify inference-profile access before enabling `deep`).
