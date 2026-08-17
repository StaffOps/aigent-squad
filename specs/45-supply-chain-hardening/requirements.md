---
spec: 45-supply-chain-hardening
status: in-progress
completed: null
superseded_by: null
depends_on: ["08-ci-cd-pipeline", "36-agent-native-dev-loop"]
deferred: []
---

# Feature: Supply-Chain Hardening (verifiable releases + CI integrity)

**Spec**: `45-supply-chain-hardening`
**Severity**: 🟠 Trust boundary (what a downstream consumer can and cannot prove about our artifact)
**Origin**: evaluation of the OpenSSF project catalogue (2026-08-12) against this repo's real exposure.
The evaluation's conclusion was that the projects examined (Best Practices Badge, gittuf, Scorecard,
OSPS Baseline, RSTUF) *measure* or *document*; none of them signs the artifact or proves where it
came from. This spec does the thing they would only have reported missing.
**Depends on**: `08-ci-cd-pipeline`

---

## Problem

`aigent-squad` is a public, Apache-2.0 container image that runs **inside customer infrastructure**
with read access to observability and cloud data. A consumer who pulls
`karlipegomes/aigent-squad:v0.4.0` today cannot prove any of the following:

1. That the image was built from the commit they read on GitHub.
2. That nobody with the `DOCKERHUB_TOKEN` overwrote the tag after the build.
3. What is inside it — the CycloneDX SBOM is generated on every build and then **thrown away** as a
   CI artifact that expires; it is never attached to the Release and never attested.

There is no cosign signature, no build provenance, and no attestation of any kind.

At the same time the build pipeline itself has an unclosed path:

4. **Every third-party action is referenced by a mutable ref** — `@v4`, `@v5`, `@v6`, and
   `aquasecurity/trivy-action@master` (three call sites). A moved tag or a push to that branch
   executes attacker-chosen code inside our runner on the next run. This is the exact mechanism of
   the Codecov (2021) and `tj-actions/changed-files` (2025) compromises.
5. **`test.yml` writes a GitHub token to `~/.git-credentials`** on the runner and then runs
   third-party actions in the same job. Combined with (4) this is a live credential-exfiltration
   path. Two details make it worse than it looks, both verified 2026-08-12:
   - The token is **`DOCS_DEPLOY_TOKEN`** — a PAT whose actual purpose is pushing to
     `StaffOps/staffops.github.io`. The prize is write access to a *different* repository.
   - **It is dead code.** The only `git+https` dependency (`otel-helper @
     git+https://github.com/StaffOps/otel-libs.git@v0.2.0`) has been in a **public** repo since
     2026-07-14 (commit `d8dc822`), and the `Dockerfile` already removed its credential mount on
     2026-07-15 (BACKLOG B-28) so that a plain `docker build .` works from a fresh fork. `test.yml`
     never got the same treatment. So the remedy is **deletion**, not scoping — we are carrying an
     exfiltration path for an auth requirement that no longer exists.
6. **Trivy scans one architecture and we would sign two.** The pipeline builds `linux/amd64`
   locally, scans *that*, then **rebuilds** multi-arch and pushes. The `arm64` layers are produced by
   a second, separate build invocation and are never scanned. Signing the manifest-list digest (which
   is correct, and covers both children) would therefore attest an `arm64` image Trivy never
   examined. This gap exists today; adding signatures makes it *load-bearing*, because a signature is
   read as a stronger claim than "we scanned the amd64 sibling".
7. **`test.yml` and `docs.yml` declare no `permissions:`**, so they inherit the repository default
   instead of least privilege. `docs.yml` also pushes to `gh-pages` through a third-party action.
8. **The base image is a mutable tag** (`FROM python:3.11-alpine`, both stages), so the build inputs
   are not fixed and two builds of the same commit can differ.
9. **Vulnerability suppressions never expire.** `.trivyignore` carries 6 CVEs and its own header
   claims the format is `CVE-ID [expiry] [reason]` — but the plain text format cannot express an
   expiry, so every suppression is permanent and silently invisible.

### Threats NOT addressed (stated so the Problem section is not read as a bigger claim than it is)

A signature proves *which workflow, on which ref, built this digest*. It does not prove the code was
good. This spec does **nothing** against:

- **A compromised maintainer GitHub account.** The attacker would sign through the legitimate
  workflow, and verification would pass. This is the threat gittuf targets and the reason gittuf is
  reconsidered if the project gains multiple maintainers with independent key custody.
- **A malicious or careless commit by the maintainer.** Provenance is exactly what a malicious
  maintainer would happily provide.
- **A poisoned upstream dependency that is not yet a known CVE.** Trivy only knows the advisory
  database; a fresh malicious release passes the gate.
- **A compromise of a *pinned* action's own runtime behaviour** (a pinned commit that downloads a
  binary at run time, or calls a sub-action by mutable ref). Pinning removes the moved-tag trigger,
  not the transitive trust.

---

## What is NOT broken (corrected baseline)

An earlier draft of this analysis, written against a stale branch, claimed transitive CVEs
"accumulate silently". **That is false.** CI already runs a Trivy filesystem scan on the repo
(`dep_scan`, `scan-type: fs`, `exit-code: 1`, `CRITICAL,HIGH`) and a Trivy image scan gates both
`build.yml` and `release.yml` **before** any push. Detection exists and it blocks. What is missing is
automated **remediation** (nothing opens the bump PR) and **integrity of the inputs** (nothing pins
what is being installed). This spec must not be justified on a detection gap that does not exist.

---

## User Stories

### Verifiable artifact

WHEN a release image is pushed to Docker Hub THEN the pipeline SHALL sign it with cosign in keyless
(OIDC) mode **by digest**, never by tag — a tag is a mutable pointer and a signature over a mutable
pointer proves nothing.

WHEN a consumer runs the documented `cosign verify` command with our workflow identity and OIDC
issuer THEN verification SHALL succeed for every published version tag, with no public key
distributed by us — the trust anchor is the workflow identity, which is strictly more informative
than possession of a key.

WHEN a release is published THEN build provenance SHALL be produced by the pipeline and be
retrievable by a consumer who has **only the image reference**, without access to our CI logs.

WHEN a release is published THEN the CycloneDX SBOM SHALL be attested against the image digest AND
attached to the GitHub Release as a durable asset — a CI artifact that expires is not a deliverable.

WHEN signing, attestation, or SBOM publication silently breaks THEN CI SHALL FAIL, because a
verification step that only runs on the consumer's machine is a promise nobody checks. A post-publish
job SHALL verify the artifact it just published.

### CI integrity

WHEN any workflow references a third-party action THEN it SHALL be pinned to a full 40-character
commit SHA. `@master`, `@vN`, and any branch or tag ref are forbidden in this repo's workflows.

WHEN a workflow needs a token on the runner filesystem THEN that credential SHALL NOT be present
while any third-party action executes in the same job — either it is written after the last
third-party step, or it is injected per-command and never persisted.

WHEN any workflow runs THEN it SHALL declare an explicit top-level `permissions:` block granting the
minimum required, and elevate per-job only where a job genuinely needs write.

WHEN the container is built THEN the base image SHALL be pinned by its **manifest-list** digest (not
a per-architecture digest, which would break the multi-arch build).

### Vulnerability hygiene

WHEN a CVE is suppressed THEN the suppression SHALL carry a machine-enforced expiry date, so an
unreviewed exception re-surfaces instead of becoming permanent.

WHEN a dependency, action pin, or base-image digest goes stale THEN a bump PR SHALL be opened
automatically. Detection already exists (Trivy gates); this closes the remediation half.

### Visibility

WHEN the repository is assessed by OpenSSF Scorecard THEN the result SHALL be published and the badge
displayed — as a continuously-measured trust signal, not as a target to optimise.

WHEN someone finds a vulnerability THEN they SHALL find a disclosure path: a real `SECURITY.md` (the
existing `docs/SECURITY.md` is a threat model, not a reporting policy) plus GitHub Private
Vulnerability Reporting enabled.

---

## Acceptance Criteria

### Artifact provenance
- [ ] `cosign verify` succeeds against the published release digest using only `--certificate-identity[-regexp]` + `--certificate-oidc-issuer`. No key material distributed.
- [ ] Signature is created over `${{ steps.build.outputs.digest }}` (manifest-list digest), and a test proves signing a tag is not what happens.
- [ ] Build provenance for the release is retrievable by a consumer holding only the image reference.
- [ ] CycloneDX SBOM attested against the image digest AND present as a GitHub Release asset.
- [ ] A post-publish CI job verifies signature + provenance + SBOM attestation of the artifact just pushed, and FAILS the pipeline if any is missing or invalid.
- [ ] `docs/SECURITY.md` (or a new consumer-facing doc) carries the exact copy-pasteable verification commands, including what identity to expect.

### CI integrity
- [ ] Zero mutable action refs across all 5 workflows: `grep -rE 'uses: .*@(v[0-9]|main|master)' .github/workflows/` returns nothing. Enforced by a CI check, not by memory.
- [ ] All 5 workflows declare an explicit top-level `permissions:`.
- [ ] No GitHub token exists on the runner filesystem while a third-party action runs in the same job.
- [ ] `Dockerfile` pins both stages by manifest-list digest; the multi-arch build still produces `linux/amd64` + `linux/arm64`.

### Preserved invariant (do not regress)
- [ ] **Trivy still gates the push**: build locally → scan → push ONLY if clean, in both `build.yml` and `release.yml`. Any refactor that publishes before scanning is a regression, regardless of what it adds.
- [ ] The ordering above is proven **structurally**, not by review attention: a check asserts that in each workflow the `push: true` build step appears after the Trivy gate step, and that both live in the **same job** (a job boundary between them would let the push run on a re-run without the gate).
- [ ] The ~90% coverage gate, `specs_status`, `harness_score`, `lint`, `typecheck` and the `guard` job (main accepts PRs only from `dev`) all still run and still block.

### Same harness locally and in CI (spec 36)
- [ ] Every CI check added by this spec has a `make` target running the identical command, so a maintainer or agent can reproduce it locally: at minimum `make pin-check` and `make verify-release`. A CI-only check violates spec 36 and is not accepted.

### Consumer verification is usable and hard to get wrong
- [ ] The consumer doc states the **exact expected identity string**, not only a regexp.
- [ ] It warns that a loose `--certificate-identity-regexp` (e.g. `.*`) silently accepts a signature from **any** workflow in **any** repo — the single most common keyless misconfiguration, and the only defence is this doc.
- [ ] It includes a **negative example**: verifying with the wrong identity MUST fail, shown as output.
- [ ] It states why following `latest` after verifying a version once loses the guarantee (tag overwrite is precisely the threat the signature exists to detect).

### Hygiene
- [ ] Suppressions carry an enforced expiry; an expired entry causes the scan to report the CVE again.
- [ ] Automated bump PRs exist for: Python deps, GitHub Actions pins, Docker base image digest.
- [ ] `SECURITY.md` in a location the tooling actually reads, plus Private Vulnerability Reporting enabled.
- [ ] `CODEOWNERS` present.
- [ ] Scorecard result published and badge in `README.md`.

---

## Out of scope — and why

| Excluded | Why |
|----------|-----|
| **gittuf** | Its threat model is a compromised forge admin tampering with git refs. With one maintainer, the gittuf keys live on the same machine as the GitHub credential, so the protection is largely circular; and it does nothing for the container artifact, which is our actual exposure. Reconsider if the project gains multiple maintainers with independent key custody. |
| **RSTUF** | Built for the **operator** of a package index. We are a tenant on Docker Hub. It would mean running an API + worker + broker + offline root-key ceremony to protect nothing we own. |
| **OpenSSF Best Practices Badge** | Self-certification questionnaire; verifies nothing automatically. After this spec ships, the Passing level is nearly free — do it then, as communication, not as security work. |
| **OSPS Baseline badge** | Used here as a **checklist** (it correctly flagged OSPS-AC-03/04, BR-06/07, VM-01/02/03 as real gaps, all covered above). Pursuing the badge adds nothing while assessment tooling is still self-attestation. |
| **Hash-pinned dependencies** (`--require-hashes`) | Hard-blocked: `--require-hashes` is all-or-nothing and pip cannot hash a VCS URL (pypa/pip#6469), while this repo installs a private **git** dependency. See promotion trigger in `design.md`. |
| **Reproducible builds** | Needs `SOURCE_DATE_EPOCH` discipline and a byte-comparison harness. Pinning the base image digest is the prerequisite and is in scope; bit-for-bit reproducibility is not. |
| **Migrating off Docker Hub to GHCR** | Would make several things easier (native referrers, Scorecard's `Packaging` check). It is a distribution decision with consumer impact, not a hardening task. Explicitly deferred, not dismissed. |
| **Signing every intermediate/dev image** | Only versioned releases carry a trust promise. Signing `latest` and every short-SHA build multiplies Rekor entries and teaches consumers to trust mutable tags. |

---

## Open questions — MUST be settled in T0.3 before writing YAML

These come from two independent research passes that **disagreed**. Recording the disagreement
instead of silently picking a side.

| # | Question | Why it matters | How to settle |
|---|----------|----------------|---------------|
| **Q1** | Exact action name and current major version for GitHub-native attestation. One pass reported `actions/attest-build-provenance`; the other that `actions/attest@v4` supersedes it. | The YAML cannot be written without it, and a wrong guess silently produces no attestation. | Read the current GitHub docs + the action's own repo at implementation time. |
| **Q2** | Does Docker Hub serve the **native OCI Referrers API**, or does tooling fall back to the tag-based (`sha256-<digest>.sig`) scheme? Both passes concluded "it works", by **different mechanisms**; one explicitly marked the referrers claim UNVERIFIED. | Determines whether `push-to-registry: true` yields something a consumer can discover from the image alone, and whether we need cosign's `--new-bundle-format=false` the way the corporate Harbor pipeline did. | Smoke test on a throwaway tag: sign, then `cosign verify` and `cosign triangulate` from a clean environment before committing the real workflow. |
| **Q3** | Does Scorecard currently have an **SBOM** check at all? One pass listed it as a Medium-risk check added in 2024; another could not find it in current `checks.md`. | Decides whether attaching the SBOM to the Release earns anything measurable — and if not, we do it for the consumer-value reason only, which is fine but must be stated honestly. | Read `docs/checks/internal/checks.yaml` in the Scorecard repo at implementation time. |
| **Q4** | The exact CycloneDX predicate-type URI Trivy emits (varies with `specVersion`). | A wrong `--predicate-type` makes verification fail against a valid attestation — the worst failure mode, because it looks like tampering. | Inspect `specVersion` in our own generated SBOM. |

**Verified and NOT open**: Scorecard's `Signed-Releases` check reads **GitHub Release assets**
(`*.sig`, `*.asc`, `*.intoto.jsonl`, …) — it does not query the container registry or the GitHub
attestations API. So a registry-only attestation, however real, scores zero there. That asymmetry is
addressed as a deliberate decision in `design.md` (D3), not by pretending the check does more than
it does.
