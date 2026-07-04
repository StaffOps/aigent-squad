# Tasks: Spec Lifecycle SSOT

> Highest process priority — every other doc improvement depends on status being trustworthy.
> Verification pipeline per `specs/README.md` (created here — T3): author → independent
> review (docs/process change: no separate test-author needed except for the script, T6).

## Phase 1 — SSOT + gate

- [ ] T1: Define frontmatter schema (status vocabulary, fields) — documented in specs/README.md (T3)
- [ ] T2: Backfill frontmatter on specs 01–31, reconciled against each tasks.md real state
      (NOT against ROADMAP claims). Statuses per the 2026-07-03 full-read ground truth.
- [ ] T3: Write `specs/README.md` — lifecycle (incl. operate/measure stage → spec 33),
      template, status vocabulary, full-spec vs `bugfix.md` tiers, verification pipeline
      (written ONCE), mandatory-security-review rule, numbering + language conventions
- [ ] T4: `scripts/specs_status.py` — parse frontmatter + checkboxes; validate
      (done-with-open-tasks, unknown status, deferred↔BACKLOG cross-check, ROADMAP table
      mismatch); `--table` emits the canonical table (depends on: T1, T2)
- [ ] T5: Wire the script into CI as a gate (test.yml job or dedicated workflow) (depends on: T4)
- [ ] T6 (independent author): tests/review for the script — inconsistency cases exit ≠0,
      consistent tree exits 0 (depends on: T4)

## Phase 2 — Homes for orphaned work

- [ ] T7: `specs/BACKLOG.md` — Dormant (moved from ROADMAP with blocker-triggers intact),
      Findings (seed `F-001` aws `<use_mcp_tool>` XML echo), Deferred register (seed from
      frontmatter: 06/17/18 T11, 08 T7, 21 Opus enricher + Slack approval, 31 T21/T23)
- [ ] T8: ROADMAP slim-down — single validated status table, backlog → pointer to
      BACKLOG.md, Long-term Vision → `specs/VISION.md` (depends on: T4, T7)
- [ ] T9: HANDOFF restructure — current session + next steps only; history →
      `archive/handoffs/`; overwrite rule stated at top
- [ ] T10: `Status: frozen (date)` banners on ANALYSIS.md, ECOSYSTEM.md, EVIDENCE-MODEL.md
- [ ] T11: AGENTS.md workflow section points to specs/README.md (remove duplicated rules)
- [ ] T12: Independent review (docs/process): no status statement left outside frontmatter;
      script gate effective; BACKLOG complete vs the 2026-07-03 deferred inventory
      (depends on: T2–T11)

## Order
T1 → T2/T3 in parallel → T4 → T5/T6; T7 → T8; T9/T10/T11 anytime; T12 closes.

## Notes
- Per `documentation-sync`: ROADMAP/HANDOFF/AGENTS updated in the same change.
- The script stays dependency-free (PyYAML + stdlib), <150 LoC.
- This spec deliberately does NOT touch code paths — zero runtime risk.
