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
**Severity**: 🟠 High (open-source project — docs are the community's entry point)
**Origin**: user requirement (organize extensive/confusing docs; markdown portal Material-style, inspired by the DevOps portal); docs findings (`ANALYSIS.md` D6–D15, AUDIT D2–D4)
**Relation**: consumes "Proposal 1 — Documentation restructure" and "Proposal 2 — ADR" from the documentation specialist (`ANALYSIS.md`); follows the MkDocs Material convention from `staffops-chaitops` and `staffops-anomaly-detection`.

The current documentation is scattered and contradictory: ~32KB README + `CHANGES.md` + `IMPLEMENTATION_HISTORY.md` + `VERSIONS.md` + `GENERIC_VERSION.md` + `QUICKSTART.md` + 9 files in `docs/`, with overlap (3 docs explain "how to run"), ghost content (`server_new.py`, `terraform/`) and corrupted encoding. To distribute to the community, consolidate everything into a **MkDocs Material portal** (`docs_dir: src` → `site_dir: public`), with the README becoming a lean index, organized by **Diátaxis** (tutorial / how-to / reference / explanation).

## User Stories

WHEN a new user arrives at the repository THEN the `README.md` SHALL be a **short index** (overview + diagram + link table), not an encyclopedia.

WHEN someone wants to learn the project THEN they SHALL find a navigable MkDocs Material portal organized by sections (Getting Started, Architecture, Agents, API, Operations, Development), renderable locally with a script.

WHEN the documentation describes the system THEN it SHALL reflect the **actual state** of the code (no `server_new.py`, no ghost `terraform/`, no LangGraph, no corrupted encoding, honest `0.x` version).

WHEN a relevant architectural decision exists (LangGraph removal, Bedrock-direct, read-only, classifier, ecosystem repositioning) THEN there SHALL be an **ADR** recording context/decision/consequences.

WHEN an external contributor wants to help THEN they SHALL find `CONTRIBUTING`, testing guide (spec 23), and how to add an agent (spec 22).

WHEN the portal is built THEN it SHALL use the **same convention** as the other StaffOps portals (Material, `src`→`public`, Mermaid, git-revision-date) for ecosystem consistency.

WHEN ghost/redundant docs exist today THEN they SHALL be **removed or absorbed** (no orphan file left referenced).

## Acceptance Criteria

- [ ] `mkdocs.yml` (Material theme, `docs_dir: src`, `site_dir: public`, plugins search + git-revision-date, Mermaid) consistent with the chaitops/anomaly-detection portals.
- [ ] Structure `docs/src/` by section: `getting-started/`, `architecture/`, `agents/`, `api/`, `operations/`, `development/` (each with `index.md`).
- [ ] README reduced to index: overview + diagram + link table to the portal (does not duplicate content).
- [ ] **Consolidation**: `QUICKSTART.md` + `docs/LOCAL_DEVELOPMENT.md` + `docs/SETUP.md` + `docs/PREREQUISITES.md` → `getting-started/`; `docs/ARCHITECTURE.md` (rewritten, no LangGraph) → `architecture/`; `docs/READ_ONLY_POLICY.md` → `architecture/` or `agents/`; `docs/OBSERVABILITY.md` → `operations/`; `docs/RAG_IMPLEMENTATION.md` → mark real status.
- [ ] **Removal**: `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `docs/MIGRATION.md` (ghost) → useful content absorbed; rest deleted. `IMPLEMENTATION_HISTORY.md` → archived (outside the portal).
- [ ] `CHANGELOG.md` (Keep a Changelog) replaces `CHANGES.md`/`VERSIONS.md`; honest `0.x` version.
- [ ] `architecture/decisions.md` (or `architecture/adr/`) with ADRs: LangGraph removed, Bedrock-direct, read-only 4-layers, classifier vs tool-use, ecosystem repositioning (`ECOSYSTEM.md`).
- [ ] `development/`: building, testing (points to spec 23), contributing (new CONTRIBUTING), specs (points to `specs/`).
- [ ] Corrupted encoding fixed in migrated files (D8: "docker-compoif", "Responif", etc.).
- [ ] Script `docs/scripts/serve.sh` + `build.sh` (MkDocs via Docker — no local install, per `dev-environment`).
- [ ] No broken internal links; no reference to a non-existent file.
- [ ] `specs/` remains the SSOT for specs; the portal **links** to specs, does not duplicate them.

## Out of scope

- Publishing/hosting the portal (GitHub Pages/S3) — spec 08 (CI/CD) can add the deploy later.
- Multi-language translation — choose 1 language (follow repo convention; docs in English for open-source, except specs that were already in PT).
- Rewriting the technical content of each page beyond fixing state/encoding — migration + correction, not a total rewrite (except `ARCHITECTURE.md`, which is obsolete).
- Automatic API docs generation from OpenAPI — future (there is a skill `api-docs-patterns` if needed).
