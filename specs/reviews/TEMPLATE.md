# Operational Review — YYYY-MM

## 1. Review Metadata

| Field | Value |
|-------|-------|
| **Date** | YYYY-MM-DD |
| **Reviewer** | (name or agent session) |
| **Milestone** | (release tag or "monthly cadence") |
| **Scope** | (all triggers / subset — justify if subset) |

---

## 0. Follow-up of the previous review's decisions

Anti-review-theater check (spec 33 design): every decision in the previous review's §5
gets a line here. A decision that was ignored is called out, not silently re-made.

| Prev. # | Decision | Done? | Evidence / why not |
|---------|----------|-------|--------------------|
| 1 | ... | ✅ / ❌ / partial | (PR, commit, or reason) |

---

## 2. Trigger Measurements

Source: `specs/TRIGGERS.md` — **one row per catalog row, in the same order**. Read the
"Query conventions" section there first (list the real metric names before querying).
Paste the query you actually ran; "Window" is the range you used (default `[30d]`).

| Trigger (source spec) | Query run verbatim? | Window | Measured value | Threshold | Status |
|-----------------------|---------------------|--------|----------------|-----------|--------|
| L1→L2: single-round insufficient (VISION) — proxy: `baixa` share | yes / edited (paste) | 30d | __% | >30% | ⬜ / 🟡 / 🔴 / GAP |
| L2→L3: cross-agent context helps (VISION) | — | — | NOT MEASURABLE (G7) | >20% | GAP |
| L3→L4: real autonomy needed (VISION) | — | — | NOT MEASURABLE (L3 not built) | qualitative | GAP |
| Haiku routing accuracy (spec 11) — mean confidence per tier as drift proxy | | 30d | fast __ / standard __ / deep __ | undefined (G8) | GAP / drift note |
| Synthesizer Sonnet→Opus (spec 11) | — | — | NOT MEASURABLE (G7) | >40% at L3+ | GAP |
| Agent-as-tools depth rejected (spec 17) | — | — | NOT MEASURABLE (G3) | frequency of rejections | GAP |
| Fan-out max_agents=3 ceiling (spec 17) | — | — | NOT MEASURABLE (no agents_requested metric) | >3 agents frequently | GAP |
| Multi-round needed (spec 18) — same proxy as L1→L2 | | 30d | __% | >30% | ⬜ / 🟡 / 🔴 |
| Opus enricher demotion (spec 21) | — | — | NOT MEASURABLE (G4) | >60% | GAP |
| Gateway saturation (spec 25) — Bedrock queue_wait p99 | | 30d | __ s | >5 s | ⬜ / 🟡 / 🔴 |
| Gateway saturation (spec 25) — session_lock timeouts | — | — | NOT MEASURABLE (G6) | >1% | GAP |
| Skills keyword-miss (spec 26) | — | — | NOT MEASURABLE (G5) | undefined (G8) | GAP |
| Evidence confidence skew (EVIDENCE-MODEL §4) — `baixa` share | | 30d | __% | >50% | ⬜ / 🟡 / 🔴 |
| Gateway pressure (spec 31) — pool rejections / max pool depth / rate-limit blocks | | 30d | __ / __ / __ | sustained non-zero | ⬜ / 🟡 / 🔴 |
| Security-layer overhead (BACKLOG B-08) | — | — | NOT MEASURABLE (G10) | undefined (G8) | GAP |

**Legend**: ⬜ = well below threshold; 🟡 = approaching (>70% of threshold); 🔴 = crossed → action required; GAP = not measurable today (cite the gap id). No other status symbols.

**Update this table when `TRIGGERS.md` gains or loses a row** — the two must stay 1:1.

For triggers marked GAP in `TRIGGERS.md`: state "NOT MEASURABLE — gap G{N}" and skip.

---

## 2b. Model-version refresh (BACKLOG B-10)

| Role / tier | Model id in use (config) | Newest available inference profile | Decision |
|-------------|--------------------------|------------------------------------|----------|
| classifier / fast | | | keep / evaluate / switch (cite eval run) |
| agents / standard | | | ... |
| synthesis | | | ... |
| deep | | | ... |

A switch needs an eval-harness run (`scripts/eval_squad.py`) with no regression before it lands.

---

## 3. Cost Reconciliation

| Metric | Value |
|--------|-------|
| **Actual monthly Bedrock cost** (Cost Explorer, tag `CostProject=<value from get-tags>`, Service = Amazon Bedrock) | $____ |
| **ANALYSIS.md estimate for current level** | $____ |
| **Divergence** | __% |
| **Status** | ✅ Within 30% / ⚠️ Divergence >30% |

If divergence >30%, fill:

| Driver | Finding |
|--------|---------|
| Top agent by cost | (agent_id, % of total) |
| Query volume vs assumption | (actual/day vs 200/day) |
| Prompt caching working? | NOT MEASURABLE from metrics today (G9) — check a Bedrock invocation log sample for `cache_read_input_tokens` > 0 |
| Action taken | (updated ANALYSIS.md / config change / further investigation) |

---

## 4. Findings & Deferred Triage

Review `specs/BACKLOG.md` — **Findings (F-\*)**, Deferred register and Dormant section.

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
