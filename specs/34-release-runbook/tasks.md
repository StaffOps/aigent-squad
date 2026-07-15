# Tasks: Release Runbook

> Depends on spec 32 (status gate as pre-flight) and spec 33 (review as a close step).
> Verification pipeline per `specs/README.md`.
> **Note (2026-07-15): specs 32/33/README.md don't exist/aren't done yet** —
> executed T1-T6 anyway (out of the stated dependency order) because the user
> asked to cut 0.4.0 now. `RELEASE.md` documents both gaps honestly (manual
> pre-flight status check instead of a script; HANDOFF stays append-only
> instead of "overwrite" until spec 32 defines that rule) rather than
> blocking on specs that don't exist. Revisit T2/T7's close-phase steps once
> 32/33 ship for real.

- [x] T1: Write `RELEASE.md` — phases 0–7 per design; every step a command or a single
      verifiable check; cross-repo steps labeled `[helm-charts]` / `[k8s-setup]`
      (2026-07-15).
- [x] T2: Embed the version-bump rule + the "current bump gate" pointer to ROADMAP
      (depends on: T1). **`steering/version-management.md` doesn't exist** — not in
      this repo, not found anywhere on this machine, despite `steering/project.md`
      referencing it as "global steering". Documented the gap in `RELEASE.md`
      directly; used `steering/milestone-criteria.md` + the historical bump
      pattern (0.3.0 gated on "cluster-validated", not a feature list) as the
      operative local rule instead.
- [x] T3: Homologation smoke list (health · per-agent real query · injection
      probe → 403 · admission headers · MCP round-trip), reusing the actual probes
      from the 2026-07-01/03/15 homologation sessions (depends on: T1).
- [x] T4: Dry-run validation — mapped every ad-hoc action from the 0.3.0 cycle
      (HANDOFF `session 2026-07-01` + `session 2026-07-02 → 2026-07-03`) onto a
      runbook step; zero orphans (0.2.0 cycle's HANDOFF section — commit
      `ba13399` era — was thinner on detail, 0.3.0 alone was sufficient to
      exercise every phase). Two real gaps surfaced, recorded as BACKLOG-worthy
      findings inline in RELEASE.md's dry-run section, not silently dropped:
      (1) Harbor (private, manual push) vs Docker Hub (public, `release.yml`) are
      two DIFFERENT image-publish paths that have NEVER been reconciled — every
      cycle so far, including 2026-07-15's, released to Harbor only; (2)
      `helmfile diff`/`apply` broken locally (helm-diff plugin vs Helm v4 CLI
      incompatibility, found 2026-07-15) — Phase 5 documents a fallback, the
      underlying tooling issue itself stays open.
- [x] T5: Cross-references — `AGENTS.md`'s "Key references" table now points at
      `RELEASE.md` (depends on: T1). `specs/README.md` doesn't exist to
      cross-reference from the other direction.
- [x] T6: Independent review (fresh-context subagent, `code-review` type) —
      sequencing checked against the real `test.yml` guard job, `release.yml`,
      and `helm-charts`' `release.yaml` (chart-releaser); coherence rule and
      credential step verified (depends on: T4, T5). 8 findings, all fixed same
      day: **F1** (Medium-High) dry-run table misattributed the "neutralize
      image.repository" TODO to the wrong repo/phase, AND the underlying item
      was never done/never tracked — corrected the table, added `specs/
      BACKLOG.md` B-27. **F2** (Medium) the two disclosed tooling gaps lived
      only in RELEASE.md prose, no BACKLOG anchor — added B-25 (Harbor vs
      Docker Hub split) and B-26 (helm-diff/Helm-v4 incompatibility). **F3**
      (Medium) "found twice already" overstated a single correction event —
      reworded. **F4** (Medium) credential-hygiene step was sandwiched between
      two explicitly-inactive steps, risking a skim-skip — moved to Phase 7
      position 1. **F5** (Medium-High) the coherence rule didn't say which
      artifact wins on divergence, and undersold that the overlay-pin
      "exception" has in practice been the ONLY mode ever used — made both
      explicit. **F6** (Low-Medium) Phase 5's fallback conflated "renders
      cleanly" with "confirmed unchanged" — clarified. **F7** (Low) the guard
      job's "fails closed" reused a term-of-art loosely (a stricter, different
      meaning elsewhere in this repo) — reworded to "fails loudly" with an
      explicit note that no branch protection backs it. **F8** (Low) Phase
      1.5 missed `docs.yml`'s `mkdocs.yml` trigger path — added.
- [ ] T7: execute `RELEASE.md` verbatim on the 0.4.0 release; fold friction back
      into the runbook in the same PR. **This is now** (2026-07-15) — in progress.

## Order
T1 → T2/T3 → T4 → T5 → T6; T7 rides the next real release.

## Notes
- The runbook sequences EXISTING CI — any CI gap found in T4 is a BACKLOG item, not scope.
- Immutable tags: a bad release gets X.Y.Z+1, never a re-tag.
- Credential hygiene (revoke cycle-temporary tokens) is a standing close step — never a
  handoff TODO again.
