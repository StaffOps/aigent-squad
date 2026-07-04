# Design: Operational Review Loop

## Architecture (the closed loop)

```
 build stage (existing)                operate stage (NEW)
 requirements → design → tasks         devops-core runtime
      → verify → done                        │ emits aigent.* metrics + AIP cost tags
            ▲                                ▼
            │                     ┌─────────────────────────┐
   promote phase / open spec ◄────│  Operational Review      │  monthly + per milestone
   (ONLY via measured trigger)    │  1. TRIGGERS.md sweep    │
            ▲                     │  2. cost reconciliation  │
            │                     │  3. BACKLOG triage       │
   close / keep-dormant ◄─────────│  → specs/reviews/YYYY-MM │
                                  └─────────────────────────┘
```

## Components

| Component | Responsibility | Where |
|-----------|----------------|-------|
| Trigger catalog | Every trigger + threshold + measurement query + gap flag | `specs/TRIGGERS.md` |
| Review template | Fixed comparable structure | `specs/reviews/TEMPLATE.md` |
| Review records | Dated evidence + decisions | `specs/reviews/YYYY-MM.md` |
| Cost recipe | CE-by-tag filter + MetricsQL attribution (from spec 27) | TRIGGERS.md appendix |
| Triage target | Findings + deferred register | `specs/BACKLOG.md` (spec 32) |

## Rationale (decisions)

### Decision 1: a checklist-driven review, not automation

**Choice**: the review is a template a human/agent executes, producing a dated record.
No cron, no bot, no dashboard requirement.

**Justification, in order of strength**:
1. **The bottleneck is that nobody looks, not that looking is slow.** Every input already
   exists (metrics emit, CE has tags); the missing piece is an obligation with a fixed
   shape. A checklist creates that for ~zero build cost.
2. **Dogfoods the project's own philosophy**: automate only after the manual loop proves
   value (same reasoning that kept RCA single-round until triggers fire).
3. An agent (Claude Code session) can execute the template end-to-end — the record format
   IS the automation interface if it's ever scripted.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| Depends on discipline to run it | Mitigated: cadence anchored to releases (spec 34 runbook includes "run the review") + monthly slot |
| Manual queries each time | Copy-pasteable recipes in TRIGGERS.md; ~30min/review |

**When this would be wrong**: if reviews are skipped 2+ cycles in a row — then a scheduled
agent (CronCreate/routine) should run it and post the draft record.

### Decision 2: TRIGGERS.md centralizes measurements; specs keep owning the triggers

**Choice**: the trigger *statement* stays in its spec (context lives there); TRIGGERS.md
holds the operational view (threshold + query + last measured value's location).

**Justification**: the review needs one sweepable surface (same argument as BACKLOG.md);
duplicating full context would rot. A one-line row per trigger with a link is the minimum
that makes the sweep mechanical.

**Trade-off accepted**: one more file to keep in sync — bounded: rows change only when a
spec adds/retires a trigger, and spec 32's README makes "add the TRIGGERS.md row" part of
the definition-of-done for any spec that defines a trigger.

### Decision 3: promotion ONLY via measured trigger (make the implicit rule enforceable)

**Choice**: the review record is the required citation for opening any dormant phase
(RCA multi-round, Opus tiers, embeddings-based skills, spec 25 resurrection, Levels 2–4).

**Justification**: this was always the project's stated principle ("each level proves
value before advancing", "promotion triggers are measurable — not 'seems like we need
it'") — but with no measurement ritual it was unenforceable. The review record makes it
citable: a phase-opening spec links the review that shows the threshold crossed.

**When this would be wrong**: a production incident that demands immediate phase work —
the review is a gate for *planned* promotion, never an excuse to delay incident response
(document the exception in specs/README.md).

## First review (dogfood) — measurement plan

| Trigger | How to measure now |
|---------|--------------------|
| RCA multi-round need | `aigent.investigation.rounds` histogram vs count of investigations whose RCA confidence = low |
| Haiku routing accuracy | classifier confidence distribution + fallback-parse rate (logs) — flag gap if no counter |
| Cost model divergence | CE filtered by `CostProject=aigent-squad` (AIP tags) vs ANALYSIS tables; per-agent split via `aigent_cost_estimated` MetricsQL (spec 27 design) |
| Cache effectiveness | `aigent.cache.hits/misses` by namespace (informs TTL tuning) |
| Gateway pressure (spec 25 signals) | `aigent.gateway.pool_rejections`, `pool_depth`, `rate_limit.blocks` |

## Invariants

- Every trigger row has a measurement source or an explicit `GAP:` flag — no unmeasurable
  trigger silently persists.
- Review records are append-only, dated, and never edited after the fact.
- The review never *implements* fixes — it opens/updates specs and BACKLOG rows
  (separation identical to RCA "propose, never remediate").
- A dormant item is only promoted with a review citation.

## Verification

- First review record exists with real numbers from devops-core (or explicit GAP rows).
- Each TRIGGERS.md row resolves: run its query verbatim → a value or a clean gap.
- Negative check: BACKLOG item promoted without review citation should be rejected in
  review (process check, exercised in the dogfood run).

## Risks

- Review theater (record produced, decisions ignored) → the Decisions section requires a
  spec/BACKLOG reference per row; empty Decisions with crossed thresholds is a red flag
  the next review must call out.
- Metric gaps make the first review thin → expected and fine: surfacing gaps WITH evidence
  is the deliverable that finally rightsizes specs 15/16.
