---
spec: 38-model-tier-escalation
status: done
completed: 2026-07-22
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
Phase 1 **DELIVERED** (agentic24, pre-routing; escalation dropped by harness). Classifier emits
`complexity`; `_resolve_tier_model` maps (complexity, confidence) → tier one-shot; 77 tier tests pass.
- **T9 Opus access — RESOLVED 2026-07-23:** the originally-configured Opus 4.0 profile does NOT exist
  in-account (ResourceNotFound); **Opus 4.5** (`us.anthropic.claude-opus-4-5-20251101-v1:0`) is ACTIVE
  (cross-region). Fixed the deep model id + pricing ($5/$25, ~3× cheaper than Opus 4.0). Overlay flip
  (`AIGENT_TIER_DEEP_ENABLED=true` + explicit model id) PREPARED — activates on push.

### Post code-review follow-ups (Phase-1 polish, non-blocking)
- [ ] **FU-A — document the Phase-2 dispatch condition.** Phase 1 pre-routes one-shot (no runtime
  escalation). Phase 2 (if ever) would add a *dispatch condition* to bump tier mid-loop ONLY on a
  measured signal (e.g. repeated low-confidence tool results), NOT on quality guesswork. Documented
  here as the trigger; no code in Phase 1.
- [x] **FU-B — DONE (agentic28): startup validation moved to the FastAPI lifespan** (was an import side-effect). `validate_tier_models_at_startup()`
  is currently a **module-level call at `src/supervisor/agent.py:602`** (runs on import → complicates
  testing + import ordering). Move it into the app startup hook (FastAPI lifespan / explicit init) so
  validation runs once at boot, not on every import. Medium-risk (must keep fail-loud on misconfig) →
  do with a test that asserts boot fails on an invalid tier id. Deferred (not rushed at session tail).

### FU-C — tier routing was INERT in prod (CRITICAL, FIXED + homologated 2026-07-23)
Homologation revealed `_resolve_tier_model` was only threaded in auto-route + fan-out; **force_agent
streaming** (LibreChat per-agent models), the **investigation orchestration**, and the **alertmanager
webhook** all bypassed it → 17/17 live invocations were Sonnet; Opus/Haiku never activated. Fixed via
the harness pipeline (103 tests): force_agent uses a heuristic complexity (no extra classify call),
investigation/alertmanager route as `complex`. **T10 homologated live (agentic28): simple→Haiku (2.4s),
complex RCA→Opus 4.5 (`tier=deep`, 15 Opus invocations).** Raised `GATEWAY_FIRST_BYTE_TIMEOUT` 90→140s
(Opus + non-streaming first-byte ≈ loop completion ~100s). T9 + T10 now DONE.
