---
spec: 32-spec-lifecycle-ssot
status: done
completed: 2026-07-17
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Spec Lifecycle SSOT (status frontmatter + process doc + backlog home)

**Spec**: `32-spec-lifecycle-ssot`
**Severity**: 🔴 Critical (process — the recurring status drift corrupts every other doc)
**Origin**: process analysis 2026-07-03 (full read of all 81 specs/ files). Direct evidence
of the failure mode: specs 23/24 marked "Not started" in one ROADMAP table while the state
table said "Docs ✅ MkDocs site"; spec 11 "Not started" and "✅ done" in two tables of the
same file; spec 29 shipped with every task unchecked; HANDOFF asserting "dev and main in
sync" three sections above commits proving otherwise.
**Depends on**: — (can start immediately; blocks nothing, de-risks everything)

---

## Thesis

Spec **status** currently lives in ≥4 places (each spec's `tasks.md`, two ROADMAP tables,
HANDOFF, README) with no single source of truth and no mechanical check — so they drift
apart on every milestone. The process rigor is front-loaded (birth of a spec) with almost
none invested in maintenance (life after "done"). This spec makes each spec directory the
**single source of truth for its own status**, makes every other surface derive from it,
and adds the missing homes: a process README (the lifecycle is currently implicit), and a
BACKLOG (findings and deferred work currently evaporate — e.g. the aws-agent
`<use_mcp_tool>` XML echo bug has no tracking home at all).

## User Stories

WHEN anyone (human or agent) needs the status of a spec THEN it SHALL be readable from
that spec's own frontmatter, without cross-referencing ROADMAP/HANDOFF.

WHEN a spec's tasks.md still has open, non-deferred checkboxes THEN CI SHALL fail if the
spec's frontmatter claims `status: done`.

WHEN the ROADMAP references spec status THEN it SHALL contain exactly ONE status table,
consistent with the frontmatter (validated, or generated).

WHEN a completed spec leaves deferred tails (smoke tests, k6, Opus enricher, …) THEN they
SHALL be listed in the frontmatter (`deferred:`) and aggregated in `specs/BACKLOG.md` —
never buried only in prose notes.

WHEN an operational finding appears that belongs to no open spec (e.g. the aws-agent XML
echo) THEN it SHALL get an ID (`F-NNN`) and a row in `specs/BACKLOG.md`.

WHEN a new contributor (or AI agent) asks "how does the spec process work here" THEN
`specs/README.md` SHALL answer it: lifecycle, template, status vocabulary, two spec tiers
(full spec vs `bugfix.md`), verification pipeline (written ONCE, referenced by specs),
when security review is mandatory, numbering and language conventions.

WHEN a session ends THEN `HANDOFF.md` SHALL be **overwritten** (last session + next steps
only), with previous sessions archived — never appended into a contradictory palimpsest.

WHEN a root specs/ document is a frozen deliberation (ANALYSIS, ECOSYSTEM, EVIDENCE-MODEL)
THEN it SHALL carry a `Status: frozen (date)` banner so readers don't mistake it for
current state.

## Acceptance Criteria

- [ ] Frontmatter schema defined and documented: `status` (one of `not-started |
      design-only | in-progress | done | done-with-deferrals | superseded | dormant |
      removed`), `completed:` (date), `superseded_by:`, `depends_on:`, `deferred: []`.
- [ ] Frontmatter backfilled on **all** existing spec dirs (01–31), values reconciled with
      the real tasks.md state (not the ROADMAP's claims).
- [ ] `scripts/specs_status.py`: parses frontmatter + checkbox state; exits ≠0 on any
      inconsistency (done-with-open-tasks, unknown status value, ROADMAP row mismatch,
      superseded spec without `superseded_by`); can emit the status table (`--table`).
- [ ] CI runs the script as a gate (job in `test.yml` or a small dedicated workflow).
- [ ] `specs/README.md` exists covering: lifecycle (including the operate/measure stage
      from spec 33), the 3-file template, status vocabulary, full-spec vs bugfix tiers,
      the verification-independence pipeline written once, the mandatory-security-review
      rule (any spec touching an input surface, auth, or data egress), numbering,
      language convention (PT = frozen historical, EN = new), and the document homes
      adopted from `template-project` (PRD → `docs/prd/`, ADRs →
      `docs/architecture/decisions/` — see the 2026-07-03 backfill: PRD + ADRs 0002–0006;
      new architectural decisions get an ADR there, not only a design.md section).
- [ ] `specs/BACKLOG.md` exists with three sections: **Dormant** (moved from ROADMAP:
      finops↔Athena, distributed topology — with their existing blocker-triggers),
      **Findings** (`F-001`: aws agent echoes raw `<use_mcp_tool>` XML instead of
      executing — from 2026-07-03 homologation), **Deferred register** (aggregated from
      frontmatter `deferred:`; includes at minimum: specs 06/17/18 T11 smoke, spec 08
      T7 demo stage, spec 21 Opus enricher + Slack approval flow, spec 31 T21 k6 + T23
      final review).
- [ ] ROADMAP slimmed to plan-only: single status table (validated by the script),
      backlog section replaced by a pointer to BACKLOG.md, "Long-term Vision" moved to
      `specs/VISION.md`.
- [ ] HANDOFF.md restructured: current session + next steps only; prior sessions moved to
      `archive/handoffs/`; the overwrite rule stated at the top of the file.
- [ ] `Status: frozen (date)` banners on ANALYSIS.md, ECOSYSTEM.md, EVIDENCE-MODEL.md
      (AUDIT.md already has one).
- [ ] AGENTS.md workflow section updated to reference `specs/README.md` as the process
      SSOT (no duplicated rules).

## Out of scope

- The operational review cadence itself (measuring triggers, cost reconciliation) → spec 33.
- The release runbook → spec 34.
- Retro-translating PT specs to EN (explicitly frozen — convention documented instead).
- GitHub Issues migration — the file-based flow is deliberate (solo dev, agent-friendly);
  revisit only if a second regular contributor joins.
