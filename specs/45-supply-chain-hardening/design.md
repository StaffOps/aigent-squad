# Design: Supply-Chain Hardening

**Spec**: `45-supply-chain-hardening`

---

## Architecture

The change is entirely in the delivery path. No application source is touched.

```
                        ┌─────────────────────────── today ────────────────────────────┐
  push/tag ──▶ build local (amd64) ──▶ Trivy gate ──▶ build+push multi-arch ──▶ SBOM ──▶ (artifact expires)
                                          │                                             ✗ nothing signed
                                          └── GATE: nothing reaches the registry if dirty
                                              ↑ THIS ORDERING IS AN INVARIANT

                        ┌─────────────────────────── target ───────────────────────────┐
  push/tag ──▶ build local (amd64) ──▶ Trivy gate ──▶ build+push multi-arch
                                                            │
                                                            ├──▶ digest (manifest list)
                                                            │      │
                                                            │      ├──▶ cosign sign (keyless, OIDC)
                                                            │      ├──▶ build provenance attestation
                                                            │      └──▶ SBOM attestation (CycloneDX)
                                                            │
                                                            ├──▶ GitHub Release assets: SBOM + provenance file
                                                            │
                                                            └──▶ VERIFY job (fails the pipeline if any of
                                                                 the above cannot be verified from outside)
```

Everything added hangs off the digest emitted by the existing build step. The Trivy-gate-before-push
ordering is preserved, and the verify job is what stops the new guarantees from rotting silently.

### The refactor this implies (not optional)

`release.yml` is **one job** today. A `verify` job that `needs:` the publish job requires splitting it,
which has two hard constraints:

1. **The scan step and the push step MUST stay in the same job.** A job boundary between them means a
   re-run of the publish job could push without the gate. This is the invariant, not a preference.
2. **The digest must cross the job boundary** via `jobs.<publish>.outputs.digest`, because the second
   job has no build context or cache.

`id-token: write` and `attestations: write` are granted **per job**, on the publishing job only, never
at workflow top level.

Neither `docker/build-push-action` step in `release.yml` currently has an `id:` — one must be added, to
the **multi-arch `push: true`** step. The local `load: true` build produces no registry digest, so
signing its output would produce a signature over something no consumer can pull.

## Components

| Component | Change | Responsibility |
|-----------|--------|----------------|
| `.github/workflows/release.yml` | modified | sign + attest + attach assets + verify, after the existing gate |
| `.github/workflows/build.yml` | modified | pins + `permissions:`; signs `main` builds only if D7 is accepted |
| `.github/workflows/test.yml` | modified | explicit `permissions:`; credential moved out of the third-party blast radius; pin check |
| `.github/workflows/docs.yml` | modified | explicit `permissions:`; pins |
| `.github/workflows/sast.yml` | modified | pins |
| `.github/workflows/pin-check.yml` (or a job) | **new** | fails if any `uses:` is not a 40-char SHA — the rule must outlive the memory of it |
| `.github/workflows/scorecard.yml` | **new** | scheduled + on-push Scorecard run, publishes results |
| `Dockerfile` | modified | base image by manifest-list digest, both stages |
| `.trivyignore` → `.trivyignore.yaml` | **replaced** | suppressions with enforced `expired_at` |
| `renovate.json` | **new** | `pinDigests` + weekly grouped bumps for pip / actions / docker |
| `.github/SECURITY.md` | **new** | vulnerability disclosure policy (distinct from `docs/SECURITY.md`, the threat model) |
| `.github/CODEOWNERS` | **new** | review ownership |
| `docs/VERIFYING-RELEASES.md` | **new** | consumer-facing verification commands |

---

## Rationale (decisions and trade-offs)

### Decision 1: cosign in **keyless** (OIDC) mode, diverging from the corporate key-based pipeline

**Choice**: sign with `cosign sign` using the GitHub Actions OIDC identity (Fulcio + Rekor). No key
pair, no `COSIGN_PASSWORD`, no secret in the repo.

**Justification, in order of strength**:
1. **It removes key custody entirely.** For a single maintainer, the private key IS the risk: it has
   to be generated, stored, rotated, and kept out of git, and every one of those steps is a place to
   fail quietly. A guarantee that depends on a ritual nobody audits is weaker than it looks.
2. **GitHub OIDC is natively available here.** The corporate pipelines run in GitLab against Harbor,
   where key-based signing with `--new-bundle-format=false` was the correct answer. Different
   substrate, different answer. The divergence is deliberate, not drift.
3. **The trust anchor becomes the workflow identity** — `.../release.yml@refs/tags/vX`. That says
   *which code, on which ref, in which repo* produced the artifact. A public key only says *someone
   held the key*.
4. **Transparency suits a public project.** Every signature lands in a public append-only log, so a
   consumer can audit our signing history without asking us.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Signing needs Fulcio/Rekor reachable | An outage means "retry the release in 5 minutes". Not a production-traffic dependency. |
| Verification wants Rekor (or a saved bundle) | Fine for the normal consumer; an air-gapped consumer needs the bundle shipped alongside. Documented, not hidden. |
| Certificate is short-lived (~10 min) | The durable proof is the log entry, not the certificate. Counter-intuitive, so it goes in the consumer doc. |
| Two signing models across the org | Justified above; the risk is cargo-culting one into the other, which is why both this file and the corporate steering state the reason. |

**When this decision would be wrong**:
- A consumer must verify in an air-gapped environment and cannot accept bundle distribution.
- An admission controller in the consumer's path can only be configured with a static public key.
- Sigstore's public-good instance stops being credible for release-blocking use.

**Alternatives discarded**: key-based cosign (recreates the custody problem we are trying to avoid);
Docker Content Trust / Notary v1 (retired by Docker); signing nothing and relying on registry
access control (that is the status quo, and it proves nothing to a consumer).

---

### Decision 2: GitHub-native build attestation, **not** the SLSA reusable-workflow generator

**Choice**: produce provenance with GitHub's own attestation action (exact name/version → **Q1**),
added *after* the existing build step. Do not adopt `slsa-framework/slsa-github-generator`'s
container workflow.

**Justification, in order of strength**:
1. **It is additive.** ~10 lines fed by `steps.build.outputs.digest`. The generator is a *reusable
   workflow that must BE the build* — adopting it means handing our build to it.
2. **It would put our safety invariant at risk.** Today the pipeline builds locally, scans, and
   pushes only if clean. Delegating the build to an external reusable workflow means re-implementing
   that gate inside someone else's flow, and the failure mode is publishing before scanning. We are
   not trading a real gate for a provenance badge.
3. **Zero infra, keyless by construction**, same as D1 — one trust model to explain, not two.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| GitHub does not advertise an explicit SLSA level for this path | Nobody is asking us for a level today. Provenance a consumer can verify beats an unverified number. |
| Attestation lives in GitHub's attestation API (plus the registry when supported) | Acceptable while GitHub is already the source of truth for the code. Registry copy is the goal — see Q2. |
| `gh attestation verify` is the natural verifier | It is one more tool in the consumer's path than `cosign`. Both commands go in the consumer doc. |

**When this decision would be wrong**: a consumer requires `slsa-verifier` against a claimed SLSA
L3. Then the generator becomes worth the refactor — and the Trivy gate must be rebuilt inside it
first, not after.

**Alternatives discarded**: `slsa-github-generator` (above); `cosign attest` alone — kept as the
**fallback** if Q2 shows the native path cannot deliver something discoverable from Docker Hub.

---

### Decision 3: also attach signature/provenance/SBOM as **GitHub Release assets**

**Choice**: publish the SBOM and a provenance file as Release assets in addition to the registry
attestations.

**Justification, in order of strength**:
1. **A CI artifact is not a deliverable.** Today's SBOM expires. A consumer doing due diligence six
   months from now finds nothing.
2. **Not every consumer speaks OCI.** Someone assessing the project from the GitHub page should be
   able to download the SBOM without a registry client or `gh`.
3. **It is the only thing Scorecard's `Signed-Releases` check actually reads** — verified: it looks
   at Release assets, not at the registry or the attestations API.

**Honesty about reason 3**: on its own that would be score-gaming. It is listed third deliberately —
reasons 1 and 2 are why we do it; the Scorecard credit is a side effect. If reasons 1 and 2 did not
hold, we would not add the step. The related trap is Q3: if Scorecard has no SBOM check at all, then
attaching the SBOM has **only** consumer value, and that is still sufficient.

**When this decision would be wrong**: if maintaining two publication paths ever causes them to
disagree (the asset says one thing, the registry another). Mitigation: both are produced from the
same digest in the same job, and the verify job checks the registry copy.

---

### Decision 4: **Renovate with `pinDigests`**, not Dependabot

**Choice**: `renovate.json` with `pinDigests: true` for the `github-actions` and `docker` managers,
plus pip, on a weekly grouped schedule.

**Justification, in order of strength**:
1. **The repo has zero pins today.** Renovate can *introduce* digest pins and then maintain them;
   Dependabot only bumps pins that already exist. Choosing Dependabot means a manual pin sweep first
   and no protection against a new unpinned `uses:` sneaking in later.
2. **Grouping and scheduling matter with one maintainer.** Ungrouped bump PRs on ~20 deps plus ~10
   actions is exactly the noise that trains someone to merge without reading.

**What this does NOT buy** — stated explicitly so the spec cannot be justified on a false premise:
this adds **automated remediation, not detection**. Vulnerable dependencies are already caught and
already block: `dep_scan` runs Trivy `fs` with `exit-code: 1` on `CRITICAL,HIGH`, and both push
paths are gated by an image scan.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| A third-party GitHub App in the repo | The alternative is a manual pin sweep plus permanent vigilance. The App's blast radius is bounded by its own permissions. |
| `renovate.json` is a larger config surface than `dependabot.yml` | One file, written once. |
| Digest pins make diffs unreadable to humans | Renovate keeps the `# v4.2.1` comment beside the SHA. |

**When this decision would be wrong**: if the maintainer decides no third-party App may hold write
access to the repo. Then: Dependabot + a one-time pin sweep + the `pin-check` job as the guard.

---

### Decision 5: `.trivyignore` → `.trivyignore.yaml` with mandatory `expired_at`

**Choice**: convert the six suppressions to YAML, each with an `expired_at` (90 days), and pass
`--ignorefile .trivyignore.yaml` explicitly.

**Justification**: the current file's header documents a `[expiry]` field that **the plain text
format cannot express** — so it is decoration, and all six suppressions are permanent. A permanent
suppression is indistinguishable from having no scanner for that CVE. The YAML format supports
`expired_at`, which turns each exception into a decision with a review date.

**Trade-offs accepted**: the YAML ignore file is flagged experimental upstream and requires the
explicit `--ignorefile` flag (a silent no-op if we forget it, so the conversion PR must show the CVE
re-appearing when an entry is expired). Expiring suppressions will occasionally turn CI red on a day
nobody planned for — that is the intended behaviour, not a side effect.

**When this decision would be wrong**: if the upstream schema changes or the feature is withdrawn.

---

### Decision 6: **no** hash-pinned dependencies now — with an explicit promotion trigger

**Choice**: do not adopt `pip-compile --generate-hashes` + `--require-hashes` in this spec.

**Justification**: `--require-hashes` is all-or-nothing, and pip cannot hash a VCS URL
(pypa/pip#6469). This repo installs a **private git dependency**, so the mode cannot be enabled at
all — not "is expensive", *cannot*. Adding a ~200-line lock file for one maintainer while the mode
stays off would be pure cost.

**Promotion trigger (revisit when ANY of these becomes true)**:
- The private git dependency is published as a wheel to an index (then hash mode becomes possible).
- pip gains VCS hash support.
- The project starts distributing a Python package (not just a container), which raises install-time
  tamper-proofing from nice-to-have to expected.

---

### Decision 7: sign **releases only**, not every `main` build

**Choice**: `release.yml` (version tags) signs and attests. `build.yml` (`latest` + short-SHA on
`main`) does not.

**Justification**: only a versioned release carries a trust promise. Signing `latest` teaches
consumers to verify a mutable tag, which is the habit we are trying to break, and every non-release
build adds a permanent public log entry for an artifact nobody should depend on.

**Trade-off accepted**: a consumer running `latest` gets no signature. That is the correct incentive
— the consumer doc will say to pin a version.

**When this decision would be wrong**: if `main` builds ever become the supported consumption path.
Then `build.yml` gets the same treatment and `latest` stops being published.

---

### Decision 8: **delete** the runner credential rather than scope it

**Choice**: remove the `~/.git-credentials` write from `test.yml` entirely. Do not "move it later in
the job", do not replace it with `GIT_ASKPASS`.

**Justification, in order of strength**:
1. **The auth requirement no longer exists.** The only `git+https` dependency, `otel-helper`, has been
   in a **public** repo since 2026-07-14 (`d8dc822`), and the `Dockerfile` already dropped its
   credential mount on 2026-07-15 (BACKLOG B-28) precisely so a fresh fork could build with a plain
   `docker build .`. `test.yml` was never updated. We are carrying an exfiltration path for a
   credential nothing needs.
2. **The token is over-privileged for the job it is in.** It is `DOCS_DEPLOY_TOKEN` — a PAT that can
   push to `StaffOps/staffops.github.io`. The prize for compromising a third-party action in
   `test.yml` is write access to a *different* repository, which is worse than it first reads.
3. **Deletion is the only fix with no residual.** Scoping narrows a window; deletion closes it.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| If a private git dependency returns, the pattern has to be rebuilt properly | Then it should be a separate job with no third-party actions, or the dep published to an index — not a token on the shared runner filesystem. Recorded here so the next person does not re-add the old pattern. |
| One more thing to verify before deleting | T2.4 verifies `pip install` still resolves `otel-helper` with no auth before the write is removed. |

**Residual risk that pinning does NOT remove** (this was overstated in the first draft, which implied
"pin + scope closes the path"):
- **SHA pinning does not protect against the pinned commit's own runtime behaviour.** An action pinned
  at a SHA can still `curl | sh` at run time or call a sub-action by mutable ref. The trust boundary
  is the transitive runtime behaviour of that commit, not its source tree.
- **Any env-var-based credential is readable by later steps in the same job** (environment
  inheritance / `/proc/self/environ`). That is why `GIT_ASKPASS` was rejected as the fix here — it
  would have moved the secret from the filesystem into the environment and been described as "never
  persisted", which is false for the lifetime of the job.
- Full closure for a future private dependency = a dedicated job containing no third-party actions.

**When this decision would be wrong**: if `otel-helper` (or any dependency) goes private again. Then
the requirement returns and the dedicated-job option must be costed rather than reaching for the
convenient `printf` into a credentials file.

---

### Decision 9: sign the manifest list, and close the scan/sign asymmetry rather than inherit it

**Choice**: keep signing the manifest-list digest (it is the correct subject), and add a Trivy scan of
the **pushed manifest** so the signature does not certify an architecture nothing examined.

**Justification, in order of strength**:
1. **A signature is read as a stronger claim than it is.** Today the pipeline scans a locally-built
   `amd64` image, then *rebuilds* multi-arch and pushes. The `arm64` layers come from a second build
   invocation and are never scanned. Unsigned, that is a known rough edge. Signed, it becomes an
   assertion that both children passed a gate one of them never entered.
2. **The gap is inherited, not introduced — which is exactly why the spec must name it.** A hardening
   spec that silently steps over a hole in the thing it is hardening is worse than one that records
   it.
3. Platform-specific dependency resolution is a real divergence path: the two builds can legitimately
   resolve different wheels for different architectures.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Scanning after push means a bad `arm64` image is briefly public before we know | Mitigated by the pre-push `amd64` gate catching everything architecture-independent (the large majority: base OS and Python deps). The post-push scan closes the architecture-specific remainder. |
| Extra scan time per release | ~1 minute, off the critical path of the build itself. |
| Failing after publication needs a response, not just a red X | The verify job must state the remediation in its failure output: delete the tag / publish a fixed patch. A red build with no instruction is a red build people learn to ignore. |

**Alternative considered and discarded**: scan the arm64 image locally before push via QEMU emulation
— discarded because emulated builds are slow enough to change release ergonomics, and the pre-push
gate would then depend on QEMU correctness.

**When this decision would be wrong**: if Trivy gains a single-invocation multi-platform scan that
runs pre-push, this becomes strictly better done before publication. Promotion trigger: watch for
multi-platform support in `trivy image`.

---

## Invariants

- **Nothing reaches the registry unscanned.** Build local → Trivy gate → push, **within one job**. Any
  change that publishes before scanning, or that puts a job boundary between the gate and the push, is
  a regression even if it adds signatures. Enforced structurally, not by review attention.
- **What we sign, we scanned.** Signing the manifest list means both architectures are covered by the
  claim, so both must be scanned (D9).
- **Signatures are over digests.** Never a tag. Never the local `load: true` build's output.
- **No mutable action ref may exist in `.github/workflows/`**, enforced by CI.
- **No credential on the runner filesystem or in the job environment while third-party code runs.**
- **A guarantee that is not verified from outside does not exist.** Every added guarantee has a verify
  step that fails the pipeline, and that verify step has a negative test proving it *can* fail.
- **Every CI check has a `make` target** running the identical command (spec 36 same-harness). A
  CI-only check is not accepted.
- **Existing gates keep gating**: ~90% coverage, `specs_status`, `harness_score`, `lint`,
  `typecheck`, and `guard` (main accepts PRs only from `dev`).

---

## External dependencies

| Service | Purpose | Failure mode |
|---------|---------|--------------|
| Sigstore Fulcio | issues the short-lived signing certificate from the OIDC token | release blocked; retry |
| Sigstore Rekor | transparency log; inclusion proof | signing blocked; verification degrades to bundle-only |
| GitHub OIDC (`token.actions.githubusercontent.com`) | identity for keyless signing | signing blocked |
| GitHub Attestations API | stores provenance/SBOM attestations | `gh attestation verify` unavailable; registry copy is the mitigation (Q2) |
| Docker Hub | image + (pending Q2) attestation storage | push blocked; nothing published |
| Renovate App | automated bumps | bumps stop; Trivy detection unaffected |
| OpenSSF Scorecard API | published score + badge | badge stale; no functional impact |
