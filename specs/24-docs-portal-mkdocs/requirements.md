---
spec: 24-docs-portal-mkdocs
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Documentation Portal (MkDocs) + Consolidation

**Spec**: `24-docs-portal-mkdocs`
**Severidade**: 🟠 High (projeto open-source — docs são a porta de entrada da comunidade)
**Origem**: requisito do usuário (organizar docs extensas/confusas; portal markdown estilo Material, inspirado no portal DevOps); achados de docs (`ANALYSIS.md` D6–D15, AUDIT D2–D4)
**Relação**: consome a "Proposta 1 — Documentation restructure" e "Proposta 2 — ADR" do specialist de documentação (`ANALYSIS.md`); segue a convenção MkDocs Material de `staffops-chaitops` e `staffops-anomaly-detection`.

A documentação atual está espalhada e contraditória: README de ~32KB + `CHANGES.md` + `IMPLEMENTATION_HISTORY.md` + `VERSIONS.md` + `GENERIC_VERSION.md` + `QUICKSTART.md` + 9 arquivos em `docs/`, com sobreposição (3 docs explicam "como rodar"), conteúdo fantasma (`server_new.py`, `terraform/`) e encoding corrompido. Para distribuir à comunidade, consolidar tudo num **portal MkDocs Material** (`docs_dir: src` → `site_dir: public`), com README virando índice enxuto, organizado por **Diátaxis** (tutorial / how-to / referência / explicação).

## User Stories

WHEN um novo usuário chega ao repositório THEN o `README.md` SHALL ser um **índice curto** (visão geral + diagrama + tabela de links), não uma enciclopédia.

WHEN alguém quer aprender o projeto THEN SHALL encontrar um portal MkDocs Material navegável por seções (Getting Started, Architecture, Agents, API, Operations, Development), renderizável local com um script.

WHEN a documentação descreve o sistema THEN ela SHALL refletir o **estado real** do código (sem `server_new.py`, sem `terraform/` fantasma, sem LangGraph, sem encoding corrompido, versão honesta `0.x`).

WHEN uma decisão arquitetural relevante existe (remoção do LangGraph, Bedrock-direto, read-only, classifier, reposicionamento no ecossistema) THEN SHALL haver um **ADR** registrando contexto/decisão/consequências.

WHEN um contribuidor externo quer ajudar THEN SHALL encontrar `CONTRIBUTING`, guia de testes (spec 23), e como adicionar um agente (spec 22).

WHEN o portal é construído THEN SHALL usar a **mesma convenção** dos outros portais StaffOps (Material, `src`→`public`, Mermaid, git-revision-date) para consistência no ecossistema.

WHEN docs redundantes/fantasma existem hoje THEN SHALL ser **removidos ou absorvidos** (sem deixar arquivo órfão referenciado).

## Acceptance Criteria

- [ ] `mkdocs.yml` (tema Material, `docs_dir: src`, `site_dir: public`, plugins search + git-revision-date, Mermaid) consistente com os portais chaitops/anomaly-detection.
- [ ] Estrutura `docs/src/` por seção: `getting-started/`, `architecture/`, `agents/`, `api/`, `operations/`, `development/` (cada uma com `index.md`).
- [ ] README reduzido a índice: overview + diagrama + tabela de links para o portal (não duplicar conteúdo).
- [ ] **Consolidação**: `QUICKSTART.md` + `docs/LOCAL_DEVELOPMENT.md` + `docs/SETUP.md` + `docs/PREREQUISITES.md` → `getting-started/`; `docs/ARCHITECTURE.md` (reescrito, sem LangGraph) → `architecture/`; `docs/READ_ONLY_POLICY.md` → `architecture/` ou `agents/`; `docs/OBSERVABILITY.md` → `operations/`; `docs/RAG_IMPLEMENTATION.md` → marcar status real.
- [ ] **Remoção**: `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `docs/MIGRATION.md` (fantasma) → conteúdo útil absorvido; resto deletado. `IMPLEMENTATION_HISTORY.md` → arquivado (fora do portal).
- [ ] `CHANGELOG.md` (Keep a Changelog) substitui `CHANGES.md`/`VERSIONS.md`; versão honesta `0.x`.
- [ ] `architecture/decisions.md` (ou `architecture/adr/`) com ADRs: LangGraph removido, Bedrock-direto, read-only 4-camadas, classifier vs tool-use, reposicionamento no ecossistema (`ECOSYSTEM.md`).
- [ ] `development/`: building, testing (aponta spec 23), contributing, specs (aponta `specs/`).
- [ ] Encoding corrompido corrigido nos arquivos migrados (D8: "docker-compoif", "Responif", etc.).
- [ ] Script `docs/scripts/serve.sh` + `build.sh` (MkDocs via Docker — sem instalar local, per `dev-environment`).
- [ ] Nenhum link interno quebrado; nenhuma referência a arquivo inexistente.
- [ ] `specs/` permanece a SSOT de specs; o portal **linka** para specs, não as duplica.

## Fora de escopo

- Publicação/hosting do portal (GitHub Pages/S3) — a spec 08 (CI/CD) pode adicionar o deploy depois.
- Tradução multi-idioma — escolher 1 idioma (seguir convenção do repo; docs em inglês para open-source, exceto specs que já estão em PT).
- Reescrever o conteúdo técnico de cada página além de corrigir estado/encoding — migração + correção, não rewrite total (exceto `ARCHITECTURE.md`, que está obsoleto).
- Geração automática de API docs a partir de OpenAPI — futuro (há skill `api-docs-patterns` se necessário).
