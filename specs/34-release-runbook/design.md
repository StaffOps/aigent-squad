# Design: Release Runbook

## Shape of RELEASE.md (phases)

```
0. Pre-flight        CI green on dev (gh run list) · spec-status gate (spec 32 script) ·
                     CHANGES [Unreleased] accurate · bump justified (version-management;
                     current gate read from ROADMAP)
1. Merge             PR dev → main (guard job enforces source branch) · CI on main green ·
                     docs site auto-publishes from main (verify)
2. Tag + image       git tag vX.Y.Z → release.yml (scan-gated multi-arch + SBOM + Release) ·
                     verify the tag's image digest CONTAINS this milestone's code
                     (0.2.0 lesson: tag published without the gateway)
3. Chart             [repo: helm-charts] bump version + appVersion=X.Y.Z → chart-releaser ·
                     revert any local-path override in helmfile
4. Overlay           [repo: k8s-setup] point at published chart · remove temporary tag pins
                     (or record the exception + revert step) · pullPolicy IfNotPresent
5. Rollout           helmfile apply · watch rollout (2/2) · pods run the EXPECTED digest
6. Homologation      smoke list: /ready · 1 real query per critical agent · 1 injection
                     probe → expect 403 · admission headers (X-RateLimit/X-Budget) ·
                     MCP round-trip
7. Close             CHANGES cut [X.Y.Z] · HANDOFF overwrite (spec 32) · run operational
                     review (spec 33) · revoke temporary credentials · update ROADMAP
                     "suggested real version"
```

## Rationale (decisions)

### Decision 1: a runbook that sequences existing CI, not new automation

**Choice**: RELEASE.md orchestrates release.yml/build.yml/chart-releaser as they are.

**Justification, in order of strength**:
1. **The failures were sequencing/memory failures, not mechanics failures.** Scan-gated
   publish, guard job and chart-releaser all worked; what broke was cross-repo ordering
   (chart appVersion vs overlay tag) and post-release hygiene (PAT revocation) — things
   only a checklist spanning three repos can hold.
2. Zero new surface to maintain; the runbook can be executed by a human or an agent
   session verbatim.
3. Matches specs 32/33 philosophy: fixed shape first, automation only if skipped in
   practice.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Manual steps can still be skipped | Each phase ends with a verifiable check (digest match, 403 probe) — skipping is detectable at the next phase |
| Checklist rots if CI changes | CI changes are rare and runbook-visible (the dry-run task re-validates) |

**When this would be wrong**: release frequency grows beyond ~1/week — then script the
cross-repo steps (a `scripts/release.sh` driving gh/helm), keeping RELEASE.md as the spec.

### Decision 2: coherence rule "appVersion = released tag; overlay pins are exceptions with a revert step"

**Choice**: one sentence-rule, stated once, with the real 0.3.0-dev incident as the
worked example.

**Justification**: the 0.3.0 cycle showed three tag surfaces (git tag, chart appVersion,
overlay pin) drifting independently. A rule + example beats a diagram: whoever releases
checks three values are equal, or records the exception inline where the next release
will find it.

### Decision 3: credential hygiene is a release step, not an incident response

**Choice**: "revoke temporary tokens/PATs used this cycle" is a standing checklist item.

**Justification**: the 2026-07-01 handoff carried "revoke the two PATs pasted in chat" as
a TODO for days. Secrets exposure should never depend on someone re-reading a handoff;
making it a release-close step bounds the exposure window to one cycle at worst.

## Invariants

- No release without the spec-32 status gate green (consistent specs = trustworthy CHANGES).
- `main` never receives direct pushes — PR from `dev` only (guard job; manual discipline
  until branch protection is available).
- A tag, once published, is immutable — a bad tag gets a successor (X.Y.Z+1), never a
  re-tag (matches "no latest in prod" / immutable tags from spec 08).
- Homologation always includes at least one NEGATIVE probe (403 on injection) — a release
  that only tests happy paths doesn't validate the security posture.

## Verification

- Dry-run (T4): map every ad-hoc action of the 0.3.0 cycle (HANDOFF 2026-07-01/03
  sections) onto a runbook step; zero orphans allowed.
- Next real release (0.4.0, after spec-14 A/B/D) executes RELEASE.md verbatim; friction
  found = edits in the same PR.

## Risks

- Runbook drifts from reality → the spec-33 review's release-anchored cadence re-touches
  it every cycle.
- Over-specification (steps too rigid for a solo dev) → keep every step a single command
  or a single verifiable check; anything longer is a smell to fix in the runbook itself.
