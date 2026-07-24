# Bugfix: Fix Blockers

**Spec**: `01-fix-blockers`
**Severity**: 🔴 Blocker
**Findings**: B1, B2, B3, B4 (see `../AUDIT.md`)

Goal: make `docker-compose up` functional and remove dead/broken code that misleads anyone reading the repo.

---

## B1 — Root Dockerfile missing

**Current behavior**: `docker-compose.yaml` points the `supervisor` service to `dockerfile: Dockerfile` at root; the file doesn't exist. `docker-compose build` and `setup-local.sh` fail.

**Expected behavior**: a `Dockerfile` exists at root that builds the supervisor image (and serves as a reusable base), and the compose build succeeds.

**Unchanged**: Agent and mcp-server Dockerfiles.

---

## B2 — `src/api/server.py` broken

**Current behavior**: imports `StateStore` (actual class: `ChatStorage`), `langchain_core.messages.HumanMessage` (langchain removed), and uses `supervisor.graph.invoke()` (`SupervisorAgent` has no `.graph`). The module never imports successfully.

**Expected behavior**: the Slack integration works via `supervisor.process_request(...)` and `ChatStorage`, OR the file is removed if the Slack integration is not a priority.

**Decision**: rewrite minimally using the supervisor's current API (the supervisor is already the entry point at `/query`; `api/server.py` becomes merely the Slack webhook that calls `supervisor.process_request`). No langchain, no StateStore, no graph.

**Unchanged**: `src/supervisor/agent.py`, `src/supervisor/server.py`.

---

## B3 — `gitlab_client.py` with duplicated body

**Current behavior**: methods and singleton declared twice (from ~line 330 onward). Second half is unreachable and diverges from the first (`search_code` global vs `Company`).

**Expected behavior**: a single definition of each method; a single `gitlab_client = GitLabClient()`. Behavior preserved = first definition (the one in use).

**Unchanged**: public method signatures consumed by `devops/agent.py` (`search_documentation`, `get_file_content`, `list_projects`, `get_repository_tree`, `search_code`, `search_in_company`, `list_all_projects`, `get_docs_url`).

---

## B4 — `mcp-server.py` duplicated header

**Current behavior**: `app = FastAPI(...)` and `SUPERVISOR_URL` declared twice. Idempotent, but messy.

**Expected behavior**: single declaration.

**Unchanged**: routes `/health`, `/query` and contract `QueryRequest`/`QueryResponse`.

---

## Acceptance criteria

- [ ] `docker compose build` completes without error for all services.
- [ ] `python -c "import src.api.server"` (via container) doesn't raise ImportError — or the file was removed.
- [ ] `python -c "import src.core.gitlab_client"` imports; `grep -c "gitlab_client = GitLabClient()"` returns 1.
- [ ] `mcp-server.py` has a single `app` assignment.
- [ ] `setup-local.sh` reaches health-check without build failure.
