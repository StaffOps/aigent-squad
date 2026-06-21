# CI/CD — branch strategy, pipeline & versioning (Model A)

How code and docs move from a feature branch to production, what runs at each
step, and how versions are assigned. The guiding rule: **a PR validates, a merge
publishes — and nothing reaches a registry without passing Trivy first.**

---

## Branching

| Branch | Role | Protection |
|--------|------|------------|
| `main` | Production source of truth | Protected. PRs **only from `dev`**. No direct push. |
| `dev` | Integration | Protected. PRs from `feature/*`. No direct push. |
| `feature/*`, `fix/*` | Day-to-day work | — |

Flow: `feature/* ──PR──▶ dev ──PR──▶ main`

`main` accepting PRs only from `dev` is enforced by a guard job (below) — GitHub
branch protection alone cannot restrict the source branch.

---

## What runs, and WHEN

Two lanes selected by what the PR touches (path filters). Jobs always report a
status so required checks never hang on a docs-only change.

| Event | Code lane | Docs lane |
|-------|-----------|-----------|
| **PR → dev** | `lint` · `test` (cov ≥90%) · SAST (CodeQL) · Trivy **fs** (deps) | `mkdocs build` |
| **push: dev** (post-merge) | nothing required (optional re-`test`) | nothing (prod docs only from `main`) |
| **PR → main** (only from `dev`) | `guard` (head==dev) · `lint` · `test` · Trivy **fs** (deps) | `mkdocs build` |
| **push: main** (post-merge) | `build.yml`: build → scan → push `latest`+`sha` | `docs.yml`: deploy → `staffops.github.io/aigent-squad/` |
| **tag `v*`** (release) | `release.yml`: build local → **Trivy gate** → push **`X.Y.Z`**+`latest` · SBOM · GitHub Release | — |

### Why scan happens at two scopes (not twice)

- **PR → Trivy `fs`**: scans `requirements.txt` for vulnerable **dependencies**.
  Fast, no image build. Early feedback (most CVEs here are Python deps).
- **publish → Trivy `image`**: scans the **actual image** (OS + deps) on the exact
  artifact, with a fresh CVE DB, as a **gate before push**. This is the binding
  check — it also catches CVEs disclosed *after* the PR was scanned.

The image gate runs **once**, at publish, on what is actually shipped. The PR
scan is a different scope (source deps), not a duplicate.

### Scan-before-publish (supply chain)

Publishing never precedes scanning. The release builds a single-arch image into
the local daemon (`load`, no push), Trivy scans it (`exit-code 1` on
HIGH/CRITICAL, exceptions in `.trivyignore`), and only if it passes does the
multi-arch build + push run. A vulnerable image never reaches the registry.

> Known gap: `build.yml` (the per-merge `latest`/`sha` path) currently scans
> *after* push. Follow-up: align it to the same build-local → gate → push order
> used by `release.yml`.

---

## Required status checks (branch protection)

- **`dev`**: Require PR · Require status checks → `lint`, `test` · Require up to date
- **`main`**: Require PR · Require status checks → `guard`, `lint`, `test`, `build` · Require up to date

A check must have run once before it can be marked required. The coverage gate is
`pytest --cov --cov-fail-under=90`: exit code 1 → red `test` check → merge blocked.

### "Only from `dev`" guard

A required job on PRs targeting `main` that fails when the head branch is not
`dev`:

```yaml
guard:
  if: github.base_ref == 'main'
  runs-on: ubuntu-latest
  steps:
    - run: |
        test "${{ github.head_ref }}" = "dev" \
          || { echo "main only accepts PRs from dev"; exit 1; }
```

---

## Versioning

Three SemVer numbers, **linked** so a deployment is reproducible.

| Number | Where | Bumps when |
|--------|-------|-----------|
| **App version** `vX.Y.Z` | git tag on this repo + image tag | a validated milestone ships |
| **`appVersion`** | `helm-charts` `Chart.yaml` | = the app version the chart deploys |
| **Chart `version`** `A.B.C` | `helm-charts` `Chart.yaml` | the **chart** changes (templates/values) |

```
App  vX.Y.Z ─┬─ git tag (this repo) ──▶ release.yml ──▶ image :X.Y.Z
             └─ Chart.appVersion: "X.Y.Z" ──▶ image.tag (values) = appVersion
Chart A.B.C ── chart-releaser ──▶ Helm repo (staffops.github.io/helm-charts)
```

**Rules (the minimum):**
- App tag is SemVer: `MAJOR`=breaking (API/config), `MINOR`=compatible feature,
  `PATCH`=fix/security.
- Pre-1.0 (`0.x`): anything may change. Do **not** cut `1.0.0` without a real
  deploy + tests (steering `version-management`).
- The chart's `image.tag` resolves to `Chart.AppVersion` — never `latest` in prod.
- Bump chart `version` on any chart change; bump `appVersion` when it targets a
  new app release.
- `CHANGES.md` follows Keep-a-Changelog: `[Unreleased]` becomes `[X.Y.Z] - date`
  when a tag is cut.

### Cutting a release

1. Land all changes on `main` (via `dev`), CI green.
2. Set `CHANGES.md` heading `[Unreleased]` → `[X.Y.Z] - <date>`.
3. In `helm-charts`: `appVersion: "X.Y.Z"`, `image.tag` → `X.Y.Z` (or empty to
   inherit `appVersion`), bump chart `version`.
4. `git tag vX.Y.Z && git push origin vX.Y.Z` → `release.yml` builds, scans
   (gate), pushes `:X.Y.Z`, generates SBOM + GitHub Release.

---

## Secrets

| Secret | Used by | Purpose |
|--------|---------|---------|
| `DOCS_DEPLOY_TOKEN` | test, build, docs, release | clone private dep (HTTPS) + deploy portal |
| `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` | build, release | Docker Hub login |

---

## Workflows

| File | Trigger | Purpose |
|------|---------|---------|
| `test.yml` | push/PR (main, dev) | lint + test (coverage gate) |
| `build.yml` | push: main | build + push `latest`/`sha` to Docker Hub + Trivy |
| `docs.yml` | push: main (docs/**) | deploy MkDocs to the portal |
| `release.yml` | tag `v*` / manual | versioned, scan-gated image `:X.Y.Z` + SBOM + Release |
