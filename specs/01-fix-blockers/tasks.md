# Tasks: Fix Blockers

- [x] T1: Create `Dockerfile` at root for the supervisor (B1)
- [x] T2: Validate `docker compose build supervisor` (depends on: T1)
- [x] T3: Clean up duplication in `src/core/gitlab_client.py` — keep 1st definition + 1 singleton (B3)
- [x] T4: Confirm that `devops/agent.py` only uses methods from the kept definition (depends on: T3)
- [x] T5: Remove duplicated header in `mcp-server/mcp-server.py` (B4)
- [x] T6: Rewrite `src/api/server.py` using `supervisor.process_request` + `ChatStorage`, without langchain/StateStore/graph (B2)
- [x] T7: Validate import of `src.api.server` and `src.core.gitlab_client` via container (depends on: T3, T6)
- [x] T8: Run `docker compose up -d` and confirm green build + health-checks (depends on: T1, T6)

## Completed: 2026-06-14

Additional changes needed during execution:
- Created `__init__.py` in all `src/` directories (Python packages)
- Supervisor healthcheck: `wget --spider` → `curl -f` (GNU wget sends HEAD, FastAPI rejects with 405)
- Supervisor Dockerfile uses `python:3.11-slim` + curl (agents use `python:3.12-alpine` + BusyBox wget)
