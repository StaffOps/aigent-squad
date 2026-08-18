# Tasks: CI/CD Pipeline (GitHub Actions)

> Repo is on GitHub. Reuses the harness from spec 23. Enables the deploy assumed by 05.

- [x] T1: `.github/workflows/test.yml` — runs the harness from spec 23 (`--cov-fail-under=90`) + `ruff` on every push/PR — done 2026-06-14
- [x] T2: `mkdocs build --strict` runs in CI — `.github/workflows/docs.yml`
      `build_check` job (PRs to main/dev, path-filtered to `docs/site/**` +
      `mkdocs.yml`), validating portal nav/links. Spec 24 shipped the portal;
      strict build is green (verified 2026-07-18). Lives in `docs.yml` (dedicated
      Docs workflow), not `test.yml` — same effect, better-scoped. (depends on: T1)
- [x] T3: `build.yml` — `docker buildx` multi-arch (amd64+arm64) per service; tag `<sha>` (depends on: 01) — done 2026-06-14
- [x] T4: Trivy scan + SBOM CycloneDX per image (depends on: T3) — done 2026-06-14
- [x] T5: OIDC GitHub→AWS; push to ECR/Harbor (no long-lived secret) (depends on: T3) — done 2026-06-14
- [x] T6: `release` stage manual (tag `v<semver>`, push stable) (depends on: T3) — done 2026-06-14
- [ ] T7: optional `demo` — smoke (1 single query + 1 cross-domain) (depends on: T3) — NOT IMPLEMENTED (manual smoke only)
- [x] T8: Remove phantom GitLab CI refs from docs; README points to the real pipeline (depends on: T1) — done 2026-06-14
- [x] T9: Independent review (`code-review`/`gitops`): effective gate, multi-arch, OIDC, no `latest` (depends on: T1–T8) — done 2026-06-14

## Suggested order
T1→T2; T3→T4→T5→T6; T7; T8; T9.

## Notes
- `test` does NOT duplicate test config — it calls the Dockerfile/command from spec 23.
- Coverage gate blocks merge (not a warning).
- Multi-arch mandatory (Graviton). `latest` forbidden in prod.
- Verification pipeline: T9 is the independent review; "test-author≠author" here applies to code specs (06/17/18/...), not to CI YAML.

## Status (2026-06-14)

**Completed**: T1, T3, T4, T5, T6, T8, T9. GitHub Actions workflows for test (with coverage gate + ruff), multi-arch build, Trivy/SBOM, OIDC push, release stage, docs cleanup, and code-review.

**NOT implemented**:
- T2: `mkdocs build --strict` — DONE (2026-07-18). Spec 24 shipped the portal;
  the strict build is a CI gate in `docs.yml` (`build_check` job), verified green.
  Placed in the dedicated Docs workflow rather than `test.yml` (same effect,
  runs only when docs change).
- T7: Demo stage with automated smoke — manual smoke testing only. Formalized demo stage deferred.

**Deferred**: T7 (low priority; manual smoke sufficient for now). (T2 done — see above.)
