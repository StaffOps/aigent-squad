# Tasks: Operational Review Loop

> Depends on spec 32 (BACKLOG.md as triage target; README lifecycle hook).
> Verification pipeline per `specs/README.md`.

## Phase 1 — Catalog + template

- [ ] T1: Inventory every promotion/demotion trigger from specs 11, 17, 18, 21, 25, 26,
      EVIDENCE-MODEL and VISION (Levels 1–4) → `specs/TRIGGERS.md` rows
      (spec · trigger · threshold · measurement query · measurable-today?)
- [ ] T2: Write the cost-reconciliation recipe (CE filter by `CostProject=aigent-squad`
      + spec-27 MetricsQL per-agent attribution) as a TRIGGERS.md appendix (depends on: T1)
- [ ] T3: `specs/reviews/TEMPLATE.md` — Trigger measurements · Cost reconciliation ·
      Findings/deferred triage · Decisions (spec refs mandatory) · Gaps
- [ ] T4: Document cadence (monthly + per release milestone) + the promotion rule
      ("dormant promotes only with a review citation") in specs/README.md lifecycle
      (depends on: spec 32 T3)

## Phase 2 — Dogfood (first real review)

- [ ] T5: Run the first review against devops-core: measure every T1 row (value or `GAP:`),
      run the cost reconciliation, triage BACKLOG.md (depends on: T1–T3)
- [ ] T6: Produce `specs/reviews/2026-07.md` from the template; every Decision row cites a
      spec or BACKLOG id (depends on: T5)
- [ ] T7: Record metric gaps found in T5 back into TRIGGERS.md with a ~1-metric fix
      suggestion each (implementation out of scope) (depends on: T5)
- [ ] T8: Independent review — template completeness, queries reproducible verbatim,
      no unmeasurable trigger left unflagged (depends on: T6, T7)

## Order
T1 → T2/T3 → T4; T5 → T6/T7 → T8.

## Notes
- The review NEVER implements fixes — it opens/updates specs and BACKLOG rows.
- If reviews get skipped 2+ cycles, promote to a scheduled agent run (see design D1
  reopen signal).
- Anchor: spec 34's release runbook includes "run the operational review" as a step.
