# staffops-aigent-squad — Agent Guide

> Canonical, tool-agnostic guide for any AI coding assistant (Claude Code,
> Cursor, Copilot, Aider, …). Tool-specific files (`CLAUDE.md`) just point here.
> Detailed rules live in [`steering/`](steering/); plans in [`specs/`](specs/).

Multi-agent AI platform for AWS/Kubernetes operations: **1 supervisor + 5 specialists (in-process)
+ MCP server**. Config-driven, Bedrock-direct, read-only by default.

> **Status**: Phase 0 complete (stabilization). Work on branch `dev`. Never push to `main`.
> Blockers tracked in `specs/AUDIT.md`. Plan in `specs/ROADMAP.md`.

---

## Architecture

```
User (Slack / HTTP / LibreChat)
         │
         ▼
  Supervisor :8000
  ├── Classifier (Bedrock) → routes to 1–3 agents
  ├── Fan-out (parallel) → synthesizer merges N responses
  ├── RCA investigation → all agents collect evidence in parallel
  └── DynamoDB (history, 24h TTL, per-agent isolated)
         │
  ┌──────┴──────────────────────────────┐
  │ In-process specialists (no ports)   │
  │  aws · kubernetes · finops          │
  │  devops · observability             │
  └─────────────────────────────────────┘
         │
  Redis (datasource cache, 1–60min TTL)
  Bedrock (Claude Sonnet 4.5 inference profile)
  PostgreSQL + pgvector (knowledge base, optional)
```

### Key invariants — do not violate

1. **Classifier always routes** — routing is a Bedrock call, never manual `if/else`
2. **Per-agent isolated history** — DynamoDB `pk=user#session`, `sk=agent#timestamp`
3. **GenericAgent pattern** — all 5 specialists inherit `Agent`, implement `async process_request(...)`
4. **Single Bedrock model source** — `src/core/config.py` → env `BEDROCK_MODEL_ID`
5. **Read-only posture** — 4 layers: system prompt + IAM deny + K8s RBAC + response templates
6. **Fail-open** — Redis/DynamoDB loss = service continues (agents get empty history/cache miss)
7. **Deterministic cache keys** — `hashlib.sha256()`, never native `hash()`
8. **No business logic in `server.py`** — transport + auth only; all logic in `agent.py`
9. **Single response contract** — `{role, content, timestamp, agent_id}`

---

## Build, test, lint — ALL via Docker

No local Python. `python:3.11-slim` for tests (not 3.12 — pkg_resources/OTel issues).

```bash
# Tests + coverage gate (≥90% enforced)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim sh -c \
  "pip install -r requirements.txt -q && pytest --cov=src --cov-fail-under=90"

# Lint (ruff — rules F, E7, E9)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim sh -c \
  "pip install ruff -q && ruff check src/ tests/"

# Build image
docker build -t aigent-squad:latest .

# Local stack
./setup-local.sh     # then: curl http://localhost:8000/health
docker compose up    # alternative

# Smoke test
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{"user_input": "How many EC2 instances are running?", "user_id": "u1", "session_id": "s1"}'
```

> Tests require SSH key for `staffops-otel-libs` (private dep). In CI a deploy key is used.
> Locally, stub the dep: grep it out of requirements, create a minimal `__init__.py` stub.

---

## Repository layout

```
src/
  core/
    agent_base.py       ← Abstract Agent class (implement async process_request)
    generic_agent.py    ← Config-driven agent (all 5 specialists use this)
    bedrock.py          ← Bedrock client: circuit breaker, retry, token counting
    classifier.py       ← LLM-based routing + keyword fallback
    adapters.py         ← Boto3, Kubernetes, HTTP, Athena, MCP data collectors
    skills.py           ← Lazy skill registry (SKILL.md frontmatter matching)
    registry.py         ← Auto-discovers agents from agents/ at startup
    state_store.py      ← DynamoDB ChatStorage (per-agent history, 24h TTL)
    cache.py            ← Redis datasource cache (NOT LLM responses)
    config.py           ← Pydantic BaseSettings (env vars, BEDROCK_MODEL_ID SSOT)
    auth.py             ← X-Internal-Token header check (fail-closed)
    metrics.py          ← OTel + custom aigent.* metrics
    investigation.py    ← Evidence model, RCA logic
  supervisor/
    agent.py            ← SupervisorAgent: routing, fan-out, history, investigation
    server.py           ← FastAPI: /query, /alerts/incoming, /v1/chat/completions, health
    synthesizer.py      ← Merges N agent responses into one
    investigation.py    ← RCA orchestration (Phase 1: single-round)
  agents/
    aws/agent.py · kubernetes/agent.py · finops/agent.py
    devops/agent.py · observability/agent.py
  api/
    server.py           ← Slack entrypoint (rewrite planned in Phase 3)

agents/                 ← Config-driven agent definitions (agent.yaml + prompt.md)
  aws/ · kubernetes/ · finops/ · devops/ · observability/

skills/                 ← Lazy-loaded markdown knowledge (SKILL.md with YAML frontmatter)

specs/                  ← Spec-driven planning (source of truth)
  AUDIT.md              ← Real state + blockers
  ROADMAP.md            ← Phased plan
  ADR-001-bedrock-direct-vs-strands.md

steering/               ← project.md, efficiency-cost.md, licensing-clean-room.md, milestone-criteria.md

infra/terraform/              ← IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP
docs/                   ← ARCHITECTURE.md, SECURITY.md, METRICS.md, HOW-TO-NEW-AGENT.md, ...
```

---

## Agent config schema

Each agent in `agents/<name>/`:

**`agent.yaml`**:
```yaml
name: aws
description: "AWS infrastructure specialist. Read-only."
routing_keywords: [ec2, instance, s3, bucket, rds, iam, vpc]
datasources:
  - type: boto3
    services: [ec2, s3, rds, iam]
  - type: mcp
    name: aws-mcp
    url: "http://aws-mcp-server:8080"
    tools: [describe_instances]        # fail-closed allowlist
skills: [ec2-best-practices]           # allowed global skills
cache:
  ttl: 300
  namespace: aws
model:
  temperature: 0.1
read_only: true
required_env: [AWS_REGION]
enabled: true
```

**`prompt.md`** — system prompt (markdown, English only).

To add a new agent: create `agents/<name>/agent.yaml` + `agents/<name>/prompt.md`.
No code changes needed — `AgentRegistry` auto-discovers at startup.

---

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `BEDROCK_MODEL_ID` | ✅ | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Model (inference profile) |
| `AWS_REGION` | ✅ | `us-east-1` | Bedrock, DynamoDB, AWS APIs |
| `INTERNAL_API_TOKEN` | ✅ | — | Auth for /query + all non-health endpoints |
| `REDIS_HOST` | ✅ | — | Cache |
| `REDIS_PASSWORD` | ✅ | — | Cache auth |
| `REDIS_SSL` | | `true` | Disable in dev |
| `DYNAMODB_SESSIONS_TABLE` | | `agent-sessions` | Conversation history |
| `DYNAMODB_ENDPOINT` | | AWS | Set to `http://dynamodb-local:8000` for dev |
| `SLACK_BOT_TOKEN` | optional | — | Slack integration |
| `GITLAB_TOKEN` | optional | — | DevOps agent (read-only) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | optional | `http://otel-collector:4317` | Tracing |
| `POSTGRES_HOST/USER/PASSWORD/DB` | optional | — | Knowledge base (disabled by default) |

See `.env.example` for full template.

---

## Ports (local dev)

| Service | Port |
|---------|------|
| Supervisor | 8000 |
| MCP Server | 8006 |
| Redis | 6379 |
| PostgreSQL | 5432 |
| DynamoDB Local | 8100 |
| OTel Collector | 4317 |
| Prometheus | 9099 |
| Grafana | 3001 |

---

## Metrics (aigent.* namespace)

All metrics named `aigent.<domain>.<name>`. Key ones:

| Metric | Labels | Purpose |
|--------|--------|---------|
| `aigent.requests.total` | agent_id | Per-agent request count |
| `aigent.errors.total` | agent_id, error_type | Failures |
| `aigent.tokens.total` | agent_id, model, direction | Token consumption |
| `aigent.cost.estimated` | agent_id | USD cost per agent |
| `aigent.cache.hits/misses` | agent_id | Cache effectiveness |
| `aigent.fanout.calls` | — | Multi-agent dispatch count |
| `aigent.investigation.duration` | — | RCA latency |
| `aigent.circuit_breaker.transitions` | — | Bedrock resilience events |

**No high-cardinality labels** — never add `user_id`, `trace_id`, or raw error text as labels.
Every new feature must emit ≥1 custom metric, defined in `src/core/metrics.py` and documented
in `docs/METRICS.md`.

---

## Phase status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Stabilization (fix blockers, unify architecture, harden security) | ✅ Complete |
| 1 | Quality + docs (test suite ≥90%, per-agent cost metrics, README) | 🔄 In progress |
| 2 | Deploy (Helm chart, EKS/IRSA, per-env manifests) | 🔴 Blocked on Phase 1 |
| 3+ | Features (Slack v2, RAG, proactive agents, multi-round RCA) | ⏳ After real deploy |

Current work order (Phase 0): complete → Phase 1 in progress.

---

## Prohibitions

```
❌ Redefine agent class in server.py (use agent.py)
❌ Native hash() in cache key (use hashlib.sha256)
❌ Cache LLM responses (leaks across users, breaks multi-turn)
❌ Unauthenticated endpoints (except /healthz, /ready, /health)
❌ Container as root (USER 65534)
❌ Hardcoded URLs/endpoints that have env vars
❌ Suggest write/mutation commands (system is read-only TODAY)
❌ datetime.utcnow() — use datetime.now(timezone.utc)
❌ Reintroduce LangGraph (removed; Bedrock-direct by design)
❌ Copy third-party code (learn patterns, implement from scratch)
❌ Push to main (work on dev)
❌ Commit without explicit approval
```

---

## Workflow rules

- **Spec-driven**: update `specs/<NN>/design.md` BEFORE implementing
- **Tests ship with code**: ≥90% coverage, Docker-measured, independent author
- **Metrics ship with code**: new feature = new `aigent.*` metric in `metrics.py` + `docs/METRICS.md`
- **Docs ship with code**: update relevant `docs/` files in the same change
- **Mark tasks**: update `tasks.md` with completion dates; explicitly defer unfinished items
- **Conventional commits**: `feat/fix/docs/test/refactor/chore(scope): description`
- **Stage explicitly**: `git add <specific files>` — never `git add .`
- **Cost discipline**: truncate adapter output before prompt, lazy-inject skills, cap history to N messages

---

## Key references

| Need to... | Go to |
|------------|-------|
| Understand real state / blockers | `specs/AUDIT.md` |
| See phased plan | `specs/ROADMAP.md` |
| Work a feature | `specs/<NN-feature>/requirements.md` + `design.md` + `tasks.md` |
| Add a new agent | `docs/HOW-TO-NEW-AGENT.md` |
| Architecture overview | `docs/ARCHITECTURE.md` |
| Security / read-only policy | `docs/SECURITY.md`, `docs/READ_ONLY_POLICY.md` |
| Observability pipeline | `docs/OBSERVABILITY.md` |
| All custom metrics | `docs/METRICS.md` |
| Bedrock design decision | `specs/ADR-001-bedrock-direct-vs-strands.md` |
| Deploy to K8s | `helm-charts/charts/aigent-squad` (sibling repo) |
