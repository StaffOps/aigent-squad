# staffops-aigent-squad — Agent Guide

> Canonical, tool-agnostic guide for any AI coding assistant (Claude Code,
> Cursor, Copilot, Aider, …). Tool-specific files (`CLAUDE.md`) just point here.
> Detailed rules live in [`steering/`](steering/); plans in [`specs/`](specs/).

Multi-agent AI platform for AWS/Kubernetes operations: **edge gateway + supervisor
(1 process, 6 in-process specialists) + MCP server**. Config-driven, Bedrock-direct,
read-only by default, defense-in-depth anti-prompt-injection (spec 14).

> **Status**: `0.4.0` released 2026-07-15 (tag `v0.4.0`, GitHub Release, image
> `karlipegomes/aigent-squad:0.4.0` on Docker Hub — spec 34's `RELEASE.md` executed for
> real, Phases 0-2). Specs 11 (model tiering), 14 (security L1–L6, all findings closed),
> 35 (quality eval harness, complete), and 36 (dev loop) shipped. The devops-core cluster
> runs the same code via a separate path (Harbor `labs/aigent-squad:0.3.0-dev`, digest
> `e94a901`, homologated live 2026-07-15) — the two publish paths were never reconciled,
> tracked as `specs/BACKLOG.md` B-25. Work on branch `dev`. Never push to `main` — go
> through a PR (see `RELEASE.md` for the release flow specifically).
> Real status per spec in `specs/ROADMAP.md`; session state in `HANDOFF.md`.
> `specs/AUDIT.md` is the historical 2026-05-30 audit (findings fixed — kept as record).

---

## Architecture

```
User (LibreChat /v1 · HTTP /query · Alertmanager · MCP :8006)
         │
         ▼
  Gateway :8000 (public front door — spec 31)
  ├── Edge auth (INTERNAL_API_TOKEN / GATEWAY_API_KEYS / Authorization: Bearer, fail-closed; per-consumer GATEWAY_KEY_AGENT_MAP scope)
  ├── AdmissionGuard: per-user rate + global daily budget (Redis, fail-open)
  ├── WorkerPool backpressure (semaphore 20, 503 + Retry-After)
  └── OpenAI /v1 shaping (spec 29) · /jobs/{id}/cancel
         │  POST /internal/process  (SUPERVISOR_INTERNAL_TOKEN, fail-closed)
         ▼
  Supervisor :8001 (backend-only)
  ├── InputScanner L2 (NFKC/homoglyph/zero-width, fail-closed — spec 14)
  ├── Classifier (Bedrock Haiku) → routes to 1–3 agents
  ├── Fan-out (parallel) → synthesizer merges N responses
  ├── RCA investigation → agents collect evidence in parallel
  ├── Guardrail: ingress INPUT + agentic tool-args + tool-result + OUTPUT (fail-closed 403) + canary + guardContent input-tagging
  └── DynamoDB (history, 24h TTL, per-agent isolated)
         │
  ┌──────┴──────────────────────────────┐
  │ In-process specialists (no ports)   │
  │  aws · kubernetes · finops          │
  │  devops · observability · security  │
  └─────────────────────────────────────┘
         │
  Redis (datasource cache 1–60min TTL · rate/budget · jobs)
  Bedrock (tiered: Haiku classifier / Sonnet agents+synthesis, prompt caching)
  PostgreSQL + pgvector (knowledge base, optional)
```

### Key invariants — do not violate

1. **Classifier always routes** — routing is a Bedrock call, never manual `if/else`
2. **Per-agent isolated history** — DynamoDB `pk=user#session`, `sk=agent#timestamp`
3. **GenericAgent pattern** — all 6 specialists inherit `Agent`, implement `async process_request(...)`
4. **Model tiering from config, never hardcoded** (spec 11) — `src/core/model_tier.py`
   resolves role→model: `BEDROCK_CLASSIFIER_MODEL_ID` (Haiku) / `BEDROCK_MODEL_ID`
   (agents) / `BEDROCK_SYNTHESIS_MODEL_ID`; misconfig fails loudly at startup
5. **Read-only posture** — system prompt + IAM deny + K8s RBAC + response templates; for
   **agentic tool-calling (spec 37)** it holds via a positive fail-closed tool **allowlist** +
   the MCP server's own ServiceAccount RBAC + guardrail on tool args+results (proven by the MCP
   SA-RBAC audit gate, `scripts/mcp_rbac_audit.py`)
6. **Fail-open for availability, fail-closed for security** — Redis/DynamoDB loss =
   service continues (empty history/cache miss); rate/budget guards fail-open. BUT
   security layers (Guardrail, InputScanner, output filter — spec 14) are
   **fail-closed**: block or unavailable → 403, never bypass. **Exception: canary
   (L5)** is redact-and-continue, not fail-closed (spec 14 F-005, 2026-07-13,
   deliberate decision) — a detected leak is audited + the token is redacted
   from the response, which still reaches the user; it does not 403. Live
   testing found a real false-positive rate on ordinary benign answers (models
   echo the canary as a self-invented "session ID" footer, no injection
   involved), and the token is single-use/worthless once redacted, so denying
   an otherwise-legitimate answer cost more than it protected
7. **Deterministic cache keys** — `hashlib.sha256()`, never native `hash()`
8. **No business logic in `server.py`/gateway** — transport + auth only; the gateway
   holds NO orchestration and NEVER evaluates the guardrail (supervisor does)
9. **Single response contract** — `{role, content, timestamp, agent_id}`
10. **Two-tier trust boundary** — supervisor `/internal/*` accepts only the gateway
    (`SUPERVISOR_INTERNAL_TOKEN`, distinct secret, + NetworkPolicy); public routes
    live exclusively on the gateway
11. **Agentic tool-calling is config-only** (spec 37) — the LLM selects read-only tools+args via
    the Bedrock **Converse** loop (`src/core/agentic_loop.py`, bounded steps/tokens/time); a new
    MCP server = URL + read-only allowlist, **zero code**. The gateway accepts any model id
    (unknown → auto-route) and streams the loop's steps (🔧 tool call / 📦 result).
12. **Calibrated honesty + accuracy discipline** — agents separate verified (tool-backed) facts from
    inferred ones, never fabricate an unretrieved value/state, and end with a confidence + unverified
    list (`<calibrated_honesty>`, all agents); observability DISCOVERS metric names/labels before
    querying (canonical OTel names, `service`/`job` not `app`). Regression-guarded by the eval harness
    (`scripts/eval_squad.py` + `evals/golden_queries.yaml`). Loop budgets: 8 steps / 60s / 150K tokens.

---

## Build, test, lint — ALL via Docker, ALL via make (spec 36)

No local Python. `python:3.11-slim` for tests (not 3.12 — pkg_resources/OTel issues).
The **Makefile is the canonical command surface** — CI runs the same targets.

```bash
make up          # local two-tier stack + wait for gateway /ready
make smoke       # health + 1 real query + /v1/models
make test        # full suite + 90% gate via Docker (auto-stubs the private otel dep)
make test-one FILE=tests/test_x.py
make lint        # ruff, CI-verbatim scope
make down        # stop (V=1 drops volumes)

# Build image
docker build -t aigent-squad:latest .

# Legacy wrapper (delegates to make up + smoke)
./setup-local.sh
docker compose up    # alternative

# Smoke test
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{"user_input": "How many EC2 instances are running?", "user_id": "u1", "session_id": "s1"}'
```

> Tests require the private `staffops-otel-libs` dep. In CI a deploy key is used.
> Locally, `make test` handles it automatically (`scripts/test-local.sh` generates a
> no-op stub when the repo is unreachable and prints a loud warning).
>
> **Pre-push gate (mandatory).** CI runs `lint` → `test` and STOPS at the first
> failure, so a lint error hides test results. Before every push, run the CI
> lint command verbatim on the FULL scope — `ruff check src/ tests/` (not just
> the files you touched; `--fix` auto-resolves F401) — then the tests. The local
> `otel_helper` stub masks both flaky-dep tests and the real coverage gate, so
> "passes locally" ≠ "passes CI": confirm with `gh run list` after pushing.
> Subagent-generated modules/tests commonly leave unused imports — always lint them.

---

## Playbook — common failure modes (spec 36)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `403` on any query | Fail-closed security (spec 14): guardrail block, InputScanner, or `GUARDRAIL_ENABLED=true` with no `GUARDRAIL_ID` | Local compose defaults guardrail OFF — check env overrides; prod: this is working as designed |
| `401` on `/query` or `/v1/*` | Edge token mismatch | `X-Internal-Token` (or `X-API-Key` / `Authorization: Bearer`) must match `INTERNAL_API_TOKEN` or a `GATEWAY_API_KEYS` entry (NOT `SUPERVISOR_INTERNAL_TOKEN` — that's the gateway→supervisor link only) |
| `503 backend_unavailable` | Supervisor down/unreachable | `docker compose logs supervisor` — usual cause: invalid `agent.yaml` (Pydantic fails at startup) |
| `503 service_overloaded` | WorkerPool full (backpressure) | Self-healing; persistent → raise `GATEWAY_MAX_CONCURRENT` |
| Empty history / no context | DynamoDB fail-open (by design) | Check `dynamodb-local` health; the query still answers |
| Passes locally, fails CI | Local run used the otel stub (masks deps/telemetry) | The `make test` warning says so; verdict = `gh run list -L 3` |
| Bedrock `on-demand throughput isn't supported` | Raw model id used | `BEDROCK_MODEL_ID` must be an inference profile (`us.` prefix) |

---

## Repository layout

```
src/
  gateway/              ← Edge tier (spec 31) — public :8000
    main.py             ← FastAPI front door: /query, /v1/*, /jobs/{id}/cancel, health
    worker_pool.py      ← Local semaphore(20), PoolFullError→503, cancel via Redis poll
    supervisor_client.py← httpx client + preflight to the supervisor backend
    auth.py             ← Edge auth (INTERNAL_API_TOKEN / GATEWAY_API_KEYS, fail-closed)
  core/
    generic_agent.py    ← Config-driven agent (all 6 specialists use this)
    bedrock.py          ← Bedrock chokepoint: guardrail, retry, tokens, prompt caching
    model_tier.py       ← Role→model resolver + per-model pricing (spec 11)
    token_budget.py     ← Per-session token budget (hard cap, spec 11)
    classifier.py       ← LLM-based routing (Haiku) + keyword fallback
    guardrail.py        ← Bedrock Guardrail client (L1, fail-closed — spec 14)
    input_scanner.py    ← L2 pre-LLM normalization + heuristics (spec 14)
    output_filter.py    ← L4 PII/secret/canary scan on responses (spec 14)
    canary.py           ← L5 exfiltration canary tokens (spec 14)
    rate_limiter.py     ← AdmissionGuard: rate + daily budget, Lua atomic, fail-open
    circuit_breaker.py  ← Bedrock circuit breaker
    adapters.py         ← Boto3, Kubernetes, HTTP, Athena, MCP data collectors (cached)
    skills.py           ← Lazy skill registry (SKILL.md frontmatter matching)
    registry.py         ← Auto-discovers agents from agents/ at startup
    state_store.py      ← DynamoDB ChatStorage (per-agent history, 24h TTL)
    cache.py            ← Redis datasource cache (NOT LLM responses)
    config.py           ← Pydantic BaseSettings (env vars SSOT)
    auth.py / internal_auth.py ← edge token / supervisor-internal token (fail-closed)
    health.py           ← DependencyChecker for /ready probes
    metrics.py          ← OTel + custom aigent.* metrics
    investigation.py    ← Evidence model, RCA logic
    triage.py           ← trivial-vs-investigate decision
    kb/                 ← Incident memory (spec 21): extractor, enricher, pgvector store, RAG
  supervisor/
    agent.py            ← SupervisorAgent: routing, fan-out, history, investigation
    server.py           ← FastAPI backend :8001: /internal/process, /internal/agents,
                          /alerts/incoming, /kb/*, health (public /query & /v1 moved to gateway)
    synthesizer.py      ← Merges N agent responses into one
    openai_compat.py    ← OpenAI /v1 shaping (imported by the gateway)
    investigation.py    ← RCA orchestration (Phase 1: single-round + alert ingestion)
  api/
    server.py           ← Slack entrypoint (rewrite planned in Phase 3)

agents/                 ← Config-driven agent definitions (agent.yaml + prompt.md)
  aws/ · kubernetes/ · finops/ · devops/ · observability/ · security/

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
| `BEDROCK_MODEL_ID` | ✅ | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Agent/synthesis model (inference profile) |
| `BEDROCK_CLASSIFIER_MODEL_ID` | | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Classifier model (Haiku tiering, spec 11) |
| `AWS_REGION` | ✅ | `us-east-1` | Bedrock, DynamoDB, AWS APIs |
| `INTERNAL_API_TOKEN` | ✅ | — | Edge auth for /query + /v1 (gateway) |
| `SUPERVISOR_INTERNAL_TOKEN` | ✅ | — | Gateway→supervisor `/internal/*` link (distinct secret, fail-closed) |
| `GUARDRAIL_ENABLED` | | `true` | Spec-14 Bedrock Guardrail (fail-closed; needs `GUARDRAIL_ID`) |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | when enabled | — / `DRAFT` | From `infra/terraform/guardrail/` outputs |
| `AIGENT_TRACE_STYLE` | | `think` | Streaming tool-trace wrapper: `think` (collapsible in LibreChat/Open WebUI) / `details` / `plain` / `off` |
| `SESSION_TOKEN_BUDGET` | | `2000000` | Per-session cumulative token cap (spec 11); raised from 200K for agentic loop cost (~30-60K/query) |
| `AIGENT_MAX_LOOP_DURATION_MS` / `AIGENT_MAX_LOOP_TOKENS` | | `120000` / `300000` | Agentic loop wall-clock + cumulative-token budgets (deep analyses; context accumulates across turns) |
| `AIGENT_CONTEXT_TRIM_ENABLED` / `AIGENT_CONTEXT_KEEP_LAST_N` | | `true` / `5` | Spec 40 context-trimming: keep last N tool-result turns verbatim, summarize older (bounds per-turn context) |
| `AIGENT_TIER_ROUTING_ENABLED` / `AIGENT_TIER_DEEP_ENABLED` | | `true` / `false` | Spec 38 model-tier pre-routing; `deep`(Opus) off by default → falls back to standard until Opus access confirmed |
| `AIGENT_TIER_CONFIDENCE_HIGH` / `BEDROCK_TIER_{FAST,STANDARD,DEEP}_MODEL_ID` | | `0.85` / haiku,sonnet,opus | Downshift threshold + tier→model map (spec 38) |
| `BEDROCK_READ_TIMEOUT_SECONDS` | | `120` | boto3 Bedrock read timeout (default 60s cut slow Converse → stream "terminated") |
| `GATEWAY_JOB/FIRST_BYTE/IDLE_STREAM_TIMEOUT_SECONDS` | | `150`/`90`/`35` | Gateway stream timeouts; must exceed the loop budget + Bedrock read timeout |
| `SELF_SERVICE_INSTRUCTION` / `CALIBRATED_HONESTY_INSTRUCTION` / `DECISIVENESS_INSTRUCTION` | | baked default | Env-overridable shared system-prompt instructions (no rebuild to tune) |

> **Deploy gotcha:** set numeric envs via `helm --set-string` — plain `--set` renders large ints as `2e+06` → pydantic int-parse crash on startup.
| `INPUT_SCANNER_ENABLED` / `OUTPUT_FILTER_ENABLED` / `CANARY_ENABLED` | | `true` | Spec-14 L2/L4/L5 toggles |
| `RATE_BUDGET_ENABLED` | | `true` | Gateway admission guards (rate + daily budget) |
| `GATEWAY_MAX_CONCURRENT` | | `20` | WorkerPool size (+ `GATEWAY_*_TIMEOUT_SECONDS`) |
| `REDIS_HOST` | ✅ | — | Cache + rate/budget + job lifecycle |
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
| Gateway (public) | 8000 |
| Supervisor (backend, `/internal/*`) | 8001 |
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
| `aigent.cache.hits/misses` | namespace | Datasource cache effectiveness (spec 30) |
| `aigent.gateway.pool_rejections/pool_depth/queue_wait` | — | Gateway backpressure (spec 31) |
| `aigent.rate_limit.blocks` | reason | Admission guard blocks (user/global) |
| `aigent.fanout.calls` | — | Multi-agent dispatch count |
| `aigent.investigation.duration` | — | RCA latency |
| `aigent.circuit_breaker.transitions` | — | Bedrock resilience events |

**No high-cardinality labels** — never add `user_id`, `trace_id`, or raw error text as labels.
Every new feature must emit ≥1 custom metric, defined in `src/core/metrics.py` and documented
in `docs/METRICS.md`.

---

## Phase status

Phase/spec status is **not duplicated here**. Authoritative per-spec status lives in
each spec's `requirements.md` frontmatter and the single canonical table in
[`specs/ROADMAP.md`](specs/ROADMAP.md) (CI-validated by `scripts/specs_status.py`).
Live session state + next steps: `HANDOFF.md`. Live items (findings/backlog/deferred):
[`specs/BACKLOG.md`](specs/BACKLOG.md).

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

> **Spec process** — lifecycle, status frontmatter (the SSOT), full-spec vs `bugfix.md`
> tiers, the verification-independence pipeline, the mandatory security-review rule, and
> numbering + language conventions — is defined once in
> [`specs/README.md`](specs/README.md). Don't restate it here. The bullets below are the
> repo's dev conventions.

- **Spec-driven**: update the spec's `design.md` BEFORE implementing (process: `specs/README.md`)
- **Tests ship with code**: ≥90% coverage, Docker-measured, independent author
- **Metrics ship with code**: new feature = new `aigent.*` metric in `metrics.py` + `docs/METRICS.md`
- **Docs ship with code**: update relevant `docs/` files in the same change.
  Opt-in enforcement: `make install-hooks` installs a pre-commit hook
  (`.githooks/pre-commit`) that blocks a commit touching `src/` or an
  agent's `agent.yaml`/`prompt.md` without a docs/spec file in the same
  commit (bypass per-commit: `git commit --no-verify`)
- **Mark tasks**: update `tasks.md` with completion dates; explicitly defer unfinished items
- **Conventional commits**: `feat/fix/docs/test/refactor/chore(scope): description`
- **Stage explicitly**: `git add <specific files>` — never `git add .`
- **Cost discipline**: truncate adapter output before prompt, lazy-inject skills, cap history to N messages

---

## Key references

| Need to... | Go to |
|------------|-------|
| Understand real state / blockers | historical audit (frozen): `specs/AUDIT.md`; **current** per-spec status: `specs/ROADMAP.md` canonical table + spec frontmatter |
| How the spec process works (lifecycle, status, tiers, review) | `specs/README.md` |
| See phased plan | `specs/ROADMAP.md` |
| Live items (findings `F-*`, product `B-*`, decisions `D-*`, deferred, dormant) | `specs/BACKLOG.md` |
| Long-term vision (maturity levels) | `specs/VISION.md` |
| Work a feature | `specs/<NN-feature>/requirements.md` + `design.md` + `tasks.md` |
| Add a new agent | `docs/HOW-TO-NEW-AGENT.md` |
| Architecture overview | `docs/ARCHITECTURE.md` |
| Security / read-only policy | `docs/SECURITY.md`, `docs/READ_ONLY_POLICY.md` |
| Observability pipeline | `docs/OBSERVABILITY.md` |
| All custom metrics | `docs/METRICS.md` |
| Product why / personas / success metrics | `docs/prd/aigent-squad.md` |
| Architecture decisions (ADR index) | `docs/architecture/decisions/README.md` |
| Bedrock design decision | `specs/ADR-001-bedrock-direct-vs-strands.md` (= ADR-0001) |
| Deploy to K8s | `helm-charts/charts/aigent-squad` (sibling repo) |
| Cut a release | `RELEASE.md` (spec 34) — 8-phase runbook, cross-repo |
