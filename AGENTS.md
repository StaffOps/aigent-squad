# staffops-aigent-squad — Agent Guide

> Canonical, tool-agnostic guide for any AI coding assistant (Claude Code,
> Cursor, Copilot, Aider, …). Tool-specific files (`CLAUDE.md`) just point here.
> Detailed rules live in [`steering/`](steering/); plans in [`specs/`](specs/).

Multi-agent AI platform for AWS/Kubernetes operations: **edge gateway + supervisor
(1 process, 6 in-process specialists) + MCP server**. Config-driven, framework-free,
LLM = Bedrock (default) or any OpenAI-compatible API (`LLM_PROVIDER`, spec 28),
read-only by default, defense-in-depth anti-prompt-injection (spec 14).

> **Status**: latest release **`v0.5.0`** (2026-08-18; GitHub Release + signed image
> `ghcr.io/staffops/aigent-squad:0.5.0` on GHCR). CI publishes to GHCR on merge to `main`.
> **`dev` since v0.5.0** (see `CHANGELOG.md` → `[Unreleased]`): spec 28 (Bedrock or any
> OpenAI-compatible LLM), spec 46 (portable profiles: pluggable history with **Redis as the
> default**, `${VAR}`-addressed datasources, `AGENT_<NAME>_ENABLED`), F-024 (history, rate
> limits and sessions scoped to the API-key consumer), L4 output filter + L5 canary on
> **streamed** answers, streamed answers saved to history, spec 47 Phase 1 (`POST /feedback`),
> KB retention + `KB_ENABLED`, gateway input-size caps + enforced job timeout, the repo now
> ships **example skills only** (`example-skills/`; real catalogs via `SKILLS_DIR`), and
> supply-chain/Scorecard work (CodeQL, least-privilege workflow tokens, signed release assets).
> The next release is a user decision and follows `RELEASE.md`.
> Per-spec status: `specs/ROADMAP.md`; session state + what is pending: `docs/handoff.md`.
> `specs/AUDIT.md` is the historical 2026-05-30 audit (findings fixed — kept as record).

---

## Architecture

```
User (LibreChat /v1 · HTTP /query · MCP :8006)          Alertmanager
         │                                                    │
         ▼                                                    │
  Gateway :8000 (public front door — spec 31)                 │
  ├── Edge auth (INTERNAL_API_TOKEN / GATEWAY_API_KEYS / Authorization: Bearer, fail-closed; per-consumer GATEWAY_KEY_AGENT_MAP scope;
  │   user ids forwarded as `<consumer>:<user_id>` so history/rate/sessions never cross keys — F-024)
  ├── Size caps (413, F-009) · AdmissionGuard: per-user rate + global daily budget (Redis, fail-open)
  ├── WorkerPool backpressure (semaphore 20, 503 + Retry-After) · job timeout (504)
  └── OpenAI /v1 shaping (spec 29) · /jobs/{id}/cancel · POST /feedback (spec 47)
         │  POST /internal/process[/stream]  (SUPERVISOR_INTERNAL_TOKEN, fail-closed)
         ▼                                                    ▼  POST /alerts/incoming (X-Internal-Token)
  Supervisor :8001 (backend; /alerts/incoming + /kb/* use the edge token)
  ├── InputScanner L2 (NFKC/homoglyph/zero-width, fail-closed — spec 14)
  ├── Classifier (LLM, Haiku by default) → routes to 1–3 agents; model tier per question (spec 38)
  ├── Fan-out (parallel) → synthesizer merges N responses
  ├── RCA investigation → agents collect evidence in parallel
  ├── Guardrail: ingress INPUT + agentic tool-args + tool-result + OUTPUT (fail-closed 403) + canary + guardContent input-tagging
  ├── Output: L5 canary → L4 output filter → quality check, on full and on streamed answers (stream_output_guard)
  └── History store (HISTORY_BACKEND: redis default · dynamodb on AWS · postgres; 24h, per-agent isolated)
         │
  ┌──────┴──────────────────────────────┐
  │ In-process specialists (no ports)   │
  │  aws · kubernetes · finops          │
  │  devops · observability · security  │
  └─────────────────────────────────────┘
         │
  Redis (datasource cache 1–60min TTL · rate/budget · jobs · sessions · feedback · history by default)
  LLM via LLMService (spec 28): Bedrock (default; tiered Haiku/Sonnet/Opus, prompt caching)
       or any OpenAI-compatible /chat/completions (LLM_PROVIDER=openai)
  PostgreSQL + pgvector (incident-memory KB, KB_ENABLED; optional history backend)
```

### Key invariants — do not violate

1. **Classifier always routes** — routing is a Bedrock call, never manual `if/else`
2. **Per-agent isolated history** — on every `HISTORY_BACKEND` (DynamoDB `pk=user#session`,
   `sk=agent#timestamp`; Redis/Postgres key the `(user, session)` pair + agent), 24 h retention,
   `user` = the consumer-scoped `<consumer>:<user_id>` from the gateway (F-024)
3. **GenericAgent pattern** — all 6 specialists inherit `Agent`, implement `async process_request(...)`
4. **Model tiering from config, never hardcoded** (spec 11) — `src/core/model_tier.py`
   resolves role→model: `BEDROCK_CLASSIFIER_MODEL_ID` (Haiku) / `BEDROCK_MODEL_ID`
   (agents) / `BEDROCK_SYNTHESIS_MODEL_ID`; provider-neutral `LLM_*_MODEL_ID` win when
   set (required under `LLM_PROVIDER=openai`, spec 28); misconfig fails loudly at startup.
   All LLM calls go through `LLMService` (`src/core/llm/`) — never call a provider SDK
   directly; resilience, guardrail and token/cost metrics live there once
5. **Read-only posture** — system prompt + IAM deny + K8s RBAC + response templates; for
   **agentic tool-calling (spec 37)** it holds via a positive fail-closed tool **allowlist** +
   the MCP server's own ServiceAccount RBAC + guardrail on tool args+results (proven by the MCP
   SA-RBAC audit gate, `scripts/mcp_rbac_audit.py`)
6. **Fail-open for availability, fail-closed for security** — history-store or cache loss =
   service continues (empty history/cache miss); rate/budget guards fail-open (the per-session
   token budget is the exception: Redis down → it refuses, `reason=store_unavailable`). BUT
   security layers (Guardrail, InputScanner, output filter — spec 14) are
   **fail-closed**: block or unavailable → 403, never bypass. **Exception: canary
   (L5)** is redact-and-continue, not fail-closed (spec 14 F-005, 2026-07-13,
   deliberate decision) — a detected leak is audited + the token is redacted
   from the response, which still reaches the user; it does not 403. Live
   testing found a real false-positive rate on ordinary benign answers (models
   echo the canary as a self-invented "session ID" footer, no injection
   involved), and the token is single-use/worthless once redacted, so denying
   an otherwise-legitimate answer cost more than it protected.
   **Exception 2: Bedrock Guardrail (L1) is OFF when `LLM_PROVIDER != bedrock`**
   (spec 28 Decision 10, ADR-0009, 2026-11-15, deliberate decision) — the guardrail
   is an AWS Bedrock API; every L1 point (ingress, per-stage INPUT/OUTPUT, tool
   args/results, Converse `guardrailConfig`/guardContent) becomes a no-op. Never
   silent: startup WARNING `llm_guardrail_l1_inactive`,
   `aigent.llm.guardrail_l1_active{provider}=0`, `/ready` `checks.llm.guardrail_l1`.
   L2–L6 run unchanged. Need L1 → stay on Bedrock
7. **Deterministic cache keys** — `hashlib.sha256()`, never native `hash()`
8. **No business logic in `server.py`/gateway** — transport + auth only; the gateway
   holds NO orchestration and NEVER evaluates the guardrail (supervisor does)
9. **Single response contract** — `{role, content, timestamp, agent_id}`
10. **Two-tier trust boundary** — supervisor `/internal/*` accepts only the gateway
    (`SUPERVISOR_INTERNAL_TOKEN`, distinct secret, + NetworkPolicy); user-facing query
    routes live exclusively on the gateway. The supervisor's only other routes are health,
    the Alertmanager webhook `/alerts/incoming` and the KB review `/kb/*`, both behind the
    edge token (`X-Internal-Token`)
11. **Agentic tool-calling is config-only** (spec 37) — the LLM selects read-only tools+args via
    the Bedrock **Converse** loop (`src/core/agentic_loop.py`, bounded steps/tokens/time); a new
    MCP server = URL + read-only allowlist, **zero code**. The gateway accepts any model id
    (unknown → auto-route) and streams the loop's steps (🔧 tool call / 📦 result).
12. **Calibrated honesty + accuracy discipline** — agents separate verified (tool-backed) facts from
    inferred ones, never fabricate an unretrieved value/state, and end with a confidence + unverified
    list (`<calibrated_honesty>`, all agents); observability DISCOVERS metric names/labels before
    querying (canonical OTel names, `service`/`job` not `app`). Regression-guarded by the eval harness
    (`scripts/eval_squad.py` + `evals/golden_queries.yaml`). Loop budgets (`src/core/agent_config.py`): 8 steps / 120 s / 300K tokens.

---

## Build, test, lint — ALL via Docker, ALL via make (spec 36)

No local Python. Tests, lint, typecheck and `specs-status` run in `python:3.11-slim`
(`scripts/test-local.sh`, `Dockerfile.test`); the runtime image is `python:3.11-alpine`
(`Dockerfile`); `make docs` uses `python:3.12-slim` (MkDocs only).
The **Makefile is the canonical command surface** — CI runs the same targets.

```bash
make up          # local two-tier stack + wait for gateway /ready
make smoke       # health + 1 real query + /v1/models
make test        # full suite + 90% gate via Docker (auto-stubs the otel-helper dep)
make test-one FILE=tests/test_x.py
make lint        # ruff, CI-verbatim scope
make typecheck   # mypy gate; CI blocks the test job on it (needs: [lint, typecheck])
make harness-score  # AI-agent harness maturity gate (L0-L4); floor = MIN_LEVEL (default 1)
make pin-check   # fail if any GitHub Action is not pinned to a 40-hex commit SHA (spec 45)
make specs-status   # spec status SSOT lint (frontmatter + ROADMAP table)
make docs        # strict MkDocs build into site/ (CI-verbatim)
make bandit / make scan   # SAST / Trivy image scan, both mirror CI
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

> Tests depend on `otel-helper` (git+https, public `StaffOps/otel-libs` repo since
> 2026-07-14; CI installs it with a plain `pip install`, no credentials). Locally,
> `make test` always replaces it with a no-op stub (the `python:3.11-slim` test
> container has no git) and prints a loud warning.
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
| `413 request_too_large` | Edge size cap (F-009) | Input over `GATEWAY_MAX_INPUT_CHARS` (forwarded turn), `/v1` over `GATEWAY_MAX_MESSAGES`, or body over `GATEWAY_MAX_BODY_BYTES` — shorten the input; raise the cap only together with the InputScanner limit |
| `504 gateway_timeout` | Job ran past a gateway timeout (first-byte / idle / `GATEWAY_JOB_TIMEOUT_SECONDS`) | `aigent.gateway.timeouts{kind}` says which; the job was cancelled |
| Empty history / no context | History store fail-open (by design) | `/ready` → `checks.history` names the backend (`HISTORY_BACKEND`, local default `redis`) and its state; the query still answers |
| Passes locally, fails CI | Local run used the otel stub (masks deps/telemetry) | The `make test` warning says so; verdict = `gh run list -L 3` |
| `make up` fails: `dependency failed to start: container …supervisor… is unhealthy` | Supervisor `/ready` failing — it gates only on `redis` and `agents` (history is informational) | `docker compose logs supervisor`; readiness detail: `docker compose exec supervisor python -c "import http.client as h;c=h.HTTPConnection('127.0.0.1',8001);c.request('GET','/ready');print(c.getresponse().read().decode())"`. Invalid `agent.yaml` → fix it; `${VAR}` outside an address field fails startup (spec 46) |
| Bedrock `on-demand throughput isn't supported` | Raw model id used | `BEDROCK_MODEL_ID` must be an inference profile (`us.` prefix) |

---

## Repository layout

```
src/
  gateway/              ← Edge tier (spec 31) — public :8000
    main.py             ← FastAPI front door: /query, /v1/*, /jobs/{id}/cancel, health;
                          size caps (413) + job/first-byte/idle timeouts (504)
    worker_pool.py      ← Local semaphore(20), PoolFullError→503, cancel via Redis poll
    supervisor_client.py← httpx client + preflight to the supervisor backend
    auth.py             ← Edge auth (INTERNAL_API_TOKEN / GATEWAY_API_KEYS, fail-closed)
    feedback.py         ← POST /feedback + response-id records (spec 47, consumer-scoped)
  core/
    generic_agent.py    ← Config-driven agent (all 6 specialists use this)
    agent_config.py     ← agent.yaml schema (Pydantic) + agentic-loop budgets (AIGENT_MAX_*)
    agent_env.py        ← Environment profile (spec 46): ${VAR} in address fields only,
                          AGENT_<NAME>_ENABLED, datasource URL checks
    agentic_loop.py     ← Bounded Converse tool-calling loop (spec 37) (+ _streaming variant)
    capability_gate.py  ← Per-agent capability tier enforcement (spec 43)
    llm/                ← LLM chokepoint (spec 28): service.py = LLMService common layer
                          (breaker, retry, guardrail, tokens/cost, budget); types.py =
                          LLMProvider Protocol; openai_provider.py; factory.py (LLM_PROVIDER)
    bedrock.py          ← Bedrock transport (BedrockClient(LLMService)): bodies, prompt caching
    model_tier.py       ← Role→model resolver + per-model pricing (spec 11)
    token_budget.py     ← Per-session token budget (hard cap, spec 11)
    classifier.py       ← LLM-based routing (Haiku) + keyword fallback
    guardrail.py        ← Bedrock Guardrail client (L1, fail-closed — spec 14)
    input_scanner.py    ← L2 pre-LLM normalization + heuristics (spec 14)
    output_filter.py    ← L4 PII/secret/canary scan on responses (spec 14)
    canary.py           ← L5 exfiltration canary tokens (spec 14)
    stream_output_guard.py ← L5 → L4 → quality check on streamed answers + trace lines
    response_quality.py ← Structural answer-quality guard (spec 35)
    rate_limiter.py     ← AdmissionGuard: rate + daily budget, Lua atomic, fail-open
    session_lock.py     ← Distributed per-session lock (spec 25)
    redis_keys.py       ← Hashed Redis key names, never raw ids (F-028)
    circuit_breaker.py  ← Bedrock circuit breaker
    adapters.py         ← Boto3, Kubernetes, HTTP, Athena, MCP data collectors (cached)
    skills.py           ← Lazy skill registry (SKILL.md frontmatter matching; dir = SKILLS_DIR)
    registry.py         ← Auto-discovers agents from agents/ at startup
    state_store.py      ← History store factory (HISTORY_BACKEND) + DynamoDB store
    history_stores.py   ← Redis (default) and Postgres history stores (spec 46)
    conversation.py     ← Shared message type, 24 h TTL, per-message content cap
    cache.py            ← Redis datasource cache (NOT LLM responses)
    config.py           ← Pydantic BaseSettings (env vars SSOT)
    auth.py / internal_auth.py ← edge token / supervisor-internal token (fail-closed)
    health.py           ← DependencyChecker for /ready probes
    metrics.py          ← OTel + custom aigent.* metrics
    investigation.py    ← Evidence model, RCA logic
    triage.py           ← trivial-vs-investigate decision
    kb/                 ← Incident memory (spec 21): extractor, enricher, pgvector store, RAG,
                          retention sweep (KB_ENABLED, F-029)
  supervisor/
    agent.py            ← SupervisorAgent: routing, fan-out, history, investigation
    server.py           ← FastAPI backend :8001: /internal/process[/stream], /internal/agents,
                          /alerts/incoming, /kb/*, health (public /query & /v1 moved to gateway)
    alert_handler.py    ← Alertmanager payload parse + dedup → investigation
    distillation.py     ← RCA → KB item distillation (fire-and-forget)
    synthesizer.py      ← Merges N agent responses into one
    openai_compat.py    ← OpenAI /v1 shaping (imported by the gateway)
    investigation.py    ← RCA orchestration (Phase 1: single-round + alert ingestion)
  api/
    server.py           ← Slack entrypoint (rewrite planned in Phase 3)

agents/                 ← Config-driven agent definitions (agent.yaml + prompt.md)
  aws/ · kubernetes/ · finops/ · devops/ · observability/ · security/

example-skills/         ← Generic example skills (SKILL.md + YAML frontmatter); real catalogs live
                          in each environment and load via SKILLS_DIR (repo = product + examples only)

specs/                  ← Spec-driven planning (source of truth)
  AUDIT.md              ← Historical 2026-05-30 audit (frozen)
  ROADMAP.md            ← Phased plan + canonical status table
  BACKLOG.md            ← Findings, product items, deferred register
  ADR-001-bedrock-direct-vs-strands.md

steering/               ← project.md, efficiency-cost.md, licensing-clean-room.md, milestone-criteria.md

infra/terraform/        ← IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP
docs/                   ← handoff.md, SECURITY.md, METRICS.md, HOW-TO-NEW-AGENT.md, ...;
                          docs/site/ = MkDocs site pages (architecture.md, reference/, ...)
openapi/                ← Generated OpenAPI specs (gateway.json, supervisor.json)
mcp-server/             ← The squad's own MCP server (:8006)
evals/ · tests/ · scripts/ ← Eval golden sets, test suite, gate/helper scripts
```

---

## Agent config schema

Each agent in `agents/<name>/`:

**`agent.yaml`**:
```yaml
name: aws
description: "AWS infrastructure specialist. Read-only."
domain: cloud-infrastructure                       # required
capabilities: [ec2_inventory, s3_management, iam_audit]  # required, list of strings
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

Settings SSOT: `src/core/config.py` (Pydantic). A few are read directly from the
environment (marked *env*): the loop budgets in `src/core/agent_config.py`, skills, alert
and KB knobs, and every `${VAR}` an `agent.yaml` references.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LLM_PROVIDER` | | `bedrock` | Global LLM provider: `bedrock` \| `openai` (OpenAI-compatible; **disables Bedrock Guardrail L1**, spec 28) |
| `LLM_MODEL_ID` / `LLM_CLASSIFIER_MODEL_ID` / `LLM_SYNTHESIS_MODEL_ID` | openai: `LLM_MODEL_ID` ✅ | — | Provider-neutral role models; bedrock falls back to `BEDROCK_*`, openai classifier/synthesis fall back to `LLM_MODEL_ID` |
| `OPENAI_BASE_URL` | openai ✅ | — | e.g. `https://api.openai.com/v1`, `http://ollama:11434/v1` |
| `OPENAI_API_KEY_FILE` / `OPENAI_API_KEY` | openai ✅ | — | Secret (file preferred, wins); never logged |
| `OPENAI_API_KEY_HEADER` / `OPENAI_MAX_TOKENS_FIELD` | | `Authorization` / `max_tokens` | `api-key` for Azure; `max_completion_tokens` for reasoning models |
| `LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M` | | — | Non-Bedrock pricing (USD); unset → cost metrics 0 + startup WARNING |
| `BEDROCK_MODEL_ID` | ✅ (bedrock) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Default model (inference profile) |
| `BEDROCK_CLASSIFIER_MODEL_ID` / `BEDROCK_AGENT_MODEL_ID` / `BEDROCK_SYNTHESIS_MODEL_ID` | | Haiku 4.5 / Sonnet 4.5 / Sonnet 4.5 | Role→model map (spec 11) |
| `BEDROCK_PROMPT_CACHE_ENABLED` | | `true` | Bedrock prompt caching |
| `BEDROCK_READ_TIMEOUT_SECONDS` / `BEDROCK_CONNECT_TIMEOUT_SECONDS` | | `120` / `10` | LLM client timeouts (the 60 s boto3 default cut slow Converse calls) |
| `BEDROCK_MAX_CONCURRENT` | | `20` | In-process LLM call semaphore |
| `AWS_REGION` | ✅ | `us-east-1` | Bedrock, DynamoDB, AWS APIs |
| `INTERNAL_API_TOKEN` | ✅ | — | Edge auth for /query, /v1, /feedback (gateway) and `/alerts/incoming`, `/kb/*` (supervisor) |
| `GATEWAY_API_KEYS` / `GATEWAY_KEY_AGENT_MAP` | | — | Extra per-client keys (csv) / `key=agent,…` default-agent scoping (spec 31 G-5); each key is its own consumer for history/rate/sessions (F-024) |
| `SUPERVISOR_INTERNAL_TOKEN` | ✅ | — | Gateway→supervisor `/internal/*` link (distinct secret, fail-closed) |
| `SUPERVISOR_URL` | | `http://localhost:8001` | Where the gateway forwards (supervisor Service URL) |
| `GUARDRAIL_ENABLED` | | `true` | Spec-14 Bedrock Guardrail (fail-closed; needs `GUARDRAIL_ID`); forced OFF when `LLM_PROVIDER != bedrock` |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | when enabled | — / `DRAFT` | From `infra/terraform/guardrail/` outputs |
| `INPUT_SCANNER_ENABLED` / `OUTPUT_FILTER_ENABLED` / `CANARY_ENABLED` / `RESPONSE_QUALITY_ENABLED` | | `true` | Spec-14 L2/L4/L5 + spec-35 quality guard toggles (apply to streamed answers too) |
| `CANARY_ESCALATION_THRESHOLD` | | `3` | Canary detections tolerated per session (redacted); above it the answer is blocked (fail-closed) |
| `AIGENT_TRACE_STYLE` | *env* | `think` | Streaming tool-trace wrapper: `think` (collapsible in LibreChat/Open WebUI) / `details` / `plain` / `off` |
| `SESSION_TOKEN_BUDGET` | | `2000000` | Per-session cumulative token cap (spec 11; fails closed when Redis is down) |
| `HISTORY_MAX_TOKENS` | | `8000` | History tokens included per LLM call |
| `AIGENT_MAX_TOOL_STEPS` / `AIGENT_MAX_LOOP_DURATION_MS` / `AIGENT_MAX_LOOP_TOKENS` | *env* | `8` / `120000` / `300000` | Agentic loop budgets (steps, wall-clock, cumulative tokens) |
| `AIGENT_MAX_TOOL_RESULT_CHARS` | *env* | `40000` | Per-tool-result truncation before it enters the prompt |
| `AIGENT_CONTEXT_TRIM_ENABLED` / `AIGENT_CONTEXT_KEEP_LAST_N` | *env* | `true` / `5` | Spec 40 context-trimming: keep last N tool-result turns verbatim, summarize older |
| `AIGENT_TIER_ROUTING_ENABLED` / `AIGENT_TIER_DEEP_ENABLED` | | `true` / `false` | Spec 38 model-tier pre-routing on every dispatch path; `deep` (Opus) is opt-in per deployment |
| `AIGENT_TIER_CONFIDENCE_HIGH` / `BEDROCK_TIER_{FAST,STANDARD,DEEP}_MODEL_ID` | | `0.85` / Haiku 4.5, Sonnet 4.5, Opus 4.5 | Downshift threshold + tier→model map (spec 38) |
| `SELF_SERVICE_INSTRUCTION` / `CALIBRATED_HONESTY_INSTRUCTION` / `DECISIVENESS_INSTRUCTION` | | baked default | Env-overridable shared system-prompt instructions (no rebuild to tune) |
| `GRAFANA_BASE_URL` | | (empty) | Grafana root URL for clickable dashboard links; set per deployment |
| `RATE_BUDGET_ENABLED` | | `true` | Gateway admission guards (rate + daily budget) |
| `RATE_LIMIT_PER_MINUTE` / `DAILY_BUDGET_USD` | | `60` / `50.0` | Per-user sliding window / global daily LLM spend cap |
| `GATEWAY_MAX_CONCURRENT` | | `20` | WorkerPool size |
| `GATEWAY_JOB/FIRST_BYTE/IDLE_STREAM_TIMEOUT_SECONDS` | | `150` / `90` / `35`, all > 0 | `JOB` = overall wall-clock cap (F-009): pooled paths → 504 `gateway_timeout`; `/v1` SSE → 504 before the stream opens, an SSE `gateway_timeout` error event after. First-byte/idle apply to pooled (non-streaming) jobs only; must exceed the loop budget + LLM read timeout (deep-tier deployments raise first-byte) |
| `GATEWAY_CANCEL_POLL_SECONDS` | | `0.5` | How often a running job polls Redis for cancellation |
| `GATEWAY_JOB_RECORD_TTL_SECONDS` | | `86400` | Expiry of the gateway's `job:<id>` status records in Redis (F-028); must be > 0 |
| `GATEWAY_MAX_INPUT_CHARS` / `GATEWAY_MAX_MESSAGES` / `GATEWAY_MAX_BODY_BYTES` | | `10000` / `200` / `1048576` | Edge size caps (F-009): forwarded user turn length (= InputScanner limit), `/v1` message count, declared `Content-Length`. Over → 413 `request_too_large`, before admission or any LLM work |
| `FEEDBACK_ENABLED` | | `true` | Spec 47 `POST /feedback` + response-id records (off → `/feedback` 404, nothing recorded) |
| `FEEDBACK_RESPONSE_TTL_SECONDS` / `FEEDBACK_RECORD_TTL_SECONDS` / `FEEDBACK_COMMENT_MAX_CHARS` | | `86400` / `604800` / `1000` | How long an answer can be rated / how long a rating is kept / comment cap |
| `REDIS_HOST` | ✅ | — | Cache + rate/budget + jobs + sessions (+ history with the default backend) |
| `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_SSL` | | `6379` / — / `true` | Redis connection (disable SSL in dev) |
| `HISTORY_BACKEND` | | `redis` | Conversation-history store (spec 46): `redis` \| `dynamodb` \| `postgres`; **AWS deployments set `dynamodb` explicitly**; 24h retention on every backend (fixed constant `HISTORY_TTL_SECONDS`, not configurable — see `docs/SECURITY.md` → Data classification and retention) |
| `HISTORY_MAX_MESSAGE_CHARS` | | `16000` | Per-message cap on stored history `content`, every backend (truncated with a marker; `0` = no cap). With `HISTORY_BACKEND=redis`, set Redis `maxmemory` + `maxmemory-policy volatile-ttl` (see `docs/site/architecture.md` → Data stores) |
| `DYNAMODB_SESSIONS_TABLE` | | `agent-sessions` | Conversation history (when `HISTORY_BACKEND=dynamodb`) |
| `DYNAMODB_ENDPOINT` / `DYNAMODB_LOCAL_STATIC_CREDENTIALS` | | AWS / `false` | DynamoDB Local for dev (`http://dynamodb-local:8000`) / static dummy credentials for it |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | | `postgres` / `5432` / `aigent` / `changeme` / `aigent_kb` | Postgres for the KB and for `HISTORY_BACKEND=postgres` |
| `POSTGRES_SSLMODE` | | — (asyncpg default) | TLS mode for the Postgres **history** store: `disable` \| `allow` \| `prefer` \| `require` \| `verify-ca` \| `verify-full`; empty = unset. `require` = encrypted, CA-verified only if a bundle is found; `verify-*` always verify and need a CA bundle (`POSTGRES_SSLROOTCERT`) |
| `POSTGRES_SSLROOTCERT` | with `verify-*` | — | Path to the PEM CA bundle that verifies the server (e.g. the cloud provider's bundle, mounted), or `system`; used by `require` and `verify-*`; fallback `PGSSLROOTCERT`, then `~/.postgresql/root.crt`; missing/invalid under `verify-*` → no connect, error logged, history fails open. Client certs/CRL via the standard `PGSSLCERT`/`PGSSLKEY`/`PGSSLCRL` |
| `KB_ENABLED` | | `true` | Incident-memory KB (spec 21). `false` = no distillation, no RAG, no KB Postgres connection, `/kb/*` → 503 (F-029) |
| `KB_REJECTED_RETENTION_DAYS` / `KB_ITEM_MAX_AGE_DAYS` | | `30` / unset | KB retention: rejected+superseded items deleted N days after their status change; active/pending items deleted N days after creation only when the max age is set |
| `KB_RETENTION_INTERVAL_SECONDS` / `KB_RETENTION_BATCH_SIZE` / `KB_RETENTION_MAX_BATCHES` | | `3600` / `500` / `10` | KB retention sweep: at most once per interval (at start + on KB reads/writes, no cron), bounded batch deletes, fail-open |
| `KB_MONTHLY_BUDGET_USD` / `BEDROCK_EMBED_MODEL_ID` | *env* | `50.0` / `amazon.titan-embed-text-v2:0` | KB distillation spend cap / embedding model |
| `AGENTS_DIR` / `SKILLS_DIR` | *env* | `agents` / `example-skills` | Where agent configs / the skills catalog load from (mount a real catalog via `SKILLS_DIR`) |
| `AIGENT_MAX_SKILLS_PER_TURN` | *env* | `3` | Skills injected per turn |
| `AGENT_<NAME>_ENABLED` | *env* | — | Per-environment agent on/off (spec 46), e.g. `AGENT_AWS_ENABLED=false` off-AWS |
| `K8S_MCP_URL` / `VM_MCP_URL` / `PROMETHEUS_URL` | *env* | — | Datasource addresses referenced by the kubernetes / observability `agent.yaml` (spec 46); unset → that datasource is dropped (degraded, agent still loads) |
| `GITLAB_URL` / `GITLAB_TOKEN` / `DOCS_PORTAL_URL` | *env* | — | Datasource address/credential referenced by the devops `agent.yaml` (read-only) |
| `AIGENT_ALLOW_LINK_LOCAL_DATASOURCES` | *env* | `false` | Allow datasource URLs on link-local / instance-metadata addresses (refused by default, spec 46) |
| `ALERT_DEDUP_TTL` / `RCA_MAX_AGENTS` | *env* | `3600` / `5` | Alertmanager dedup window (s) / max agents per investigation |
| `SLACK_WEBHOOK_URL` | *env* | — | Post alert RCAs to Slack (unset → no-op) |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | optional | — | Slack entrypoint (`src/api/server.py`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | optional | `http://otel-collector:4317` | Tracing/metrics export |

> **Deploy gotcha:** set numeric envs via `helm --set-string` — plain `--set` renders large ints as `2e+06` → pydantic int-parse crash on startup.

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
| `aigent.cost.estimated` | agent_id, model | USD cost per agent |
| `aigent.cache.hits/misses` | namespace | Datasource cache effectiveness (spec 30) |
| `aigent.gateway.pool_rejections/pool_depth/queue_wait` | — | Gateway backpressure (spec 31) |
| `aigent.gateway.input_rejections` / `aigent.gateway.timeouts` | reason / kind | 413 size-cap rejections, 504 timeouts (F-009) |
| `aigent.rate_limit.blocks` | reason | Admission guard blocks (user/global) |
| `aigent.fanout.calls` | — | Multi-agent dispatch count |
| `aigent.investigation.duration` | confidence | RCA latency |
| `aigent.circuit_breaker.transitions` | name, from, to | LLM/MCP resilience events |
| `aigent.llm.provider_errors` | provider, error_class | LLM transport errors (spec 28) |
| `aigent.llm.guardrail_l1_active` | provider | 0 = Bedrock Guardrail L1 inactive (spec 28) |
| `aigent.feedback.score` / `aigent.feedback.errors` | agent_id, score / reason | User ratings by bucket positive/neutral/negative; refusals and recording errors (spec 47) |
| `aigent.history.backend_errors` | backend, op | History-store errors swallowed fail-open (spec 46) |
| `aigent.stream.output_interventions` | agent_id, action | Streamed-answer L4/L5 guard: `blocked` / `trace_redacted` |
| `aigent.token_budget.exceeded` | reason | Session token budget refusals (`exhausted` / `store_unavailable`) |
| `aigent.kb.retention.deleted` / `aigent.kb.retention.errors` | rule / — | KB retention sweep deletes and failures (F-029) |

**No high-cardinality labels** — never add `user_id`, `session_id`, `trace_id`, a URL, or raw error text as labels (no exceptions; `aigent.token_budget.exceeded` uses a bounded `reason`).
Every new feature must emit ≥1 custom metric, defined in `src/core/metrics.py` and documented
in `docs/METRICS.md`.

---

## Phase status

Phase/spec status is **not duplicated here**. Authoritative per-spec status lives in
each spec's `requirements.md` frontmatter and the single canonical table in
[`specs/ROADMAP.md`](specs/ROADMAP.md) (CI-validated by `scripts/specs_status.py`).
Live session state + next steps: `docs/handoff.md`. Live items (findings/backlog/deferred):
[`specs/BACKLOG.md`](specs/BACKLOG.md).

---

## Prohibitions

```
❌ Redefine agent class in server.py (use agent.py)
❌ Native hash() in cache key (use hashlib.sha256)
❌ Cache LLM responses (leaks across users, breaks multi-turn)
❌ Unauthenticated endpoints (except /healthz, /ready, /health)
❌ Container as root (image runs as non-root `appuser`, uid 10001)
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
- **Typecheck gate**: `make typecheck` must pass. CI blocks the test job on it
  (`needs: [lint, typecheck]`), so a type error stops the pipeline before tests run.
  Fix types rather than silencing them — a `# type: ignore[code]` is acceptable only for a
  missing third-party stub or something the type system genuinely cannot express, and it
  MUST carry a comment saying which. Do NOT add `[[tool.mypy.overrides]]` per-module
  exclusions to make the count drop; that converts a gate into decoration. Note the gate is
  not full `strict` — `warn_return_any` is on and ~184 missing-annotation errors remain
  outside the enabled checks, which is registered debt, not a clean bill of health.
- **Order-independence gate**: `make test` runs `pytest-randomly` with an **unpinned** seed,
  so every run re-proves the suite does not depend on collection order. Never pin the seed to
  make a red run green — a failure means real shared state leaked. The usual culprits are
  `app.dependency_overrides` (a plain dict on the app object; clear it in an autouse
  teardown) and module-level state parsed at import (read the env fresh instead — see F-016).
- **Harness gate**: `make harness-score` must pass at the `MIN_LEVEL` floor set in the
  Makefile (currently **L1**); CI enforces it (`harness_score` job). Raise the floor
  ONLY after the score genuinely clears the next level — never to turn a red CI green.
  **Never satisfy a check with a file no tool actually reads** (nested `CLAUDE.md`,
  an unused `.mcp.json`, a `[tool.ruff]` block shadowed by `ruff.toml`, a
  `.pre-commit-config.yaml` that conflicts with `.githooks/`): a scanner point bought
  that way is a lie about the harness. Ground truth the scanner does not know:
  `ruff.toml` is the lint SSOT (a `[tool.ruff]` block in `pyproject.toml` is dead
  config); pre-commit is `.githooks/` + `core.hooksPath` (`make install-hooks`), which
  excludes a `.pre-commit-config.yaml`; rule prose lives in `AGENTS.md`, never in tool
  rule dirs; `make` targets + `.claude/skills/` replace `.claude/commands/`. Checks that
  stay red for those reasons stay red. Recipe: `.claude/skills/harness-score/`.
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
| Architecture overview | `docs/site/architecture.md` |
| Security / read-only policy | `docs/SECURITY.md`, `docs/READ_ONLY_POLICY.md` |
| Observability pipeline | `docs/OBSERVABILITY.md` |
| All custom metrics | `docs/METRICS.md` |
| Product why / personas / success metrics | `docs/prd/aigent-squad.md` |
| Architecture decisions (ADR index) | `docs/architecture/decisions/README.md` |
| Bedrock design decision | `specs/ADR-001-bedrock-direct-vs-strands.md` (= ADR-0001) |
| Pluggable LLM transport / OpenAI-compatible provider | `docs/architecture/decisions/0009-llm-provider-abstraction.md`, `specs/28-llm-provider-abstraction/` |
| Deploy to K8s | `helm-charts/charts/aigent-squad` (sibling repo) |
| Cut a release | `RELEASE.md` (spec 34) — 8-phase runbook, cross-repo |
