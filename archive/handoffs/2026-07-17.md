# Handoff

> **Overwrite rule (spec 32, Decision 4)** — this file holds **only the current session
> + next steps**. It is *overwritten*, never appended. At the start of each new session's
> handoff write, the previous content moves to `archive/handoffs/YYYY-MM-DD.md`. Permanent
> history lives in `CHANGES.md`; per-spec status lives in spec frontmatter + the canonical
> table in `specs/ROADMAP.md` — not here.
>
> Prior sessions (2026-06-16 → 2026-07-16): `archive/handoffs/2026-07-16.md`.

---

## Current session — 2026-07-17 (spec 32 `spec-lifecycle-ssot`, Phases 1 + 2)

Implemented spec 32 end-to-end except its own closing review. **Nothing committed** —
the user is deliberately stacking more work before a commit (asked explicitly not to
commit yet).

### Phase 1 — SSOT + gate (T1–T6, done)
- **T2**: YAML frontmatter backfilled on all 28 `requirements.md`-bearing specs (bugfix-tier
  01/03 have none by design). Statuses derived from the ROADMAP ground-truth tables
  (Audit 2026-06-14 + Remaining 2026-07-03), **not** from `tasks.md` checkbox counts
  (measured drifted — see Decision 5 below).
- **T4**: `scripts/specs_status.py` (146 LoC, PyYAML+stdlib). Validates status vocabulary,
  `superseded`→`superseded_by`, `done`↔`deferred[]` consistency, `deferred[]`↔`BACKLOG.md`
  cross-check, and the ROADMAP canonical-table sync. `--table` regenerates the table.
- **T5**: `make specs-status` + the `specs_status` CI job (`.github/workflows/test.yml`).
- **T6**: `tests/test_specs_status.py` written by an **independent** author (subagent) +
  code-review PASS. 34 tests, 95% coverage of the script.
- **Decision 5** (new, in `specs/32-.../design.md`): the "done-with-open-tasks" check is
  **frontmatter-internal, not a checkbox count** — because checkbox state is
  demonstrably unreliable (specs 23/24 `done` with 0 boxes checked; spec 34 `not-started`
  with all checked). Frontmatter is the SSOT.

### Phase 2 — homes + slim-down (T7–T11 done; T9 = this write)
- **T7**: `specs/BACKLOG.md` already satisfied the criteria (seeded 2026-07-04) — Findings
  (F-001…), Dormant (finops↔Athena, distributed topology, with triggers), Deferred register.
- **T8**: `specs/ROADMAP.md` slimmed 385→~250 lines. Removed the "Current state" +
  "Audit Summary" + "Remaining specs" status tables → **one** canonical marker-fenced table
  (`<!-- specs-status:start/end -->`, generated + CI-validated). Backlog→pointer to
  BACKLOG.md. Long-term Vision→`specs/VISION.md` (new).
- **T10**: `Status: frozen (2026-06-02)` banners on `specs/ANALYSIS.md`, `ECOSYSTEM.md`,
  `EVIDENCE-MODEL.md`.
- **T11**: `AGENTS.md` — Phase-status table → pointer; Workflow-rules now points spec-process
  to `specs/README.md` (SSOT); Key-references updated.
- **T9**: this HANDOFF restructure (history archived, overwrite rule stated above).

## Next steps

1. **T12 (spec 32 close) — independent review**, still open. It must catch + fix the
   **residual status left outside frontmatter** in `specs/ROADMAP.md` (deliberately not
   pre-cleaned so the review is real):
   - the "Cross-domain analysis (2026-06-02) — proposed new specs" table has a per-spec
     status column;
   - the Phase 0 table's "Severity" column shows `✅ done`;
   - the work-order block has inline `✅ done` markers.
   Also verify: gate effective (it is — `make specs-status` rc=0), BACKLOG complete vs the
   2026-07-03 deferred inventory. Run as a `documentation`/`code-review` subagent for
   independence, then neutralize the flagged residuals.
2. **Commit decision** — Phase 1 + Phase 2 are one coherent change; commit when the user
   says so (deferred on purpose). Suggested message:
   `feat(specs): spec 32 — frontmatter status SSOT, validator gate, ROADMAP slim-down`.
3. After spec 32 closes: next roadmap items are **spec 36** (agent-native dev loop, flagged
   "do first") and **spec 18 Phase 1.5** (EVIDENCE-MODEL correlator). Full open inventory:
   `specs/ROADMAP.md` + `specs/BACKLOG.md`.

## Verify state on resume

```bash
git status -s                      # 30+ modified specs, new scripts/tests, HANDOFF moved
make specs-status                  # must print OK, rc=0
```
