# Design: Documentation Portal (MkDocs)

## Architecture

MkDocs Material portal, identical in convention to the `staffops-chaitops` and `staffops-anomaly-detection` portals (ecosystem consistency). README becomes an index; content lives in `docs/src/`; specs remain in `specs/` (SSOT) and are **linked**, not duplicated.

```
README.md            ← short index (overview + diagram + link table)
CHANGELOG.md         ← Keep a Changelog (replaces CHANGES/VERSIONS)
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
specs/          ← SSOT for specs (linked from development/specs.md)
```

## Migration map (consolidation)

| Source (today) | Destination | Action |
|----------------|-------------|--------|
| `README.md` (32KB) | `README.md` (index) + `src/index.md` | reduce to index; move content |
| `QUICKSTART.md`, `docs/LOCAL_DEVELOPMENT.md`, `docs/SETUP.md`, `docs/PREREQUISITES.md` | `getting-started/` | merge (remove overlap + port collision D9) |
| `docs/ARCHITECTURE.md` | `architecture/` | **rewrite** (remove LangGraph/HPA, fix encoding) |
| `docs/READ_ONLY_POLICY.md` | `architecture/read-only.md` | migrate + fix encoding |
| `docs/OBSERVABILITY.md` | `operations/observability.md` | migrate |
| `docs/RAG_IMPLEMENTATION.md` | `architecture/` or `agents/` | migrate + mark real status |
| `docs/MCP_INTEGRATION.md` | `api/mcp.md` | migrate + fix encoding |
| `docs/GITLAB_CI_SETUP.md` | `development/` or remove | fix (repo is GitHub) or absorb into spec 08 |
| `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md` | `CHANGELOG.md` | absorb useful content, **delete** |
| `docs/MIGRATION.md` | — | **delete** (ghost: references server_new.py) |
| `IMPLEMENTATION_HISTORY.md` | `archive/` (outside the portal) | archive (history) |

## ADRs (`architecture/decisions.md` or `architecture/adr/`)

Follow the format from the docs specialist (`ANALYSIS.md` Proposal 2): Status · Context · Decision · Consequences · Alternatives.

1. Removal of LangGraph (Bedrock direct)
2. Bedrock-direct classifier (not tool-use routing)
3. Read-only 4-layers (consultative)
4. Classifier over manual routing
5. Repositioning in the StaffOps ecosystem (summary of `ECOSYSTEM.md`)

## Rationale (decisions and trade-offs)

### Decision 1: MkDocs Material identical to the other StaffOps portals

**Choice**: reuse the exact convention (`docs_dir: src`→`site_dir: public`, Material, Mermaid, git-revision-date, section-based nav) from chaitops/anomaly-detection.

**Justification, in order of strength**:
1. **Ecosystem consistency**: anyone navigating one StaffOps portal navigates all the same way — reduces friction for the community and for yourself.
2. **Proven**: both repos already run this; zero tooling risk.
3. **Built-in Diátaxis**: the section-based nav (getting-started/architecture/operations/development) already separates tutorial/how-to/reference/explanation.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| One more build step (mkdocs) | Runs via Docker (no local install); optional for those who only read the markdown |
| Maintaining nav in `mkdocs.yml` | Trivial; the navigability gain compensates |

**When it would be wrong**: if the project adopts another docs stack in the ecosystem (unlikely — there are already 2 Material portals).

### Decision 2: README = index; `specs/` = SSOT (portal links, does not duplicate)

**Choice**: short README pointing to the portal; specs remain in `specs/` and are referenced, not copied.

**Justification**: the current anti-pattern is duplication (giant README + overlapping docs). One source per content type eliminates drift (steering `documentation-sync`). Specs have their own lifecycle (spec-driven-workflow) — copying them to the portal would create two truths.

**Trade-off accepted**: the portal reader needs 1 click to reach the specs — acceptable; specs are contributor material, not end-user material.

### Decision 3: Actively delete ghost/redundant docs, not just "set aside"

**Choice**: actively remove `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `docs/MIGRATION.md`.

**Justification**: outdated documentation is **worse** than no documentation — it deceives (steering `documentation-sync`, anti-pattern). `MIGRATION.md` tells you to run a non-existent `server_new.py`. Git preserves history; there is no loss.

**Trade-off accepted**: loss of "historical record" in the working tree → `IMPLEMENTATION_HISTORY.md` archived covers this.

## Invariants

- README does **NOT** duplicate portal content (it is an index).
- `specs/` is the **only** source for specs; portal links.
- No portal doc references a non-existent file/feature.
- Portal build/serve runs **via Docker** (no local install — `dev-environment`).
- Portal language consistent (English for open-source; specs in PT remain).

## External dependencies

| Lib | Usage |
|-----|-------|
| mkdocs-material | theme/portal |
| mkdocs-git-revision-date-localized-plugin | timestamps |
| (Docker) `squidfunk/mkdocs-material` image | serve/build without local install |

## Verification

```bash
# build without broken-link warnings (via Docker)
docker run --rm -v "$PWD/docs:/docs" squidfunk/mkdocs-material build --strict
```
`--strict` fails on broken links/orphan nav. Validate: README is index only; no migrated file references `server_new.py`/`terraform/`; encoding fixed; `mkdocs build --strict` green.

## Risks

- Migration loses content → do it in stages, checking each source before deleting.
- Broken links after migration → `mkdocs build --strict` in CI (spec 08) catches them.
- Mixed language (specs PT, portal EN) → document the convention explicitly; acceptable (specs are internal).
