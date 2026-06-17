# Project Steering — AIgent-squad

Project-specific rules that extend the global StaffOps steering.

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Framework | FastAPI + uvicorn |
| LLM | AWS Bedrock (Anthropic Claude via `bedrock-runtime`) |
| State | DynamoDB (history, TTL 24h) — `ChatStorage` class |
| Cache | Redis (infra data; **not** LLM responses) |
| Observability | OpenTelemetry (OTLP via env) + JSON logging |
| Deploy | Docker Compose (local) → EKS (target) |

## Architecture (invariants)

- **1 supervisor + 5 specialists** (aws, kubernetes, finops, devops, observability), each in its own pod/port (8000–8005), MCP server on 8006.
- Routing is done by the **classifier** (Bedrock), never manually.
- Every agent inherits from `src/core/agent_base.py::Agent` and implements `async process_request(...)`. **Reference pattern: `src/agents/aws/`.**
- `server.py` is HTTP transport + instrumentation only. **Zero business logic in the server.**
- Single response contract: `{role, content, timestamp, agent_id}`.
- Per-agent isolated history: DynamoDB `pk = user#session`, `sk = agent#timestamp`.

## Prohibitions (project anti-patterns)

- ❌ Redefining the agent class inside `server.py` (use `agent.py`).
- ❌ Native `hash()` in a cache key (non-deterministic across processes — use `hashlib.sha256`).
- ❌ Caching the LLM response per query (leaks across users, breaks multi-turn).
- ❌ `/process` or `/query` endpoint without authentication (`require_token`). `/health` is open.
- ❌ Container running as root (use `USER 65534`).
- ❌ Hardcoding a URL/endpoint that has an env var (`PROMETHEUS_URL`, `gitlab_url`, Bedrock model).
- ❌ Suggesting write/mutation commands — the system is **read-only today** (4 layers: prompt, IAM deny, RBAC, refusal templates).
- ❌ `datetime.utcnow()` (deprecated) — use `datetime.now(timezone.utc)`.
- ❌ Reintroducing LangGraph (it was removed; uses Bedrock directly).

## Read-only is the current posture

All agents are consultative today. They never execute `create/update/delete/terminate`. On a change request: refuse and point to automation (Terraform/ArgoCD/GitOps). See `docs/READ_ONLY_POLICY.md`.

> Read-only is the **current** posture, not a permanent lock — execution is an
> open roadmap item, gated by the spec 14 guardrails + human-in-the-loop. See
> `.kiro/specs/14-security-hardening/` and `ADR-001`.

## Conventions

- The Bedrock model comes from a **single source** (`config.py` → env `BEDROCK_MODEL_ID`). README, `.env.example` and compose must agree.
- Builds and tests **via Docker** (no local SDK) — `python:3.11-slim` for tests (not 3.12, due to pkg_resources/OTel).
- Spec-driven: an architectural change updates the corresponding spec's `design.md` **before** implementing.
- Versioning by validated milestone, not by feature (see global steering `version-management.md`). Do not mark "Production Ready" without a real deploy + tests.

## Current work order (Phase 0)

`01-fix-blockers` → `02-unify-agent-architecture` → `03-fix-cache-observability` → `04-harden-security`.
Deploy (Phase 2): `05-helm-chart` (Helm chart for EKS).
See `.kiro/specs/ROADMAP.md` and `.kiro/specs/AUDIT.md`.
