# Operational Review — YYYY-MM

## 1. Review Metadata

| Field | Value |
|-------|-------|
| **Date** | YYYY-MM-DD |
| **Reviewer** | (name or agent session) |
| **Milestone** | (release tag or "monthly cadence") |
| **Scope** | (all triggers / subset — justify if subset) |

---

## 2. Trigger Measurements

Source: `specs/TRIGGERS.md`

| Trigger (source spec) | Measured value | Threshold | Status |
|-----------------------|----------------|-----------|--------|
| L1→L2: single-round insufficient (VISION/spec 18) | __%  | >30% | ⬜ BELOW / 🟡 NEAR / 🔴 CROSSED |
| Haiku routing accuracy (spec 11) | __% correct | < baseline | ⬜ / 🟡 / 🔴 |
| Synthesizer Sonnet→Opus (spec 11) | __% low-confidence at L3+ | >40% | ⬜ / 🟡 / 🔴 |
| Agent-as-tools depth rejected (spec 17) | __ rejections/30d | >0 sustained | ⬜ / 🟡 / 🔴 |
| Opus enricher demotion (spec 21) | __% no-added-value | >60% | ⬜ / 🟡 / 🔴 |
| Gateway saturation (spec 25) | queue_wait p99 = __ms | >5000ms | ⬜ / 🟡 / 🔴 |
| Skills keyword-miss (spec 26) | __% miss rate | "high" (define after baseline) | ⬜ / 🟡 / 🔴 |
| Evidence confidence skew (EVIDENCE-MODEL) | __% LOW confidence | >50% sustained | ⬜ / 🟡 / 🔴 |

**Legend**: ⬜ = well below threshold; 🟡 = approaching (>70% of threshold); 🔴 = crossed → action required.

For triggers marked GAP in `TRIGGERS.md`: state "NOT MEASURABLE — gap G{N}" and skip.

---

## 3. Cost Reconciliation

| Metric | Value |
|--------|-------|
| **Actual monthly Bedrock cost** (Cost Explorer, tag `CostProject=AIGENT-SQUAD`) | $____ |
| **ANALYSIS.md estimate for current level** | $____ |
| **Divergence** | __% |
| **Status** | ✅ Within 30% / ⚠️ Divergence >30% |

If divergence >30%, fill:

| Driver | Finding |
|--------|---------|
| Top agent by cost | (agent_id, % of total) |
| Query volume vs assumption | (actual/day vs 200/day) |
| Prompt caching working? | yes/no (check `cache_creation_input_tokens` > 0) |
| Action taken | (updated ANALYSIS.md / config change / further investigation) |

---

## 4. Findings & Deferred Triage

Review `specs/BACKLOG.md` — Deferred register and Dormant section.

| BACKLOG ID | Item | Decision | Reason |
|------------|------|----------|--------|
| F-NNN | ... | PULL / KEEP / CLOSE | (one-line reason; cite trigger or data) |
| D-NNN | ... | PULL / KEEP / CLOSE | ... |

**PULL** = create or resurrect a spec for this item.
**KEEP** = trigger not yet fired; leave with its existing trigger.
**CLOSE** = no longer relevant; close with reason.

---

## 5. Decisions

Each decision made in this review cites a spec or BACKLOG id.

| # | Decision | Cites |
|---|----------|-------|
| 1 | ... | spec NN / BACKLOG F-NNN |

---

## 6. Gaps

Metrics needed but not yet available (update `TRIGGERS.md` gap table if new).

| Gap | What's needed | Priority (blocks upcoming decision?) |
|-----|---------------|--------------------------------------|
| ... | ... | ... |
