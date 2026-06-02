# Tasks: Documentation Portal (MkDocs)

> Segue a convenção MkDocs Material de `staffops-chaitops`/`staffops-anomaly-detection`. Consome as Propostas 1/2 de docs do `ANALYSIS.md`. Corrige D6–D15 + AUDIT D2–D4.

- [ ] T1: `docs/mkdocs.yml` (Material, `docs_dir: src`, `site_dir: public`, search + git-revision-date, Mermaid) espelhando os portais existentes
- [ ] T2: Esqueleto `docs/src/` com `index.md` por seção (getting-started, architecture, agents, api, operations, development) (depends on: T1)
- [ ] T3: `docs/scripts/serve.sh` + `build.sh` (MkDocs via Docker, sem install local)
- [ ] T4: Migrar Getting Started — mesclar QUICKSTART + LOCAL_DEVELOPMENT + SETUP + PREREQUISITES; corrigir colisão de porta (D9) (depends on: T2)
- [ ] T5: **Reescrever** `architecture/` — components/data-flow/read-only; remover LangGraph/HPA (D3/D12), corrigir encoding (D8) (depends on: T2)
- [ ] T6: `architecture/decisions.md` (ADRs): LangGraph, Bedrock-direto, read-only, classifier, reposicionamento (ECOSYSTEM) (depends on: T5)
- [ ] T7: Migrar Operations (observability) + API (mcp, supervisor, auth) + Agents (specialists, capability-manifest→spec 22, adding-an-agent); corrigir encoding (depends on: T2)
- [ ] T8: `development/` — building, testing (→spec 23), contributing (novo CONTRIBUTING), specs (link `.kiro/specs/`) (depends on: T2)
- [ ] T9: `CHANGELOG.md` (Keep a Changelog, versão `0.x`); absorver e **deletar** CHANGES/VERSIONS/GENERIC_VERSION; deletar MIGRATION (fantasma); arquivar IMPLEMENTATION_HISTORY (depends on: T4–T8)
- [ ] T10: Reduzir `README.md` a índice (overview + diagrama + tabela de links pro portal) (depends on: T4–T9)
- [ ] T11: `mkdocs build --strict` via Docker — zero link quebrado / nav órfã; nenhuma referência a server_new.py/terraform (depends on: T10)
- [ ] T12: Review independente (`code-review`/`documentation`): estado real, sem duplicação README↔portal, encoding limpo, links válidos (depends on: T11)

## Ordem sugerida
T1→T2→T3; T4/T5/T7/T8 em paralelo; T6 (após T5); T9 (após migrações); T10; T11→T12.

## Notas
- `.kiro/specs/` continua SSOT — o portal **linka**, não duplica.
- Doc desatualizada é pior que ausência: deletar fantasma ativamente (git preserva histórico).
- Build/serve só via Docker (`dev-environment`); hosting (GitHub Pages) fica pra spec 08.
- Idioma: portal em inglês (open-source); specs em PT permanecem — documentar a convenção.
- Per `documentation-sync`: esta spec É a sincronização de docs — ao concluir, o README e o portal ficam a fonte única.
