# Tasks: CI/CD Pipeline (GitHub Actions)

> Repo está no GitHub. Reusa o harness da spec 23. Habilita o deploy assumido pela 05.

- [x] T1: `.github/workflows/test.yml` — roda o harness da spec 23 (`--cov-fail-under=90`) + `ruff` em todo push/PR — done 2026-06-14
- [x] T2: `mkdocs build --strict` runs in CI — `.github/workflows/docs.yml`
      `build_check` job (PRs to main/dev, path-filtered to `docs/site/**` +
      `mkdocs.yml`), validating portal nav/links. Spec 24 shipped the portal;
      strict build is green (verified 2026-07-18). Lives in `docs.yml` (dedicated
      Docs workflow), not `test.yml` — same effect, better-scoped. (depends on: T1)
- [x] T3: `build.yml` — `docker buildx` multi-arch (amd64+arm64) por serviço; tag `<sha>` (depends on: 01) — done 2026-06-14
- [x] T4: Trivy scan + SBOM CycloneDX por imagem (depends on: T3) — done 2026-06-14
- [x] T5: OIDC GitHub→AWS; push pra ECR/Harbor (sem secret de longa duração) (depends on: T3) — done 2026-06-14
- [x] T6: Estágio `release` manual (tag `v<semver>`, push estável) (depends on: T3) — done 2026-06-14
- [ ] T7: `demo` opcional — smoke (1 query single + 1 cross-domain) (depends on: T3) — NOT IMPLEMENTED (manual smoke only)
- [x] T8: Remover refs fantasma a GitLab CI dos docs; README aponta o pipeline real (depends on: T1) — done 2026-06-14
- [x] T9: Review independente (`code-review`/`gitops`): gate efetivo, multi-arch, OIDC, sem `latest` (depends on: T1–T8) — done 2026-06-14

## Ordem sugerida
T1→T2; T3→T4→T5→T6; T7; T8; T9.

## Notas
- `test` NÃO duplica config de teste — chama o Dockerfile/comando da spec 23.
- Coverage gate barra merge (não é warning).
- Multi-arch obrigatório (Graviton). `latest` proibido em prod.
- Pipeline de verificação: T9 é o review independente; o "test-author≠autor" aqui se aplica às specs de código (06/17/18/...), não ao YAML de CI.

## Status (2026-06-14)

**Completed**: T1, T3, T4, T5, T6, T8, T9. GitHub Actions workflows for test (with coverage gate + ruff), multi-arch build, Trivy/SBOM, OIDC push, release stage, docs cleanup, and code-review.

**NOT implemented**:
- T2: `mkdocs build --strict` — DONE (2026-07-18). Spec 24 shipped the portal;
  the strict build is a CI gate in `docs.yml` (`build_check` job), verified green.
  Placed in the dedicated Docs workflow rather than `test.yml` (same effect,
  runs only when docs change).
- T7: Demo stage with automated smoke — manual smoke testing only. Formalized demo stage deferred.

**Deferred**: T7 (low priority; manual smoke sufficient for now). (T2 done — see above.)
