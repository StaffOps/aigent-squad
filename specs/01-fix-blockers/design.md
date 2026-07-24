# Design: Fix Blockers

## Approach

Surgical changes, without refactoring the architecture (that is spec 02). Each blocker is independent and can be done in parallel.

## B1 — Root Dockerfile

The supervisor needs the entire `src/` (imports `src.supervisor`, `src.core.*`). Reuse the same pattern as the agents:

```dockerfile
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
EXPOSE 8000
CMD ["python", "-m", "src.supervisor.server"]
```

> Note: Non-root `USER` goes in spec 04 (security), to avoid mixing scope. Here we just unblock the build.

## B2 — `src/api/server.py`

Decision: **minimal rewrite** (keep the Slack feature, which is cited in the README). New flow:

```
Slack event → verify signature → supervisor.process_request(text, user_id, session_id) → chat_postMessage
```

- Removes `StateStore`, `HumanMessage`, `supervisor.graph`.
- `session_id = f"{channel}:{thread_ts}"` (keeps the original concept).
- History is already managed by `ChatStorage` inside the supervisor — the webhook doesn't need to manage state.
- `process_request` is async → async handler with `await`.

Alternative considered: delete the file. Rejected because the Slack integration is a cited differentiator and the cost of maintaining it is low after the fix.

## B3 — `gitlab_client.py`

Truncate the file at the end of the first complete class definition (before the duplicated block) and leave a single singleton. No signature changes.

Verify beforehand that `devops/agent.py` uses only methods present in the first definition (it uses `self.gitlab = gitlab_client`).

## B4 — `mcp-server.py`

Remove the second pair of `app = FastAPI(...)` / `SUPERVISOR_URL = ...` lines.

## Invariants

- Do not alter the HTTP contract of any service.
- Do not alter `requirements.txt` (unless B2 requires it — it doesn't).
- Read-only behavior preserved.

## External dependencies

None new. Uses what's already in `requirements.txt` (`slack-sdk` already present for B2).

## Verification

Build via Docker (no local SDK, per `dev-environment.md`):

```bash
docker compose build supervisor mcp-server
docker run --rm -v $(pwd):/src -w /src python:3.12-alpine \
  sh -c "pip install -q -r requirements.txt && python -c 'import src.core.gitlab_client'"
```
