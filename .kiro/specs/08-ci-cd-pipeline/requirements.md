# Feature: CI/CD Pipeline (GitHub Actions)

**Spec**: `08-ci-cd-pipeline`
**Severidade**: 🔴 High (elo perdido entre código e deploy; traz o gate de cobertura)
**Origem**: `../ANALYSIS.md` gitops F8 (zero CI; docs citam GitLab mas o repo é GitHub), AUDIT "sem testes"
**Depende de**: `01-fix-blockers` (imagens precisam buildar), `23-test-harness-docker` (reusa o harness)

O repo está no **GitHub** (`github.com:karlipegomes/AIgent-squad`) e **não tem CI nenhum** — docs falam de GitLab CI que não existe. Nada builda as imagens que a `05-helm-chart` assume. Esta spec cria o pipeline GitHub Actions, reusando o harness dockerizado da `23`.

## User Stories

WHEN um push/PR chega THEN o CI SHALL rodar testes com cobertura via **o mesmo harness da spec 23** e **falhar abaixo de 90%**.

WHEN o CI builda imagens THEN SHALL ser **multi-arch** (`amd64`+`arm64`/Graviton) e escaneadas (Trivy).

WHEN é push na branch default THEN SHALL publicar imagens dev (`<sha>`); release estável (`v<semver>`) é **manual**.

WHEN credenciais AWS são necessárias THEN SHALL usar **OIDC federation** (sem chave de longa duração).

## Acceptance Criteria

- [ ] `.github/workflows/` com estágios: `test` → `build-dev` → (`demo` opcional) → `release` (manual).
- [ ] `test` reusa o `Dockerfile.test`/comando da spec 23 com `--cov-fail-under=90` (gate efetivo).
- [ ] Build multi-arch (`docker buildx`, amd64+arm64) por serviço.
- [ ] Trivy scan por imagem; SBOM (CycloneDX) gerado.
- [ ] Tags: `<sha>` (dev, imutável), `v<semver>` (release manual); nunca `latest` em prod.
- [ ] AWS via OIDC (sem secrets de longa duração); push pra ECR/Harbor.
- [ ] `mkdocs build --strict` no CI (valida o portal da spec 24).
- [ ] Lint (ruff) + o gate de cobertura barram o merge.
- [ ] README/docs apontam o pipeline real (remove referência fantasma a GitLab CI).

## Fora de escopo
- ArgoCD/progressive delivery (deploy) → futuro / spec 05.
- Assinatura cosign de imagem de app (golden base é assinada no pipeline de base) — avaliar depois.
- Hosting do portal (GitHub Pages) — pode entrar aqui ou depois.
