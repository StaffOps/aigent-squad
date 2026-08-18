---
spec: 34-release-runbook
status: done-with-deferrals
completed: 2026-07-15
superseded_by: null
depends_on: ["32-spec-lifecycle-ssot", "33-operational-review-loop"]
deferred: ["RELEASE.md Phases 3-5 (chart bump / overlay / rollout) — gated on B-25"]
---

# Feature: Release Runbook (repeatable milestone → tag → chart → cluster path)

**Spec**: `34-release-runbook`
**Severity**: 🟠 High (releases are currently artisanal; each one re-invents its checklist)
**Origin**: process analysis 2026-07-03. Evidence from the 0.2.0 and 0.3.0 cycles: the
2026-07-01 milestone checklist was invented ad hoc in HANDOFF ("publish chart 0.9.0,
revert the helmfile local-path override, neutralize gateway.image.repository, revert
overlay pullPolicy, **revoke the two PATs pasted in chat**"); a chart `appVersion` vs
running-tag mismatch had to be diagnosed in-cluster (fixed by pinning `tag: 0.3.0-dev` in
the overlay); the `0.2.0` image was published WITHOUT the gateway while `latest` had it;
docs publish only from `main` so the public site lags `dev` until someone remembers the PR.
**Depends on**: `32-spec-lifecycle-ssot` (status gate), `33-operational-review-loop`
(review as a release step). Steering: `version-management`.

---

## Thesis

Every release so far worked, but only because the same person carried the checklist in
their head — and the near-misses (leaked PATs pending revocation, image/tag skew, private
Docker Hub repo in public values) are exactly the kind of step a written runbook makes
un-forgettable. The project already has strong release *mechanics* (scan-before-publish,
tag-driven release.yml, chart-releaser, guard job on `main`); what's missing is the
*orchestration document* that sequences them, including the cross-repo steps
(app ↔ helm-charts ↔ k8s-setup overlay) that CI cannot see.

## User Stories

WHEN a milestone is ready on `dev` THEN there SHALL be a single ordered checklist
(`RELEASE.md`) covering: pre-flight → PR `dev→main` → tag → image → chart → overlay →
rollout → homologation → docs/changelog → post-release cleanup.

WHEN a release starts THEN pre-flight SHALL verify: CI green on `dev` (`gh run list`),
spec statuses consistent (spec-32 gate), CHANGES.md `[Unreleased]` accurate, and the
version bump justified by a measurable result (per `version-management` — e.g. 0.4.0
gates on spec-14 findings A/B/D closed).

WHEN the image is published THEN the runbook SHALL make tag/appVersion/overlay coherence
explicit: chart `appVersion` = the released app tag; overlays pin a tag ONLY as a
temporary exception, with a listed revert step.

WHEN the release touches the sibling repos THEN the cross-repo steps SHALL be in the
checklist (helm-charts: bump + chart-releaser publish + revert any local-path override;
k8s-setup: overlay values, pullPolicy back to `IfNotPresent`).

WHEN the rollout lands THEN homologation SHALL follow a written smoke list (health,
real query per critical agent, one security probe expecting 403, one budget/rate header
check) — the 2026-07-01/03 homologations, made repeatable.

WHEN the release completes THEN post-release SHALL include: CHANGES.md cut, HANDOFF
overwrite (spec 32 rule), operational review run (spec 33), and a **credential hygiene**
step (revoke any temporary tokens/PATs used during the cycle).

## Acceptance Criteria

- [ ] `RELEASE.md` at repo root with the ordered phases above; each step = command or
      exact click-path; cross-repo steps explicitly labeled with their repo.
- [ ] Version-bump decision rule embedded (from `version-management`): bump only on
      measurable result in the target environment; the "next bump gate" is read from
      ROADMAP (currently: 0.4.0 ⇐ spec-14 findings A/B/D closed).
- [ ] Tag/appVersion/overlay coherence rule written, with the failure mode from 0.3.0-dev
      (chart said 0.3.0, cluster ran 0.3.0-dev) as the documented example.
- [ ] Homologation smoke list included (health, per-agent real query, 403 security probe,
      429/503 admission headers, MCP round-trip).
- [ ] Post-release checklist includes: CHANGES cut · HANDOFF overwrite · spec-33 review ·
      credential revocation · confirm docs site published from `main`.
- [ ] Dry-run validation: walk the FULL checklist against the state of the 0.3.0 release
      and confirm every ad-hoc action taken then maps to a step (no orphan actions).
- [ ] AGENTS.md / specs/README.md reference RELEASE.md (release is part of the lifecycle).

## Out of scope

- Changing CI workflows themselves (release.yml/build.yml already implement the
  mechanics; the runbook sequences them). Small gaps found during the dry-run become
  BACKLOG items, not scope here.
- Automated release orchestration (a bot walking the checklist) — same philosophy as
  spec 33: manual with a fixed shape first.
- Branch protection on `main` — still blocked on the GitHub plan (HANDOFF pending #1);
  the runbook notes the manual discipline until then.
