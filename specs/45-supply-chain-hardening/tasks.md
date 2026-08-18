# Tasks: Supply-Chain Hardening

**Spec**: `45-supply-chain-hardening`

Ordering rationale: the phases run cheapest-and-safest first. Phase 1 cannot break a build. Phase 2
closes the live exfiltration path and must land before Phase 3, because signing an artifact produced
by a pipeline that can be hijacked signs the wrong thing convincingly.

## Phase status

| Phase | Scope | Status | Done when |
|-------|-------|--------|-----------|
| **0** | Spec + harness + settle Q1–Q4 | `not-started` | Open questions answered with evidence; harness findings folded in |
| **1** | Disclosure + governance | `in-progress` | T1.1–T1.5 DONE 2026-08-12. Open: T1.6 (baseline score — needs the first Scorecard run on `main`), T1.7 (branch protection — repo setting, needs an explicit decision) |
| **2** | CI integrity | `done` | DONE 2026-08-12. 38 refs pinned across 6 workflows, zero mutable remaining; `pin-check` job + `make pin-check` (negative-tested against `@v4.1.0`); explicit `permissions:` in all 6; dead credential write deleted after proving the dep resolves unauthenticated; base image pinned to the OCI index digest with a multi-arch build proving amd64+arm64 still build |
| **3** | Artifact provenance | `done` | `release.yml` split into publish+verify (gate and push still in ONE job); release signed + attested + SBOM attached; pushed manifest scanned; verify job proves it from outside and has a negative test |
| **4** | Hygiene automation | `done` | Suppressions expire; Renovate opens grouped bump PRs |
| **5** | Docs | `done` | Consumer verification doc; `README`/`docs/SECURITY.md` consistent with reality |

---

## Phase 0 — Spec & validation

- [ ] T0.1: Write requirements/design/tasks (this spec) — DONE on merge of this dir.
- [x] T0.2: **Harness** — EXECUTED 2026-08-12 with three independent reviewers (`security`,
      `gitops`, `code-review`), instructed to refute and to verify every claim against the real
      workflow files. Verdicts: CONDITIONAL PASS / CONDITIONAL PASS / COMMIT AFTER FIXES. All
      mechanical findings are folded in above: the missing step `id:`, the required two-job split, the
      wrong `docs.yml` permissions analysis, a pin-check regex that **missed `@v4.1.0`**, the
      `trivyignores:` input name, the missing `files:` input on the release action, the absent `make`
      targets (spec 36), and — most consequential — that the `~/.git-credentials` write is **dead
      code** and the scan/sign architecture asymmetry (D9), neither of which the first draft mentioned.
      `finops` was deliberately not consulted: GitHub Actions minutes on standard runners are free for
      public repositories, and the measured addition is ~60–90s per release with no second multi-arch
      build.
- [ ] T0.3: **BLOCKS Phase 3.** Settle Q1–Q4 from `requirements.md` with evidence, and record the
      answers here:
  - [ ] Q1 — exact attestation action name + major version (read current GitHub docs).
  - [ ] Q2 — Docker Hub referrers vs tag-fallback. **Smoke test required**: push a throwaway tag,
        sign it, then from a clean environment run `cosign verify` and `cosign triangulate`. Do not
        write the real workflow before this passes.
  - [ ] Q3 — whether Scorecard has an SBOM check (read `docs/checks/internal/checks.yaml`).
  - [ ] Q4 — CycloneDX predicate-type URI actually emitted by our Trivy version.
  - [ ] Q5 — how the `.intoto.jsonl` release asset is produced (T3.4).
  - [ ] Q6 — re-check design D6's promotion trigger: `otel-helper` is now a **public** git dependency,
        but `pip --require-hashes` still cannot hash a VCS URL, so hash-pinning stays blocked until it
        is consumed from an index rather than by git URL. Confirm this reading before leaving D6
        deferred.

---

## Phase 1 — Disclosure + governance (cannot break a build)

- [x] T1.1: `.github/SECURITY.md` — supported versions, how to report privately, expected response
      window, disclosure expectations. Must NOT duplicate `docs/SECURITY.md`; that one is the threat
      model and stays. Cross-link both ways so neither looks like the whole story.
- [x] T1.2: Enable **GitHub Private Vulnerability Reporting** on the repo (Settings → Security).
      Record in `T1.1` that it is the preferred channel.
- [x] T1.3: `.github/CODEOWNERS`.
- [x] T1.4: `.github/workflows/scorecard.yml` — `ossf/scorecard-action` pinned by SHA, on
      `push: [main]` + weekly `schedule`, `permissions: id-token: write` + `security-events: write`,
      `publish_results: true`.
- [x] T1.5: Scorecard badge in `README.md`. **Do not gate CI on the score** — several checks are
      permanently unfixable here (`Contributors` needs 3+ orgs; `Fuzzing` has no Python support;
      `Packaging` does not recognise Docker Hub), so a threshold gate would encode noise as policy.
- [ ] T1.6: Record the baseline score in `CHANGES.md` before any Phase 2/3 work, so the delta is
      attributable instead of anecdotal.
- [ ] T1.7: Branch protection on `dev` and `main` (required checks, no force-push). Note: this is a
      repo **setting**, not a file — record what was configured in `docs/SECURITY.md` so it is
      auditable and restorable.

---

## Phase 2 — CI integrity (must land before Phase 3)

- [x] T2.1: Pin **every** `uses:` in all 5 workflows to a 40-char commit SHA with a trailing
      `# vX.Y.Z` comment. Full inventory to convert: `actions/checkout`, `actions/setup-python`,
      `actions/setup-node`, `docker/setup-buildx-action`, `docker/setup-qemu-action`,
      `docker/login-action`, `docker/build-push-action`, `softprops/action-gh-release`,
      `peaceiris/actions-gh-pages`, and `aquasecurity/trivy-action` (**three call sites, all
      `@master`** — a branch ref, the worst case).
- [x] T2.2: `pin-check` — fail if any `uses:` is not a full 40-hex SHA. **Use inverse matching, not a
      denylist of tag shapes.** A first draft used
      `grep -rE 'uses: .*@(v[0-9]|main|master|[a-z-]+)$'`, which **misses `@v4.1.0`** (the alternation
      is `$`-anchored, so a semver tail with dots and digits matches nothing) — i.e. it would have
      passed the very refs we are trying to ban. Correct form:
      `grep -rP 'uses:\s+[^#\s]+@(?![0-9a-f]{40}\b)' .github/workflows/` → any match fails the job.
      Local (`uses: ./...`) and Docker (`uses: docker://...`) refs have no `@<ref>` and are unaffected.
      Add it to `test.yml` so it blocks PRs, **and add `make pin-check`** running the identical command
      (spec 36 same-harness).
- [x] T2.3: Explicit top-level `permissions:` in all 5 workflows, minimum viable:
  - `test.yml`: `contents: read` (currently **absent** → repo default).
  - `docs.yml`: `contents: read` **only**. Verified: the deploy step uses `peaceiris/actions-gh-pages`
    with `personal_token: ${{ secrets.DOCS_DEPLOY_TOKEN }}` pushing to the **external** repo
    `StaffOps/staffops.github.io` — it does not use `GITHUB_TOKEN`, so no `contents: write` is needed.
    (A first draft of this task said to elevate the deploy job; that was wrong and would have granted
    an unnecessary write.)
  - `build.yml` / `sast.yml`: already `contents: read` — verify no job needs more.
  - `release.yml`: `contents: write` for the Release; Phase 3 adds `id-token: write` and
    `attestations: write` **on the publishing job only**. Per-job permissions do override top level, so
    this works.
- [x] T2.4: **Delete** the `~/.git-credentials` write from `test.yml` (design D8) — do not scope it,
      do not convert it to `GIT_ASKPASS`. Order of operations:
  1. Confirm `pip install -r requirements.txt` resolves
     `otel-helper @ git+https://github.com/StaffOps/otel-libs.git@v0.2.0` with **no** auth (the repo
     went public 2026-07-14, `d8dc822`; the `Dockerfile` already removed its credential mount on
     2026-07-15 per BACKLOG B-28).
  2. Remove the credential step and the `DOCS_DEPLOY_TOKEN` env from the `test` job.
  3. Confirm the test job still installs and passes.
      If step 1 fails, STOP and re-cost the dedicated-job option from D8 — do not restore the file
      write.
- [x] T2.5: Pin the base image by **manifest-list** digest in both `Dockerfile` stages. Get it with
      `docker buildx imagetools inspect python:3.11-alpine` and take the top-level index digest —
      **not** a per-platform digest, which would break `linux/amd64,linux/arm64`.
- [x] T2.6: Prove the multi-arch build still produces both platforms after T2.5
      (`docker buildx imagetools inspect` on the resulting tag).

---

## Phase 3 — Artifact provenance (release path)

Every task below is appended **after** the existing Trivy gate. If a step needs to move the gate,
stop: that is the invariant, not an implementation detail.

- [x] T3.0: **Refactor `release.yml` into two jobs** — `publish` (build → scan → push → sign → attest)
      and `verify` (`needs: publish`). Hard constraints:
  - The scan step and the `push: true` step **stay in the same job**. A boundary between them lets a
    job re-run push without the gate.
  - `publish` exposes the digest via `jobs.publish.outputs.digest`; `verify` has no build context.
  - `id-token: write` + `attestations: write` on `publish` only, not at workflow top level.
- [x] T3.1: Add `id: push` to the **multi-arch `push: true`** `docker/build-push-action` step in
      `release.yml` — neither build step has an `id` today. Then sign
      `steps.push.outputs.digest` (the manifest-list/index digest). **Do not** put the `id` on the
      first, local `load: true` build: it produces no registry digest, so a signature over it verifies
      against nothing a consumer can pull. Install cosign via `sigstore/cosign-installer` (pinned by
      SHA) and run `cosign sign --yes <image>@<digest>`.
- [x] T3.1b: **Scan the pushed manifest** (design D9) so the signature does not certify an `arm64`
      image nothing examined — today only the locally-built `amd64` image is scanned, and the
      multi-arch image is a second, separate build. On failure the job output MUST state the
      remediation (delete the tag / ship a patch), otherwise it is a red X people learn to ignore.
- [x] T3.2: Build-provenance attestation over the same digest (action per Q1), with
      `push-to-registry` set according to the Q2 outcome.
- [x] T3.3: SBOM attestation — feed the SBOM Trivy already generates; do not generate a second one.
- [x] T3.4: Attach the SBOM **and** a provenance file to the GitHub Release (design D3). The
      provenance asset should be the `.intoto.jsonl` form, which is also what Scorecard's
      `Signed-Releases` check reads. Mechanics: `softprops/action-gh-release` currently has **no
      `files:` input** — it must be added. Settle in T0.3 how the `.intoto.jsonl` is produced (export
      from the attestation vs a `cosign` output); do not assume a file simply appears on disk.
- [x] T3.5: **`verify` job** — `needs: publish`, runs in a clean job, and verifies from outside:
      `cosign verify` with `--certificate-identity-regexp` + `--certificate-oidc-issuer`, plus
      attestation verification for provenance and SBOM. **Fails the pipeline** on any miss. Add
      **`make verify-release`** running the identical commands against a given digest (spec 36
      same-harness) — this is also the command the consumer doc will show.
- [x] T3.6: Negative test for the verify job: point it at an unsigned digest (e.g. a `build.yml`
      short-SHA image, which by D7 is deliberately unsigned) and confirm it FAILS. A verify step that
      cannot fail proves nothing — this is the one test that keeps Phase 3 honest.
- [x] T3.7: Confirm signing covers the multi-arch manifest list (verify by tag resolves to the signed
      index digest), so we are not signing one architecture and claiming both.

---

## Phase 4 — Hygiene automation

- [x] T4.1: Convert `.trivyignore` → `.trivyignore.yaml`: the 6 existing CVEs as YAML entries with
      `id`, `statement` (reuse the existing reasons — they are good), and a mandatory `expired_at`
      90 days out. Keep the existing per-CVE justifications verbatim; they are the valuable part.
- [x] T4.2: Point all three Trivy call sites (`dep_scan`, `build.yml`, `release.yml`) at the new file.
      **Note the interface**: `aquasecurity/trivy-action` takes the input key `trivyignores:` (it
      passes `--ignorefile` internally) — the three call sites already use `trivyignores: .trivyignore`,
      so this is a value change, not a new flag. **Prove it is being read**: temporarily expire one
      entry and show the CVE re-appearing. A silently-ignored ignore file is worse than none, because
      the suppression is then invisible AND ineffective.
- [x] T4.2b: There is **no `make` target that runs Trivy locally today** (`make help` lists none), so
      the scan is CI-only — a standing spec 36 gap this spec touches. Either add `make scan` mirroring
      the CI invocation (including `trivyignores`), or record explicitly why the scan stays CI-only.
- [x] T4.3: `renovate.json` — `pinDigests: true`, managers for `pip`, `github-actions`, `docker`,
      weekly schedule, grouped PRs, `dependencyDashboard`.
- [x] T4.4: Confirm Renovate keeps the `# vX.Y.Z` comment next to each pinned SHA (otherwise the
      workflows become unreadable and someone will "fix" it by unpinning).
- [x] T4.5: One deliberate stale pin, to prove Renovate actually opens the PR. The automation is not
      done because the config exists; it is done when a PR appears.

---

## Phase 5 — Documentation

- [x] T5.1: `docs/VERIFYING-RELEASES.md` — copy-pasteable verification for a consumer. It MUST contain:
  - The **exact expected identity string**, not only a regexp.
  - An explicit warning that a loose `--certificate-identity-regexp` (e.g. `.*`) silently accepts a
    signature from **any** workflow in **any** repo. This is the most common keyless misconfiguration
    and this doc is the only defence — there is nothing server-side to catch it.
  - A **negative example** with real output: verifying with the wrong identity MUST fail.
  - The attestation verification command and where to get the SBOM.
  - Why `latest` is deliberately unsigned (D7) **and** why following `latest` after verifying a version
    once loses the guarantee: tag overwrite is precisely the threat the signature exists to detect.
  - The "why is there no public key" explanation — the workflow identity is the trust anchor — because
    that is the first question a reviewer will ask.
- [ ] T5.2: Update `docs/SECURITY.md` — add a supply-chain section pointing at T5.1 and at
      `.github/SECURITY.md`, and record the branch-protection configuration from T1.7.
- [ ] T5.3: Update `README.md` — Scorecard badge + a one-line "releases are signed; see
      VERIFYING-RELEASES.md". Do not restate the whole model there.
- [ ] T5.4: Update `AGENTS.md` with the new `make` targets (`pin-check`, `verify-release`, and `scan`
      if T4.2b adds it). **Unconditional**: this spec adds CI checks, and spec 36 requires the local
      harness to match, so the contract changed by definition.
- [ ] T5.5: `CHANGES.md` entry with the before/after Scorecard score from T1.6.

---

## Deferred (tracked, not scheduled)

| Item | Trigger to activate |
|------|---------------------|
| Hash-pinned dependencies (`--require-hashes`) | Private git dep published as a wheel to an index, OR pip gains VCS hash support, OR we start shipping a Python package (design D6) |
| Reproducible builds (bit-for-bit) | Base image pinned by digest (T2.5) lands and someone asks for byte-comparable rebuilds |
| Migrate registry to GHCR | A distribution decision, not hardening. Would give native referrers + Scorecard `Packaging` credit; costs a consumer-facing move |
| Sign `main`/`latest` builds | Only if `main` builds become a supported consumption path (design D7) |
| OpenSSF Best Practices Badge (Passing) | After Phases 1–3; at that point the gap is nearly closed and it becomes cheap communication |
| SLSA generator + `slsa-verifier` (claimed L3) | A consumer requires an explicit SLSA level; the Trivy gate must be rebuilt inside the reusable workflow FIRST (design D2) |
| gittuf | Multiple maintainers with independent key custody (until then the keys and the GitHub credential share a machine) |
