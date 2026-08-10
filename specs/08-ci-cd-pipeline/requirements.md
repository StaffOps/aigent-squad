---
spec: 08-ci-cd-pipeline
status: done-with-deferrals
completed: null
superseded_by: null
depends_on: []
deferred: ["T7 demo stage"]
---

# Feature: CI/CD Pipeline (GitHub Actions)

**Spec**: `08-ci-cd-pipeline`
**Severity**: 🔴 High (missing link between code and deploy; brings the coverage gate)
**Origin**: `../ANALYSIS.md` gitops F8 (zero CI; docs cite GitLab but the repo is on GitHub), AUDIT "no tests"
**Depends on**: `01-fix-blockers` (images need to build), `23-test-harness-docker` (reuses the harness)

The repo is on **GitHub** (`github.com:karlipegomes/AIgent-squad`) and **has no CI at all** — docs mention GitLab CI that doesn't exist. Nothing builds the images that `05-helm-chart` assumes. This spec creates the GitHub Actions pipeline, reusing the dockerized harness from `23`.

## User Stories

WHEN a push/PR arrives THEN the CI SHALL run tests with coverage via **the same harness from spec 23** and **fail below 90%**.

WHEN the CI builds images THEN SHALL be **multi-arch** (`amd64`+`arm64`/Graviton) and scanned (Trivy).

WHEN it's a push to the default branch THEN SHALL publish dev images (`<sha>`); stable release (`v<semver>`) is **manual**.

WHEN AWS credentials are needed THEN SHALL use **OIDC federation** (no long-lived keys).

## Acceptance Criteria

- [ ] `.github/workflows/` with stages: `test` → `build-dev` → (`demo` optional) → `release` (manual).
- [ ] `test` reuses the `Dockerfile.test`/command from spec 23 with `--cov-fail-under=90` (effective gate).
- [ ] Multi-arch build (`docker buildx`, amd64+arm64) per service.
- [ ] Trivy scan per image; SBOM (CycloneDX) generated.
- [ ] Tags: `<sha>` (dev, immutable), `v<semver>` (manual release); never `latest` in prod.
- [ ] AWS via OIDC (no long-lived secrets); push to ECR/Harbor.
- [ ] `mkdocs build --strict` in CI (validates the portal from spec 24).
- [ ] Lint (ruff) + the coverage gate block merge.
- [ ] README/docs point to the real pipeline (remove phantom GitLab CI reference).

## Out of scope
- ArgoCD/progressive delivery (deploy) → future / spec 05.
- cosign signing of app images (golden base is signed in the base pipeline) — evaluate later.
- Hosting the portal (GitHub Pages) — may go here or later.
