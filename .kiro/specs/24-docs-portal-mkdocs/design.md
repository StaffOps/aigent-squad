# Design: Documentation Portal (MkDocs)

## Arquitetura

Portal MkDocs Material, idêntico em convenção aos portais `staffops-chaitops` e `staffops-anomaly-detection` (consistência no ecossistema). README vira índice; conteúdo vive em `docs/src/`; specs continuam em `.kiro/specs/` (SSOT) e são **linkadas**, não duplicadas.

```
README.md            ← índice curto (overview + diagrama + tabela de links)
CHANGELOG.md         ← Keep a Changelog (substitui CHANGES/VERSIONS)
docs/
├── mkdocs.yml       ← Material, docs_dir: src, site_dir: public, Mermaid, git-revision-date
├── scripts/
│   ├── serve.sh     ← mkdocs serve via Docker
│   └── build.sh     ← mkdocs build via Docker
└── src/
    ├── index.md
    ├── getting-started/   (index, local-dev, env-vars, first-run)
    ├── architecture/      (index, components, data-flow, read-only, decisions[ADR])
    ├── agents/            (index, specialists, capability-manifest, adding-an-agent)
    ├── api/               (index, supervisor, mcp, auth)
    ├── operations/        (index, deployment, observability, troubleshooting)
    └── development/       (index, building, testing, contributing, specs)
.kiro/specs/          ← SSOT das specs (linkado de development/specs.md)
```

## Mapa de migração (consolidação)

| Origem (hoje) | Destino | Ação |
|---------------|---------|------|
| `README.md` (32KB) | `README.md` (índice) + `src/index.md` | reduzir a índice; mover conteúdo |
| `QUICKSTART.md`, `docs/LOCAL_DEVELOPMENT.md`, `docs/SETUP.md`, `docs/PREREQUISITES.md` | `getting-started/` | mesclar (remove sobreposição + colisão de porta D9) |
| `docs/ARCHITECTURE.md` | `architecture/` | **reescrever** (remove LangGraph/HPA, encoding) |
| `docs/READ_ONLY_POLICY.md` | `architecture/read-only.md` | migrar + corrigir encoding |
| `docs/OBSERVABILITY.md` | `operations/observability.md` | migrar |
| `docs/RAG_IMPLEMENTATION.md` | `architecture/` ou `agents/` | migrar + marcar status real |
| `docs/MCP_INTEGRATION.md` | `api/mcp.md` | migrar + corrigir encoding |
| `docs/GITLAB_CI_SETUP.md` | `development/` ou remover | corrigir (repo é GitHub) ou absorver na spec 08 |
| `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md` | `CHANGELOG.md` | absorver o útil, **deletar** |
| `docs/MIGRATION.md` | — | **deletar** (fantasma: server_new.py) |
| `IMPLEMENTATION_HISTORY.md` | `archive/` (fora do portal) | arquivar (histórico) |

## ADRs (`architecture/decisions.md` ou `architecture/adr/`)

Seguir formato do specialist de docs (`ANALYSIS.md` Proposta 2): Status · Context · Decision · Consequences · Alternatives.

1. Remoção do LangGraph (Bedrock direto)
2. Bedrock-direct classifier (não tool-use routing)
3. Read-only 4-camadas (consultivo)
4. Classifier sobre routing manual
5. Reposicionamento no ecossistema StaffOps (resumo do `ECOSYSTEM.md`)

## Rationale (decisões e trade-offs)

### Decisão 1: MkDocs Material idêntico aos outros portais StaffOps

**Escolha**: reusar a convenção exata (`docs_dir: src`→`site_dir: public`, Material, Mermaid, git-revision-date, nav por seções) de chaitops/anomaly-detection.

**Justificativa, em ordem de força**:
1. **Consistência de ecossistema**: quem navega um portal StaffOps navega todos igual — reduz fricção pra comunidade e pra você.
2. **Provado**: os dois repos já rodam isso; zero risco de tooling.
3. **Diátaxis embutido**: a nav por seções (getting-started/architecture/operations/development) já separa tutorial/how-to/referência/explicação.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Mais um build step (mkdocs) | Roda via Docker (sem install local); opcional pra quem só lê o markdown |
| Manter nav em `mkdocs.yml` | Trivial; o ganho de navegabilidade compensa |

**Quando estaria errada**: se o projeto adotar outra stack de docs no ecossistema (improvável — já há 2 portais Material).

### Decisão 2: README = índice; `.kiro/specs/` = SSOT (portal linka, não duplica)

**Escolha**: README curto apontando pro portal; specs permanecem em `.kiro/specs/` e são referenciadas, não copiadas.

**Justificativa**: o anti-pattern atual é duplicação (README gigante + docs sobrepostos). Uma fonte por tipo de conteúdo elimina drift (steering `documentation-sync`). Specs têm ciclo próprio (spec-driven-workflow) — copiá-las pro portal criaria duas verdades.

**Trade-off aceito**: o leitor do portal dá 1 clique pra chegar nas specs — aceitável; specs são material de contribuidor, não de usuário final.

### Decisão 3: Deletar docs fantasma/redundantes, não só "deixar de lado"

**Escolha**: remover ativamente `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `docs/MIGRATION.md`.

**Justificativa**: doc desatualizada é **pior** que ausência — engana (steering `documentation-sync`, anti-pattern). `MIGRATION.md` manda rodar `server_new.py` inexistente. Git preserva o histórico; não há perda.

**Trade-off aceito**: perda de "registro histórico" no working tree → `IMPLEMENTATION_HISTORY.md` arquivado cobre isso.

## Invariantes

- README **não** duplica conteúdo do portal (é índice).
- `.kiro/specs/` é a **única** fonte das specs; portal linka.
- Nenhum doc no portal referencia arquivo/feature inexistente.
- Build/serve do portal roda **via Docker** (sem install local — `dev-environment`).
- Idioma do portal consistente (inglês para open-source; specs em PT permanecem).

## Dependências externas

| Lib | Uso |
|-----|-----|
| mkdocs-material | tema/portal |
| mkdocs-git-revision-date-localized-plugin | timestamps |
| (Docker) `squidfunk/mkdocs-material` image | serve/build sem install local |

## Verificação

```bash
# build sem warnings de link quebrado (via Docker)
docker run --rm -v "$PWD/docs:/docs" squidfunk/mkdocs-material build --strict
```
`--strict` falha em link quebrado/nav órfã. Validar: README só índice; nenhum arquivo migrado referencia `server_new.py`/`terraform/`; encoding corrigido; `mkdocs build --strict` verde.

## Riscos

- Migração perde conteúdo → fazer por etapas, conferindo cada origem antes de deletar.
- Link quebrado pós-migração → `mkdocs build --strict` no CI (spec 08) pega.
- Idioma misto (specs PT, portal EN) → documentar a convenção explicitamente; aceitável (specs são internas).
