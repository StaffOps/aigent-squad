# Design: CI/CD Pipeline (GitHub Actions)

## Arquitetura

Pipeline GitHub Actions de 4 estágios, reusando o harness dockerizado (spec 23) no estágio de teste — paridade dev↔CI.

```
push/PR ─▶ test (harness spec 23, cov≥90 + ruff) ─▶ build-dev (multi-arch + Trivy + SBOM, tag <sha>)
                                                          └─▶ [demo opcional] ─▶ release (manual, v<semver>)
```

Espelha a convenção do `staffops-chaitops` (que já tem `.github/workflows/` test/build/lint/docs).

## Estágios

| Estágio | Faz | Gatilho |
|---------|-----|---------|
| `test` | `pytest --cov-fail-under=90` via harness (spec 23) + `ruff` + `mkdocs build --strict` (spec 24) | todo push/PR |
| `build-dev` | `docker buildx` multi-arch (amd64+arm64) por serviço; Trivy; SBOM CycloneDX; push `<sha>` | push na default |
| `demo` (opcional) | sobe stack + smoke (1 query single + 1 cross-domain) | push/manual |
| `release` | tag `v<semver>`; push estável | **manual** |

## Decisões e trade-offs

### Decisão 1: GitHub Actions (não GitLab CI)
**Escolha**: pipeline em GitHub Actions.
**Justificativa**: o repo **está** no GitHub (`github.com:karlipegomes/AIgent-squad`); os docs que citam GitLab CI são ficção (gitops F8). O ecossistema (chaitops) já usa GitHub Actions — consistência.
**Trade-off**: se um dia migrar pra GitLab, o desenho de 4 estágios traduz 1:1.

### Decisão 2: CI reusa o harness da spec 23 (não um caminho de teste próprio)
**Escolha**: o estágio `test` chama o **mesmo** Dockerfile/comando da spec 23.
**Justificativa**: mata "passa local, falha no CI" (divergência de ambiente). Um único harness, um único gate de 90%.
**Trade-off**: acopla o CI à existência da spec 23 — desejável (é dependência declarada).

### Decisão 3: OIDC para AWS (sem chave de longa duração)
**Escolha**: federation OIDC GitHub→AWS para push em ECR.
**Justificativa**: `cloud-security` steering proíbe chave de longa duração; OIDC dá token efêmero.
**Trade-off**: setup inicial de OIDC provider — uma vez só.

## Invariantes
- Cobertura <90% **barra o merge** (gate, não advisory).
- Imagens multi-arch (amd64+arm64) — single-arch quebra Graviton.
- `latest` proibido em prod; tags imutáveis `<sha>`/`v<semver>`.
- Sem credencial AWS de longa duração (OIDC).
- `test` usa o harness da spec 23 (sem segundo caminho).

## Dependências externas
| Serviço | Uso |
|---------|-----|
| GitHub Actions | runner |
| ECR/Harbor | registry |
| Trivy, Syft/CycloneDX | scan + SBOM |
| AWS OIDC | credenciais efêmeras |

## Verificação
- PR de teste: confirmar que cobertura forçada <90% **falha** o check.
- Confirmar manifest multi-arch (amd64+arm64) na imagem publicada.
- `mkdocs build --strict` verde no CI.

## Riscos
- Build multi-arch lento (QEMU arm64) → cache de layers + buildx; aceitável.
- OIDC mal configurado → testar push em ECR de dev antes de prod.
