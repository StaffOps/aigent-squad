# Tasks: Documentation Portal (MkDocs)

> Follows the MkDocs Material convention from `staffops-chaitops`/`staffops-anomaly-detection`. Consumes Proposals 1/2 from the `ANALYSIS.md`. Fixes D6–D15 + AUDIT D2–D4.

- [ ] T1: `docs/mkdocs.yml` (Material, `docs_dir: src`, `site_dir: public`, search + git-revision-date, Mermaid) mirroring existing portals
- [ ] T2: Skeleton `docs/src/` with `index.md` per section (getting-started, architecture, agents, api, operations, development) (depends on: T1)
- [ ] T3: `docs/scripts/serve.sh` + `build.sh` (MkDocs via Docker, no local install)
- [ ] T4: Migrate Getting Started — merge QUICKSTART + LOCAL_DEVELOPMENT + SETUP + PREREQUISITES; fix port collision (D9) (depends on: T2)
- [ ] T5: **Rewrite** `architecture/` — components/data-flow/read-only; remove LangGraph/HPA (D3/D12), fix encoding (D8) (depends on: T2)
- [ ] T6: `architecture/decisions.md` (ADRs): LangGraph, Bedrock-direct, read-only, classifier, repositioning (ECOSYSTEM) (depends on: T5)
- [ ] T7: Migrate Operations (observability) + API (mcp, supervisor, auth) + Agents (specialists, capability-manifest→spec 22, adding-an-agent); fix encoding (depends on: T2)
- [ ] T8: `development/` — building, testing (→spec 23), contributing (new CONTRIBUTING), specs (link `specs/`) (depends on: T2)
- [ ] T9: `CHANGELOG.md` (Keep a Changelog, version `0.x`); absorb and **delete** CHANGES/VERSIONS/GENERIC_VERSION; delete MIGRATION (ghost); archive IMPLEMENTATION_HISTORY (depends on: T4–T8)
- [ ] T10: Reduce `README.md` to index (overview + diagram + link table to the portal) (depends on: T4–T9)
- [ ] T11: `mkdocs build --strict` via Docker — zero broken links / orphan nav; no reference to server_new.py/terraform (depends on: T10)
- [ ] T12: Independent review (`code-review`/`documentation`): real state, no README↔portal duplication, clean encoding, valid links (depends on: T11)

## Suggested order
T1→T2→T3; T4/T5/T7/T8 in parallel; T6 (after T5); T9 (after migrations); T10; T11→T12.

## Notes
- `specs/` remains the SSOT — the portal **links**, does not duplicate.
- Outdated documentation is worse than absence: actively delete ghost content (git preserves history).
- Build/serve only via Docker (`dev-environment`); hosting (GitHub Pages) is for spec 08.
- Language: portal in English (open-source); specs in PT remain — document the convention.
- Per `documentation-sync`: this spec IS the docs synchronization — upon completion, the README and portal become the single source.
