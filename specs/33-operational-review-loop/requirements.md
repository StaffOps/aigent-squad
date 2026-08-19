---
spec: 33-operational-review-loop
status: done-with-deferrals
completed: 2026-08-19
superseded_by: null
depends_on: ["32-spec-lifecycle-ssot"]
deferred: []
---

# Feature: Operational Review Loop (measure triggers · reconcile cost · triage findings)

**Spec**: `33-operational-review-loop`
**Severity**: 🔴 High (the process ends at "shipped" — nothing closes the loop now that
the system runs in a real cluster)
**Origin**: process analysis 2026-07-03. Evidence: promotion triggers defined across specs
11/17/18/21/26 are measured by nobody (several already have the metric emitting, e.g.
`aigent.investigation.rounds`); the ROADMAP milestone checklist item "real costs vs
estimates (adjust ANALYSIS.md if >30% divergence)" has never been executed even though
spec 27 ships exactly that data; the spec-14 homologation produced findings A–D *after*
the spec was "✅ done" with no process slot to receive them.
**Depends on**: `32-spec-lifecycle-ssot` (BACKLOG.md is the triage target)

---

## Thesis

The spec lifecycle is build-centric: requirements → design → tasks → verify → done. Since
2026-07-01 the squad runs in devops-core, which creates a class of work the lifecycle has
no stage for: **operating evidence**. Promotion triggers were the project's mechanism for
deciding "when is phase 2 justified" — but a trigger nobody measures is a dead letter, and
phase-2 decisions silently regress to intuition. This spec adds the missing stage: a
recurring, checklist-driven operational review that (a) measures every active promotion
trigger, (b) reconciles real Bedrock cost against the ANALYSIS estimates, and (c) triages
findings/deferred items in BACKLOG.md. Output: a dated review record, so decisions cite
numbers.

## User Stories

WHEN a spec defines a promotion trigger THEN it SHALL also name the metric (or query)
that measures it — a trigger without a measurement source is an incomplete spec.

WHEN the review runs THEN every trigger in the catalog SHALL get a measured value (or an
explicit "not yet measurable — gap: <metric>"), recorded with date.

WHEN a measured trigger crosses its threshold THEN the review SHALL open (or resurrect)
the corresponding spec/phase — that is the ONLY sanctioned path for promoting a dormant
phase (no intuition-driven promotion).

WHEN the review runs THEN real Bedrock cost (Cost Explorer via AIP tags + a MetricsQL
attribution split per `aigent.cost.estimated`) SHALL be compared against the ANALYSIS.md
cost model; a divergence >30% updates the model (per the existing milestone checklist).

WHEN the review runs THEN BACKLOG.md findings and the deferred register SHALL be triaged:
each item either gets pulled into a spec, stays with an unexpired trigger, or is closed
with a one-line reason.

WHEN a review completes THEN a dated record SHALL exist at `specs/reviews/YYYY-MM.md`
following a fixed template — reviews are comparable across time.

## Acceptance Criteria

- [ ] `specs/TRIGGERS.md` — catalog of every promotion/demotion trigger currently defined,
      each row: source spec · trigger statement · threshold · measurement (metric name or
      MetricsQL/CE query) · measurable today? (gap flagged if not). Initial inventory at
      minimum: RCA multi-round (spec 18: >30% insufficient single-round;
      `aigent.investigation.rounds`), Haiku routing accuracy (spec 11), synthesizer
      Sonnet→Opus (spec 11: low confidence >40% at L3+), Opus enricher demotion (spec 21:
      no added value >60%), skills keyword-miss → embeddings (spec 26), agent-as-tools
      depth>1 (spec 17), shared-context Level 3 (>20% of cases — VISION), Level-2 gate
      from EVIDENCE-MODEL, spec-25 resurrection (gateway saturation / multi-replica
      contention signals).
- [ ] `specs/reviews/TEMPLATE.md` — fixed sections: Trigger measurements · Cost
      reconciliation · Findings/deferred triage · Decisions (with spec refs) · Gaps.
- [ ] Cadence defined and documented (recommended: monthly, plus once per release
      milestone) in specs/README.md lifecycle.
- [ ] Cost reconciliation recipe written down (the exact CE filter by `CostProject` tag +
      the MetricsQL per-agent attribution query from spec 27's design) — copy-pasteable.
- [ ] **First review executed** as part of this spec (dogfood): real measurements from
      devops-core, first `specs/reviews/2026-07.md` produced, BACKLOG triaged once.
- [ ] Metric gaps discovered by the first review are recorded in TRIGGERS.md as gaps
      (each with the ~1-metric fix suggestion) — implementing them is out of scope here.
- [ ] specs/README.md (from spec 32) lifecycle diagram includes the operate/measure stage
      pointing at this loop.

## Out of scope

- Implementing missing metrics (each gap becomes a small follow-up task/spec — the review
  exists precisely to surface them with evidence).
- Automated/scheduled execution (a human- or agent-run checklist first; automation only
  if the manual loop proves its value — same promotion-trigger philosophy).
- SLI/SLO framework (spec 15) and runbooks (spec 16) — the review's measurements are the
  evidence that will justify (or keep deferring) them.
