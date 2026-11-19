# Quick Start - Local Development

## 🚀 Setup in 3 Steps

### 1. Prerequisites

- Docker + Docker Compose, `make`
- AWS credentials in `~/.aws` (mounted read-only into the supervisor) with Bedrock
  access — if you use SSO, run `aws sso login` first; an expired session makes the
  supervisor fail its readiness check
- No `.env` is needed: `docker-compose.yaml` ships dev-safe defaults
  (`INTERNAL_API_TOKEN=dev-secret-token`, `REDIS_PASSWORD=changeme`,
  `GUARDRAIL_ENABLED=false`). Create a `.env` (template: `.env.example`) only to
  override something, e.g. `GITLAB_TOKEN` for the devops agent.

> **Guardrail note**: the application default is `GUARDRAIL_ENABLED=true`
> (fail-closed), but local compose sets it to `false`. To exercise the guardrail
> locally, provision one via `infra/terraform/guardrail/` and set
> `GUARDRAIL_ENABLED=true` + `GUARDRAIL_ID`/`GUARDRAIL_VERSION` in `.env`.

### 2. Start the stack

```bash
make up      # starts everything, waits for gateway /ready (60s max)
make smoke   # health + 1 real query (Bedrock round-trip) + /v1/models
```

This brings up the two-tier stack: **gateway** (public, :8000) → **supervisor**
(backend, internal-only, in-process specialists) + Redis, DynamoDB Local,
PostgreSQL (KB), MCP server, the observability stack (OTel Collector,
Prometheus, Tempo, Grafana), and LibreChat + MongoDB. Stop it with `make down`
(`make down V=1` also drops volumes).

### 3. Test

```bash
# Health (gateway)
curl http://localhost:8000/ready

# Query via the gateway (classifier auto-routes)
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{
    "user_input": "How many EC2 instances are running?",
    "user_id": "test",
    "session_id": "test123"
  }'

# OpenAI-compatible bridge (LibreChat or any OpenAI client)
curl -H 'X-Internal-Token: dev-secret-token' http://localhost:8000/v1/models
```

---

## 📊 Services

| Service | Port | URL | Health |
|---------|------|-----|--------|
| Gateway (public front door) | 8000 | http://localhost:8000 | `/healthz`, `/ready` |
| Supervisor (backend-only) | — (internal 8001, not published) | reachable only from the gateway | `/healthz`, `/ready` |
| MCP Server | 8006 | http://localhost:8006 | `/health` |
| Redis | 6379 | localhost:6379 | — |
| DynamoDB Local | 8100 | http://localhost:8100 | — |
| PostgreSQL (KB) | 5432 | localhost:5432 | — |
| OTel Collector | 4317 | — | — |
| Prometheus | 9099 | http://localhost:9099 | — |
| Grafana | 3001 | http://localhost:3001 | — |
| LibreChat | 3080 | http://localhost:3080 | `/health` |
| MongoDB (LibreChat storage) | — (internal) | — | — |

> LibreChat points at the remote devops-core squad by default, not at the local
> gateway; see `docs/LIBRECHAT.md` and the comment in `docker-compose.yaml` to
> point it at `http://gateway:8000/v1`.

> The 6 specialists (aws, kubernetes, finops, devops, observability, security) run
> **in-process inside the supervisor** — they have no ports. They are defined by
> config in `agents/<name>/` (`agent.yaml` + `prompt.md`).

---

## 🔧 Useful Commands

### Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f supervisor
docker compose logs -f gateway

# JSON logs formatted
docker compose logs -f supervisor | jq .
```

### Restart / Rebuild
```bash
# After changing an agent prompt or agent.yaml (config-only, no rebuild)
docker compose restart supervisor

# After changing code
docker compose up -d --build supervisor gateway

# Stop everything / reset volumes
docker compose down
docker compose down -v
```

### Tests + lint (all via Docker — no local Python)
```bash
make test                              # full suite + 90% gate
make test-one FILE=tests/test_x.py    # single file
make lint                              # ruff, CI-verbatim scope
make typecheck                         # mypy gate (blocks the CI test job)
```

> `make test` always replaces the `otel-helper` dependency with a no-op stub
> (the test container has no git) and warns loudly. "Passes locally" ≠ "passes
> CI" — confirm with `gh run list` after pushing.

### Debug
```bash
# Enter container
docker compose exec supervisor sh

# View environment variables
docker compose exec supervisor env | grep BEDROCK

# Test Redis
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping

# Test DynamoDB
aws dynamodb list-tables --endpoint-url http://localhost:8100
```

---

## 🐛 Troubleshooting

### Supervisor doesn't start
```bash
docker compose logs supervisor

# Common cause: invalid agent.yaml (Pydantic fails fast at startup) —
# check the agents/<name>/agent.yaml schema (see AGENTS.md).

# Supervisor readiness detail (port 8001 is internal; the image has no curl)
docker compose exec supervisor python -c "import http.client as h;c=h.HTTPConnection('127.0.0.1',8001);c.request('GET','/ready');print(c.getresponse().read().decode())"

# Expired AWS SSO session? (Bedrock/DynamoDB clients fail)
aws sso login

# Check if the gateway port is busy
lsof -i :8000
```

### 401 on every request
The gateway is fail-closed: `INTERNAL_API_TOKEN` must be set and sent as
`X-Internal-Token`. The supervisor additionally requires
`X-Supervisor-Token` (`SUPERVISOR_INTERNAL_TOKEN`) — only the gateway sends it.

### 403 on legitimate queries
Spec-14 defense is fail-closed: a Bedrock Guardrail block, InputScanner
detection, or output-filter match returns 403. Local compose already sets
`GUARDRAIL_ENABLED=false`; a 403 locally means an env override (check `.env`)
or an InputScanner/output-filter block.

### AWS credentials error
```bash
cat ~/.aws/credentials
aws sts get-caller-identity
docker compose exec supervisor ls -la /home/nonroot/.aws/ 2>/dev/null || \
  docker compose exec supervisor env | grep AWS
```

> `BEDROCK_MODEL_ID` must be an **inference profile** (`us.` prefix) — the raw
> model id fails with "on-demand throughput isn't supported".

### DynamoDB table error
```bash
aws dynamodb create-table \
  --table-name agent-sessions \
  --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
  --key-schema \
      AttributeName=pk,KeyType=HASH \
      AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --endpoint-url http://localhost:8100
```

---

## 🔄 Development Workflow

### 1. Modify an agent (config-only — no code)
```bash
vim agents/aws/prompt.md          # or agents/aws/agent.yaml
docker compose restart supervisor
```

### 2. Add a NEW agent (zero code)
```bash
mkdir agents/<name>
vim agents/<name>/agent.yaml      # name, description, domain, capabilities, routing_keywords, datasources
vim agents/<name>/prompt.md
docker compose restart supervisor # auto-discovered by AgentRegistry
```
See `docs/HOW-TO-NEW-AGENT.md`.

### 3. Test conversation history (multi-turn)
```bash
# First message
curl -X POST http://localhost:8000/query \
  -H 'X-Internal-Token: dev-secret-token' -H 'Content-Type: application/json' \
  -d '{"user_input":"Show EC2","user_id":"test","session_id":"abc123"}'

# Follow-up (classifier keeps the aws agent, history comes from DynamoDB)
curl -X POST http://localhost:8000/query \
  -H 'X-Internal-Token: dev-secret-token' -H 'Content-Type: application/json' \
  -d '{"user_input":"How many in us-east-1?","user_id":"test","session_id":"abc123"}'
```

---

## 🔌 Integrations

### LibreChat (OpenAI-compatible bridge)
The gateway speaks OpenAI `/v1` — point LibreChat at
`http://localhost:8000/v1` with the `X-Internal-Token` header. Models:
`aigent-squad` (auto-route) or `aigent-squad-<agent>` (force a specialist).
See `docs/LIBRECHAT.md`.

### MCP Server
```bash
curl http://localhost:8006/health

curl -X POST http://localhost:8006/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "How many EC2 instances?", "user_id": "test"}'
```
See `docs/MCP_INTEGRATION.md`.

### Alertmanager → RCA
`POST /alerts/incoming` (Alertmanager v2 payload) triggers an investigation;
optional Slack post-back via `SLACK_WEBHOOK_URL`. See `docs/ALERTING.md`.

---

## 📝 Notes

- **DynamoDB Local**: in-memory data, lost on restart
- **Redis**: dev requires `REDIS_PASSWORD`; `REDIS_SSL=false` in dev only
- **AWS Credentials**: mounted read-only from `~/.aws` (dev only — prod uses IRSA)
- **OpenTelemetry**: OTLP export to the local collector; traces in Tempo/Grafana
