# Audit — AIgent-squad

**Date**: 2026-05-30
**Branch**: `dev`
**Base commit**: `da53438` (main)
**Scope**: full read of 24 Python files, `docker-compose.yaml`, 6 Dockerfiles,
docs (`docs/*`, READMEs) and version files.

This document consolidates the findings that originate the specs in
`.kiro/specs/`. Severity:

- 🔴 **Blocker** — prevents the system from running or building.
- 🟠 **High** — breaks expected behavior, architectural debt, or security risk.
- 🟡 **Medium** — inconsistency, violates steering, or degrades operation.
- ⚪ **Low** — cosmetic, hygiene, documentation.

---

## Architecture overview (real state)

```
MCP Server (8006) ─▶ Supervisor (8000) ─▶ [aws 8001 | k8s 8002 | finops 8003 | devops 8004 | obs 8005]
                          │                         │
                          ▼                         ▼
                   Classifier (Bedrock)        Redis cache + Bedrock
                   DynamoDB (history)
```

Solid concept: supervisor + classifier + 5 read-only specialists, Redis cache,
DynamoDB history with per-agent isolation, OTel. The problem is the **distance
between the README ("v2.0, Production Ready") and the real state of the code**.

---

## 🔴 Blockers (spec 01-fix-blockers)

### B1 — Missing root Dockerfile
- `docker-compose.yaml` → `supervisor` service uses `dockerfile: Dockerfile` (root).
- Only `src/agents/*/Dockerfile` and `mcp-server/Dockerfile` exist.
- **Effect**: `docker-compose build` of the supervisor fails; `setup-local.sh`
  breaks on `build --parallel`.

### B2 — `src/api/server.py` broken (dead code)
Imports non-existent symbols:
- `from src.core.state_store import StateStore` → the real class is `ChatStorage`.
- `from langchain_core.messages import HumanMessage` → langchain removed from `requirements.txt`.
- `supervisor.graph.invoke(...)` → `SupervisorAgent` has no `.graph` (uses classifier + HTTP).
- **Effect**: the file never imports. It's the (supposed) Slack integration.
  Decide: rewrite or remove.

### B3 — `src/core/gitlab_client.py` with a duplicated body
- From ~line 330, the methods (`search_documentation`, `get_file_content`,
  `list_projects`, `get_repository_tree`, `search_code`, `get_docs_url`, encoders)
  appear **a second time**.
- The `gitlab_client = GitLabClient()` singleton declared **twice**.
- The second `search_code` behaves differently (global `/search` vs `Company`).
- **Effect**: unreachable dead code + maintenance confusion.

### B4 — `mcp-server/mcp-server.py` with a duplicated header
- `app = FastAPI(...)` and `SUPERVISOR_URL = os.getenv(...)` declared twice.
- Doesn't break execution (idempotent), but is a symptom of the same bad paste.

---

## 🟠 High — Architectural inconsistency (spec 02-unify-agent-architecture)

### A1 — Two agent patterns coexisting; servers use the wrong one

| Agent | `agent.py` (inherits base `Agent`) | Does `server.py` use `agent.py`? |
|-------|-----------------------------------|----------------------------------|
| aws | async, history, tracing, validation | ✅ yes |
| kubernetes | same | ❌ reimplements its own class |
| finops | same (Athena + Kubecost + history) | ❌ **another** `FinOpsAgent` (RAG, no Athena, no history) |
| devops | same (uses `gitlab_client`) | ❌ own class without gitlab |
| observability | (no complete base `agent.py`) | ❌ own class |

- The `agent.py` of **k8s, finops, devops, observability are dead code**.
- The servers are synchronous, **ignore `chat_history`** (receive but don't use),
  no tracing/validation, diverge from each other.
- FinOps is the worst case: the version that **runs** (server.py) lacks the
  Athena/Kubecost integration the README advertises.

### A2 — Divergent response contract
- aws/k8s `agent.py`-based → `{role, content, timestamp, agent_id}`.
- finops/devops/observability server → `{response}`.
- The supervisor masks it with
  `agent_response.get("content", agent_response.get("response", ""))`. Fragile.

### A3 — `chat_history` received and discarded
- The supervisor fetches per-agent isolated history and sends it, but the
  synchronous servers (k8s/finops/devops/obs) only pass `query`. Multi-turn
  conversation is broken in those agents.

---

## 🟠 High — Security (spec 04-harden-security)

### S1 — No endpoint has authentication
- Supervisor, 5 agents and MCP server expose open HTTP.
- Inside the cluster, any pod can invoke the AWS/K8s agent.
- Minimum: mTLS (Istio Ambient, per `cloud-security.md`) + NetworkPolicy, or a
  shared token.

### S2 — Containers run as root
- `python:3.12-alpine` Dockerfiles without `USER`.
- Violates `k8s-best-practices` (runAsNonRoot, readOnlyRootFilesystem, drop ALL caps).

### S3 — Redis without auth/TLS in compose
- `REDIS_SSL=false`, no password. Acceptable in dev, but must be documented and
  different in prod.

### S4 — Prompt injection
- User input + GitLab/docs/inventory outputs interpolated straight into the
  Bedrock prompt.
- Read-only mitigates material damage, but allows manipulating the response.
  Delimit untrusted data.

### S5 — `~/.aws` mounted in all containers
- OK for local dev. Prod **must** use IRSA (per `cloud-security.md`). Document
  and separate.

---

## 🟠 High — Cache (spec 03-fix-cache-observability)

### C1 — Native `hash()` in the cache key
- `cache_key = f"query:{hash(input_text)}"` in all agents.
- `hash()` of a str is **process-randomized** (`PYTHONHASHSEED`). Across
  replicas/restarts it never hits.
- **Fix**: `hashlib.sha256(input_text.encode()).hexdigest()`.

### C2 — Cache key ignores identity and context
- Doesn't include `user_id`/`session_id`/`chat_history`.
- Effects: (a) follow-ups ("yes", "explain more") hit the cache and return the
  wrong previous response; (b) different users share responses.
- For a conversational agent, it breaks context. Reconsider whether it makes
  sense to cache the LLM response per query.

---

## 🟡 Medium — Observability (spec 03-fix-cache-observability)

### O1 — `ConsoleSpanExporter` hardcoded
- `logger.py` exports spans to the console, not OTLP. `requirements.txt` brings
  `opentelemetry-exporter-otlp` but it's unused.
- Violates `observability-principles.md` (App SDK → OTel Collector via
  `OTEL_EXPORTER_OTLP_ENDPOINT`).

### O2 — `JSONFormatter` loses the extra fields
- It reads `record.extra`, but `logging` doesn't create that attribute —
  `logger.info(msg, extra={...})` injects the keys as direct attributes of the
  `record`.
- **Effect**: all structured context (`agent_id`, `user_id`, `duration_ms`…)
  **does not appear** in the JSON log.

### O3 — Fixed `service.name`
- `Resource.create({"service.name": "agent-squad"})` the same for all services.
  Impossible to distinguish agents in tracing.
- It should come from env (`OTEL_SERVICE_NAME`/`SERVICE_NAME`) per agent.

### O4 — `PROMETHEUS_URL` ignored
- The observability agent hardcodes
  `http://prometheus.monitoring.svc.cluster.local:9090` even though the env var
  exists in compose.
- Violates 12-factor III (config via env).

---

## ⚪ Low — Version, dates and docs

### D1 — Divergent Bedrock model (3 values)
- README: "Claude 3.5 Sonnet".
- `config.py` default: `anthropic.claude-sonnet-4-5-20250929-v1:0`.
- `.env.example`: `anthropic.claude-3-5-sonnet-20240620-v1:0`.
- Needs a single source.

### D2 — Inflated "Production Ready"
- README: "v2.0 / Production Ready / Last Updated 2026-02-14".
- Roadmap phase 1 is still "testing"; **zero tests** in the repo.
- Per `version-management.md`, "Production Ready" without a real deploy or tests
  is an inflated version.

### D3 — `docs/ARCHITECTURE.md` describes LangGraph (removed)
- The supervisor is described as "LangGraph + Bedrock". LangGraph was removed
  (commented out in `requirements.txt`).

### D4 — Corrupted encoding in docs
- "Responif", "Seforted", "Licenif", "Baif", emojis turned into "?" in
  `READ_ONLY_POLICY.md` and README.
- A find/replace or charset conversion corrupted the text.

### D5 — `datetime.utcnow()` deprecated (3.12)
- Used in `state_store.py`, agents, supervisor → `datetime.now(timezone.utc)`.

---

## ⚪ Low — Hygiene

| ID | Finding |
|----|---------|
| H1 | Monolithic `requirements.txt`: every container installs kubernetes+slack+mcp etc. Splitting per agent reduces image and surface. |
| H2 | No `.dockerignore` (the `.gitignore` lists `.dockerignore` — probably a mistake). |
| H3 | `gitlab_client` with hardcoded groups ("Company", "your-organization") — move to config. |
| H4 | `GitLabClient.base_url` hardcoded `gitlab.com`, ignores `settings.gitlab_url`. |
| H5 | `version: '3.8'` in compose is obsolete (Compose v2 ignores it). |

---

## No tests

- No test file in the repo. `pytest` not configured. Blocks the steering's
  verification criterion.
- Fix specs should include minimal tests (deterministic cache key, response
  contract, classifier parsing).

---

## Strengths (preserve)

- The supervisor/classifier/specialists separation is clean and scalable.
- Per-agent history isolation in DynamoDB (`pk = user#session`, `sk = agent#timestamp`).
- `agent_base.py` + `bedrock.py` (retry with backoff) + `aws/agent.py` are the
  **correct reference pattern** — the unification should converge to them.
- Well-thought-out read-only policy (4 layers).
- OTel already present in the dependencies; just needs proper wiring.

---

## Finding → spec map

| Spec | Covered findings |
|------|------------------|
| `01-fix-blockers` | B1, B2, B3, B4 |
| `02-unify-agent-architecture` | A1, A2, A3, H1 |
| `03-fix-cache-observability` | C1, C2, O1, O2, O3, O4, D5 |
| `04-harden-security` | S1, S2, S3, S4, S5 |
| `ROADMAP.md` + README | D1, D2, D3, D4, H2–H5, tests |
