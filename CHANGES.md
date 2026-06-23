# Changelog

## [Unreleased]

### Added (Spec 31 L3: Global admission guards)
- `src/core/rate_limiter.py`: `AdmissionGuard` (per-user sliding-window rate + global daily budget, Redis-coordinated) + `estimate_cost` (pessimistic per-model pricing). **Fail-open** (Redis down → allow), the opposite of the spec-14 guardrail (security, fail-closed) — distinction documented in the module. Placed in `src/core/` so spec 25 reuses it (round-table).
- Wired into the gateway: `_check_admission` runs BEFORE pool/preflight/forward in `/query` and `/v1/chat/completions` — a denied request never reaches the supervisor (no spend). 429 `rate_limited` (`X-RateLimit-Remaining`) / 503 `budget_exhausted` (`X-Budget-Remaining-USD`). Master switch `RATE_BUDGET_ENABLED`.
- `aigent.rate_limit.blocks` metric (label `reason` = user/global, bounded cardinality).
- Tests: `tests/test_rate_limiter.py` + `tests/test_gateway_admission.py` + `tests/test_gateway_main_paths.py` (100% on rate_limiter; gateway 96% overall). Independent author + review (APPROVE-WITH-NITS). Budget check-and-increment TOCTOU deferred to hardening (T19d — needs real-Redis EVAL/Lua; test fakeredis lacks `eval`).

### Added (Spec 31 L1+L2: Edge gateway + worker pool — implemented)
- `src/gateway/`: thin FastAPI front door (`main.py`) — hosts native `/query`, OpenAI `/v1/*` (reusing spec 29 `openai_compat` shaping), `/jobs/{id}/cancel`, `/healthz`, `/ready`. Holds no orchestration logic; forwards to the supervisor.
- `gateway/worker_pool.py`: local `asyncio.Semaphore(20)` admission with immediate-reject `PoolFullError → 503`, cancel via `cancel:<job_id>` Redis poll, three timeouts (first-byte 15s / idle-stream 10s / job backstop 45s), fail-open job lifecycle (Redis down → log-only + `redis_fallback_active` metric).
- `gateway/supervisor_client.py`: httpx client with `is_supervisor_ready` preflight, `process`, `list_agents`; pool sized `max_concurrent+5` to avoid hidden backpressure.
- `gateway/auth.py`: edge auth (`INTERNAL_API_TOKEN` or `GATEWAY_API_KEYS` allowlist), fail-closed.
- `src/core/internal_auth.py`: `require_internal_token` (`X-Supervisor-Token`), fail-closed, **distinct** from `INTERNAL_API_TOKEN`.
- `src/supervisor/server.py`: public `/query` + `/v1/*` **removed** (moved to gateway); added `/internal/process` + `/internal/agents` (internal-token gated). Supervisor is now a backend on **:8001**; guardrail (spec 14) still runs on every `/internal/process` call.
- Backpressure: `503 + dynamic Retry-After (jitter)`, body subtypes `service_overloaded` (pool full, self-healing) vs `backend_unavailable` (supervisor unreachable). Gateway `/ready` decoupled from supervisor health (avoids cascade).
- `src/core/metrics.py`: `aigent.gateway.pool_rejections`/`pool_depth`/`queue_wait`/`redis_fallback_active`.
- `src/core/config.py` + `.env.example`: `SUPERVISOR_INTERNAL_TOKEN`, `SUPERVISOR_URL`, `GATEWAY_*` pool/timeout settings.
- Tests: `tests/test_gateway_{worker_pool,client,main}.py` + `tests/test_internal_auth.py` (62 tests, **92% coverage** on new code). Independent test-author + code-review (APPROVE-WITH-NITS; nits fixed). L3–L5 (admission guards, Helm two-tier, NetworkPolicy/Rollout, docs/k6) pending.

### Added (Spec 31: Edge gateway + worker pool — spec only)
- `specs/31-edge-gateway-worker-pool/{requirements,design,tasks}.md`: design for a thin FastAPI gateway in front of the supervisor (admission control, `WorkerPool` backpressure, protocol isolation, global rate/budget), enabling the supervisor to scale multi-replica. Reuses `staffops-chaitops` `agent-api` patterns (authorized internal reuse). Documents the concurrency trade-off: **local** per-replica semaphore (pod self-protection) vs **global** Redis-coordinated budget/TPS guard — and corrects spec 25's framing (its in-memory Bedrock semaphore can't bound a global TPS limit under multi-replica). No code yet.
- Round-table sign-off (dev + security + sre + gitops, 2026-06-22): land 31 before 25 (shared guards in `src/core/`); day-1 security = NetworkPolicy + dedicated `SUPERVISOR_INTERNAL_TOKEN` (file-mounted, fail-closed), Istio mTLS later (additive); pool defaults = global, `max_concurrent=20`, immediate-reject, `job_timeout=45s` + first-byte 15s + idle 10s. Gateway readiness decoupled from supervisor health (avoid cascade); guardrail (spec 14) stays supervisor-side on every `/internal/process` call.

### Added (Spec 14 Phase 1: Bedrock Guardrail + fail-closed anti-prompt-injection)
- `infra/terraform/guardrail/`: `aws_bedrock_guardrail` (PROMPT_ATTACK `HIGH` input filter — natively multi-language; harmful-content filters input+output; PII `BLOCK`; optional operator-defined denied topics) + a published immutable version. Outputs `guardrail_id`/`guardrail_version` for the app to pin.
- `src/core/guardrail.py`: `GuardrailClient.apply(text, source, …)` evaluates untrusted input (pre-invoke) and model output (post-invoke) via the `apply_guardrail` API — a distinct evaluation from the agent's prompt (Decision 1: an injection that fools the LLM does not fool the guardrail). **Fail-closed** (Decision 2): a block OR guardrail unavailability/misconfiguration raises `GuardrailBlockedError` — never bypasses to the model.
- `src/core/bedrock.py`: guardrail applied in `_invoke_sync` (INPUT before spend, OUTPUT before return), outside the retry loop. `GuardrailBlockedError` is re-raised without tripping the circuit breaker (security refusal ≠ Bedrock fault). New `user_id`/`session_id` invoke params for audit traceability.
- Fail-closed propagation: `classifier` (no keyword-fallback on block), `supervisor` (`process_request`/`_single_agent_call`/`_fan_out` — refuses if **any** agent is blocked), `investigation` (refuses rather than emit an empty RCA). Mapped to **HTTP 403** at `/query` and `/v1/chat/completions` (OpenAI-shaped error).
- Structured audit log (no payload in clear text): `audit=True`, event, source, `agent_id`/`user_id`/`session_id`, and a sha256[:12] digest. `_extract_categories` records only detector labels, never matched text.
- `src/core/config.py`: `guardrail_enabled` (default `True`), `guardrail_id`, `guardrail_version` (`DRAFT` dev default; prod pins the Terraform-published version).
- Tests: `tests/test_guardrail.py` (25) + guardrail cases in `tests/test_bedrock.py`. **99% coverage** on `guardrail.py`. Independent test-author + code-review (APPROVE-WITH-NITS, nits fixed) per `verification-independence`.

### Removed (root doc cleanup — spec 24)
- Deleted stale root docs `VERSIONS.md` and `GENERIC_VERSION.md` (v2.0-era, 2026-02-14): package versions now live in `requirements.txt`/`CHANGES.md`; the "generic/sanitized" note described the obsolete `src/agents/` 5-agent layout.
- Archived `IMPLEMENTATION_HISTORY.md` → `archive/` (historical v2.0 roadmap, phases 6–13; still referenced by `specs/ROADMAP.md`).
- Updated refs: `README.md` (Getting Started + roadmap pointer), `specs/ROADMAP.md`.

### Changed (infra/ reorganized)
- `terraform/` → `infra/terraform/` (IaC under one roof). `git mv`, history preserved.
- Observability configs grouped: `infra/{otel-collector,tempo,prometheus}.yaml` + `infra/grafana/` → `infra/observability/`. `docker-compose.yaml` volume paths updated (validated with `docker compose config`).
- `infra/` now organized by domain: `terraform/`, `observability/`, `librechat/`, `postgres/`.
- Real path refs updated (README, AGENTS.md, ROADMAP, terraform README, OBSERVABILITY.md). Illustrative `terraform/ec2.tf` examples (read-only refusal) left as-is.

### Changed (AI-tool-agnostic layout)
- `AGENTS.md` is now the canonical, tool-neutral agent guide (was `CLAUDE.md`); `CLAUDE.md` is a one-line pointer (`See @AGENTS.md`). Any AI assistant (Claude Code, Cursor, Copilot, Aider…) reads `AGENTS.md`.
- Specs moved `.kiro/specs/` → `specs/` and steering `.kiro/steering/` → `steering/` (history preserved); `.kiro/` removed. All 70+ references across docs/README/specs updated.
- Per-tool dirs (`.claude/`, `.cursor/`, `.aider*`, `.kiro/`, `.windsurf/`) are now git-ignored (local-only). `.claude/` (subagent defs were tool-test artifacts) + `scripts/sync-claude.sh` removed from the repo.
- `README.md` "AI tooling" section rewritten to the tool-agnostic model.

### Added (Spec 30: Datasource cache layer)
- `src/core/adapters.py`: `DatasourceAdapter.collect()` is now a template method wrapping a deterministic (`sha256`) TTL cache around each adapter's `_collect()`. Per-agent TTL + namespace from `agent.yaml`; fail-open (cache errors never break collection — wrapped `cache.get`/`cache.set`); disabled when `ttl <= 0`.
- `create_adapters(..., cache_ttl, cache_namespace)` threads cache config onto every adapter; `supervisor/agent.py` passes `config.cache.{ttl,namespace}`.
- Emits `aigent.cache.hits` / `aigent.cache.misses` (label `namespace`) — closes the dead-metric gap (defined but never emitted).
- `docs/METRICS.md`: cache hits/misses moved out of "Known gaps"; `aigent.cache.tokens_saved` reassigned to spec 11 (a datasource hit avoids an API call, not LLM tokens).
- `tests/test_cache_layer.py` (20 tests, independent author): hit/miss/disabled/fail-open (store + wrapper)/key-determinism/threading. 266 passed, 92.62% coverage.

### Changed (CI/CD hardening — Model A)
- `build.yml` + `release.yml`: **scan-before-publish** (build local → Trivy gate → push) — a vulnerable image never reaches the registry.
- `release.yml`: tag-driven (`v*`) / manual, Docker Hub, immutable `:X.Y.Z` + SBOM + GitHub Release (replaced legacy ECR/SSH).
- `test.yml`: `guard` job (main accepts PRs only from `dev`) + `dep_scan` (Trivy fs deps); PRs run on main and dev.
- `sast.yml`: Bandit SAST (CodeQL unavailable — private repo without GHAS). 4 reviewed B104 false-positives suppressed with `# nosec`.
- `docs.yml`: deploy only from `main` (was overwriting prod from `dev`); `mkdocs build --strict` validation on PRs.
- `docs/CI-CD.md`: Model A pipeline + versioning (SemVer app↔chart↔image). Branch protection documented as plan-gated (not enforced — needs GitHub Pro/public; tracked in HANDOFF).
- Fixed broken Architecture page (case collision `ARCHITECTURE.md`→`architecture.md`) + LIBRECHAT external links (`--strict` clean).

## [0.2.0] - 2026-06-21

First tagged release. Bundles all previously-unreleased work below (sessions
2026-06-17 / 06-18 / 06-21): spec 07 readiness probes, spec 29 OpenAI bridge,
spec 10 metrics, Alpine image, Docker Hub CI, Apache 2.0, MkDocs site, CVE
cleanup, and the CI/CD model (see `docs/CI-CD.md`). Establishes app↔chart
version linkage (`appVersion` 0.2.0, scan-gated `release.yml`).

### Added (CI/CD model + versioning)
- `docs/CI-CD.md`: Model A pipeline (branch strategy, two lanes, scan-before-publish, SemVer linkage app↔chart↔image)
- `release.yml`: rewritten — tag-driven (`v*`) / manual, Docker Hub, build-local → **Trivy gate** → push immutable `:X.Y.Z` + `latest`, SBOM + GitHub Release. Replaces the legacy ECR/SSH workflow.

### Added (Spec 10 Phase 1: Metrics — efficiency + quality)
- `src/core/metrics.py`: 4 new metrics — `aigent.collect.duration` (histogram, ms, `agent_id`), `aigent.llm.duration` (histogram, ms, `agent_id`), `aigent.prompt.size_tokens` (histogram, `agent_id`), `aigent.investigation.rounds` (histogram)
- `src/core/generic_agent.py`: emit `aigent.collect.duration` around the adapter fan-out (data-collection latency split from LLM)
- `src/core/bedrock.py`: emit `aigent.llm.duration` (round-trip latency, excludes retry backoff) + `aigent.prompt.size_tokens` (Bedrock-reported input tokens as a distribution — detect prompt bloat) on every successful call (covers agents, classifier, synthesizer, RCA)
- `src/supervisor/investigation.py`: emit `aigent.investigation.rounds` (rounds_completed; 1 today, ready for multi-round)
- `docs/METRICS.md`: reorganized by purpose (RED / Efficiency / Quality / Domain) + new "Known gaps" section documenting that `aigent.cache.hits/misses` are **defined but never emitted** (datasource cache not wired into adapters — deferred to a future `datasource-cache-layer` spec)
- Tests: `test_bedrock` (llm.duration + prompt.size, snake_case key, usage-absent, backoff-excluded, not-on-failure), `test_generic_agent` (collect.duration with/without adapters), `test_run_investigation` (rounds value + no-label cardinality). Verification independence: tests reviewed/strengthened by a separate agent. 246 passed, 92.46% coverage
- `Dockerfile.test`: switched from `--mount=type=ssh` to `--mount=type=secret,id=github_token` (matches main Dockerfile; HTTPS private dep)

## [Unreleased] - 2026-06-17

### Added (Spec 29: OpenAI-compatible bridge — LibreChat)
- `src/supervisor/openai_compat.py`: OpenAI Chat Completions surface on the supervisor — `GET /v1/models` + `POST /v1/chat/completions` (behind `require_token`)
- Models exposed: `aigent-squad` (classifier auto-routes) + `aigent-squad-<agent>` (force a specialist)
- `SupervisorAgent.process_request(force_agent=...)`: per-agent fast-path that bypasses the classifier (preserves history + metrics)
- Streaming is pseudo-streaming (full answer as one SSE chunk) until spec 06 token streaming; `usage` returns zeros (spec 10/27)
- `docs/LIBRECHAT.md` + `infra/librechat/librechat.yaml` (wire the squad as a LibreChat custom endpoint)
- `tests/test_openai_compat.py` (100% coverage on the module) + `force_agent` supervisor tests

### Added (Claude Code compatibility)
- `CLAUDE.md`: entrypoint with build/test commands, architecture invariants, read-only posture, and `@imports` of `steering/*.md` (single source of truth)
- `.claude/`: `rules` + `skills` symlinked to `steering` + `skills`; 6 subagents converted from `agents/<name>/`; `settings.json` (read-only permissions); `README.md`
- `scripts/sync-claude.sh`: idempotent regenerator (`.kiro/` + `agents/` → `.claude/`)

### Added (Spec 07: Readiness probes)
- `src/core/health.py`: `DependencyChecker` — async checks for Redis, DynamoDB, Bedrock creds, and arbitrary HTTP endpoints; per-dep TTL cache (5 s) + timeout (2 s)
- `src/supervisor/server.py`: `/healthz` (liveness — always 200), `/ready` (readiness — 503 when Redis/DynamoDB/agents unavailable), `/health` kept as legacy alias
- `mcp-server/mcp-server.py`: same probe set; `/ready` checks supervisor reachability via httpx
- `docker-compose.yaml`: healthchecks updated from `/health` to `/ready` (supervisor + mcp-server), retries 3→5 on supervisor
- `tests/test_health.py`: 20 new tests (DependencyChecker unit + supervisor + mcp endpoint tests); 237 total passing, 92.46% coverage
- `Dockerfile.test`: reproducible test image (`python:3.11-slim` + SSH for private `otel-helper` dep); run via volume mount — no rebuild on code change

### Added (CI: Docker Hub image publishing)
- `.github/workflows/build.yml`: new `build-dockerhub` job — builds true multi-arch manifest (`linux/amd64,linux/arm64`) and pushes `karlipegomes/aigent-squad:latest` + `:sha-<short>` on every merge to `main`
- Fixed `build-ecr` SSH agent setup: now uses `webfactory/ssh-agent` (exposes `SSH_AUTH_SOCK` to Docker buildx) instead of checkout-only `ssh-key`

### Added (Helm chart — repo `helm-charts`, chart `aigent-squad` 0.5.0)
- Own generic chart (no vendor conventions): `services` map → Deployment | StatefulSet, autoscaling none | HPA | KEDA, routing none | Ingress | Gateway API
- Two topologies: `inProcess` (default) and `distributed` (`values-distributed.yaml`)
- Opt-in NetworkPolicy, ExternalSecret, read-only RBAC, in-cluster Redis (DEV)
- Repo migrated to `StaffOps/helm-charts`; published via `chart-releaser-action` → GitHub Pages
- Cleaned up "provider-agnostic" language throughout; removed Argo Rollouts (T5) and per-env values files (T14) from spec 05 scope

### Fixed (README accuracy)
- Residual Portuguese, stale `Claude 3.5` → `Claude Sonnet 4.5`, KB clarified as PostgreSQL+pgvector, `Last Updated` date, roadmap pointer; added LibreChat + Claude Code references

## [Unreleased] - 2026-06-16

### Added (Terraform infrastructure — `terraform/`)
- `iam/`: single IRSA role + scoped policies (Bedrock invoke, DynamoDB sessions = only write, read-only inventory ec2/rds/s3/ce/iam, optional Athena/CUR FinOps)
- `dynamodb/`: sessions table (`pk`/`sk`, TTL, PITR, on-demand, SSE)
- `bedrock/`: VPC endpoints (`bedrock-runtime` + `bedrock`) + optional invocation logging
- `bedrock-aip/`: Application Inference Profiles (1 per model, configurable map) for FinOps cost attribution
- `example/`: reference composition wiring all modules; cost/governance tags centralized in `provider.default_tags` (single source of truth)
- All modules validated via `terraform validate` (Docker)

### Added (Spec 26: Agent Skills)
- `src/core/skills.py`: `Skill` + `SkillRegistry` — lazy-loaded markdown knowledge from global `skills/`, allowlist per agent (`agent.yaml` `skills:`), keyword match
- Injected into the system prompt only when the query matches (token economy); fail-open
- Example skill `skills/oomkill-investigation/`; wired into the kubernetes agent
- `tests/test_skills.py` (19 tests, 100% coverage on skills.py)

### Added (Spec 27: Bedrock cost attribution)
- AIP-per-model (Terraform) carries FinOps tags → authoritative per-model spend in Cost Explorer
- `BedrockClient.invoke` now labels token/cost metrics with `agent_id` (callers: GenericAgent, Classifier) → per-agent showback via token-share ratio
- `tests/test_bedrock.py`: +2 tests (agent_id label propagation)

### Added (MCP outbound — agents as MCP clients)
- `McpAdapter` (`type: mcp` datasource): read-only tool allowlist (fail-closed), SSE transport, `inject_query_as` opt-in
- Wired the kubernetes agent to the cluster's `devops-mcp-kube` server (read-only tools only; mutating tools excluded by allowlist)
- `tests/test_adapters.py`: +7 MCP tests; validated end-to-end against the real cluster MCP server
- `docs/MCP_INTEGRATION.md`: documented both directions (inbound server / outbound client)

### Fixed (Bedrock model id requires inference profile)
- `BEDROCK_MODEL_ID` corrected to the `us.` inference profile (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`); the bare model id fails with `on-demand throughput isn't supported`
- Updated `config.py`, `.env.example`, `docker-compose.yaml`, `src/supervisor/README.md`, and Terraform `allowed_model_arns`

### Added (ADR)
- `specs/ADR-001-bedrock-direct-vs-strands.md`: decision to keep Bedrock-direct over the Strands SDK (with reopen signals)

## [Unreleased] - 2026-06-14

### Changed (Coverage gate raised: 80% → 90%)
- Test suite expanded from 124 to 166 tests (+42 targeted tests)
- Total coverage: **91.72%** (up from 84%)
- `.coveragerc`: `fail_under = 90`
- `.github/workflows/test.yml`: `--cov-fail-under=90`
- `steering/milestone-criteria.md`: minimum coverage updated to 90%
- New tests target uncovered branches in: kb/store, state_store, cache, investigation, bedrock, kb/budget, kb/extractor, kb/embedder, kb/rag, circuit_breaker, supervisor/agent, supervisor/distillation

### Added (Spec 18 Phase 2: Alert Ingestion + Slack post-back)
- `src/supervisor/alert_handler.py`: AlertmanagerPayload + AlertmanagerAlert (Pydantic), `alert_to_symptom()`, fingerprint dedup via Redis, `handle_alert_payload()` orchestrator
- `src/supervisor/slack_notifier.py`: `post_rca_to_slack()` (opt-in via `SLACK_WEBHOOK_URL`)
- `POST /alerts/incoming` endpoint (auth via `X-Internal-Token`)
- New metrics: `aigent.alerts.received`, `.deduplicated`, `.investigation_triggered`, `.postback`
- `docs/ALERTING.md`: Alertmanager config + flow + Slack post-back + dedup behavior
- `ALERT_DEDUP_TTL` env (default 3600s)

### Documentation audit
- Rewrote outdated docs to reflect current architecture: `ARCHITECTURE.md`, `OBSERVABILITY.md`, `PREREQUISITES.md`, `MCP_INTEGRATION.md`, `READ_ONLY_POLICY.md`
- Deleted obsolete docs: `MIGRATION.md` (LangGraph era), `RAG_IMPLEMENTATION.md` (replaced by KNOWLEDGE-BASE.md), `LOCAL_DEVELOPMENT.md` (duplicated SETUP.md with old ports)
- Tightened `steering/milestone-criteria.md`: operational docs (ARCHITECTURE/SETUP/SECURITY/etc) now listed as mandatory milestone gate; new anti-pattern: "stale docs are worse than no docs"

### Added (Metrics audit — covering specs 06, 17, 18, 21)
13 new custom metrics + instrumentation in existing code:
- Spec 06: `aigent.circuit_breaker.transitions`
- Spec 17: `aigent.fanout.calls`, `aigent.fanout.agents_consulted`, `aigent.fanout.agents_failed`, `aigent.synthesizer.calls`
- Spec 18: `aigent.investigation.started`, `.completed`, `.duration`, `.evidence_count`
- Spec 21: `aigent.kb.distillation.cost`, `.items_created`, `.rag.queries`, `.rag.hits`, `.budget.exhausted`
- Updated `docs/METRICS.md` with full reference (table per domain + label cardinality)
- Updated `steering/milestone-criteria.md` to make metrics a mandatory milestone gate (equal weight to tests/docs)

### Added (Spec 21: Incident Memory & Learning)
- Postgres+pgvector container (`pgvector/pgvector:pg16`) for KB persistence
- `infra/postgres/init.sql`: kb_items + kb_provenance schema, HNSW index, FTS fallback
- `src/core/kb/`: full KB module (models, store, redactor, extractor, enricher, validator, embedder, rag, budget)
- `src/supervisor/distillation.py`: fire-and-forget distillation pipeline (after each RCA)
- RAG injection wired into `run_investigation` (`<similar_cases>` block in synthesizer prompt)
- Endpoints `/kb/pending`, `/kb/{id}/approve`, `/kb/{id}/reject`
- PII redaction (emails, AWS keys, GitHub/GitLab PATs, Bearer tokens, OpenAI keys)
- Monthly budget cap ($50 default, `KB_MONTHLY_BUDGET_USD` env)
- Confidence thresholds per item type; `decision` type never auto-approves
- `docs/KNOWLEDGE-BASE.md` with full reference

### Added (Spec 18 Phase 1: RCA Investigation Workflow)
- `src/core/investigation.py`: Evidence, RCAResult, InvestigationState dataclasses
- `src/core/investigation.py`: `build_timeline()` — sorts evidence by timestamp, marks causal candidates (deploy/restart/config)
- `src/core/investigation.py`: `correlate()` — confidence rule (≥3 independent signals → alta; contradicting evidence rebaixa)
- `src/core/triage.py`: `should_investigate()` — keyword heuristic (no Bedrock call) for trivial-vs-investigate decision
- `src/supervisor/investigation.py`: `run_investigation()` orchestrator — fan-out evidence collection (parallel) + RCA synthesizer (single Bedrock call)
- `mode=investigate` flag on `/query` endpoint forces investigation workflow
- `RCA_MAX_AGENTS` env var (default 5) caps cost per investigation

### Added (Spec 17: Multi-Agent Fan-Out + Synthesizer)
- `src/supervisor/synthesizer.py`: fuses N agent responses into 1 coherent answer
- `src/core/agent_tools.py`: agent-as-tools helper with depth=1 guard (contextvars)
- Supervisor fan-out: cross-domain queries trigger parallel `asyncio.gather` of N agents
- Classifier returns multi-agent list (`AgentMatch[]`) with backward-compat `selected_agent`
- max_agents=3 cap (cost protection)
- Partial failure tolerance: 1 agent down → response synthesized with the rest

### Changed (Spec 17)
- ClassifierResult: now holds `agents: list[AgentMatch]` (was scalar `selected_agent`)
- N=1 queries: fast-path preserved (zero synthesis overhead)

### Added (Spec 06: Resilience Patterns)
- `src/core/circuit_breaker.py`: CircuitBreaker (closed→open→half-open) for Bedrock calls
- Classifier keyword fallback using `routing_keywords` from agent configs when LLM unavailable
- Graceful shutdown via FastAPI lifespan (drain + flush)

### Changed (Spec 06)
- `bedrock.invoke()` is now async (`asyncio.to_thread`) — enables real parallelism for fan-out
- Retry with jitter + botocore adaptive retry mode
- Redis fail-open: connection failure at startup + all ops wrapped in try/except
- DynamoDB fail-open: fetch returns `[]`, save logs warning — never crashes

### Added (Spec 08: CI/CD Pipeline)
- `.github/workflows/test.yml`: ruff lint + pytest --cov-fail-under=80 on push/PR
- `.github/workflows/build.yml`: multi-arch buildx (amd64+arm64) + Trivy scan + SBOM + OIDC push to ECR
- `.github/workflows/release.yml`: manual workflow_dispatch, semver tag + stable image

### Removed
- `docs/GITLAB_CI_SETUP.md` (obsolete, repo is on GitHub not GitLab)

### Changed
- `docs/SETUP.md` rewritten with current architecture (docker compose + Helm + GitHub Actions)

### Added (Spec 22 Phase B: Helm Chart)
- Helm chart at `helm-charts/charts/aigent-squad/` (deploy to K8s)
- `agentsSource: configmap` — agents inline in values.yaml
- `agentsSource: git` — initContainer clones agent definitions from git repo
- 6th agent "security" (demonstrates zero-code extensibility)
- `docs/HOW-TO-NEW-AGENT.md` — guide for creating agents (30s quick start)

### Added (Spec 22 Phase A: Config-Driven Agent Platform)
- `src/core/agent_config.py`: AgentConfig Pydantic schema
- `src/core/registry.py`: AgentRegistry with auto-discovery from AGENTS_DIR
- `src/core/adapters.py`: DatasourceAdapter interface + Boto3/K8s/Http/Athena adapters
- `src/core/generic_agent.py`: GenericAgent (single implementation for all agents)
- `agents/`: 5 agent config directories (aws, kubernetes, finops, devops, observability)
- `tests/`: 13 tests (registry: 7, generic_agent: 6) — verification-independent

### Changed (Spec 22 Phase A)
- Agents now run IN-PROCESS (no HTTP inter-service calls)
- docker-compose: 9 app containers → 2 (supervisor + mcp-server) + infra
- Classifier builds agent list dynamically from registry
- Single Docker image for entire platform

### Added (Observability Stack)
- Integrated `staffops-otel-libs` Python helper (`setup_telemetry()` in all 6 servers)
- Local observability stack: OTel Collector (contrib 0.102) → Tempo (2.4.1) + Prometheus (2.52)
- Grafana (10.4.2) on `:3001` with auto-provisioned dashboards (API metrics, workers, traces)
- All services emit traces/metrics via `OTEL_EXPORTER_OTLP_ENDPOINT`

### Changed (Spec 02: Unify Agent Architecture)
- Rewrote kubernetes/devops/finops/observability `server.py` — all now use their `agent.py` class (mirrors aws pattern)
- All 5 `/process` endpoints return uniform contract `{role, content, timestamp, agent_id}`
- Supervisor simplified: reads `agent_response["content"]` directly (removed `get("response")` fallback)
- All 5 agents use `chat_history` via `_format_history()` for multi-turn context

### Fixed (Spec 01: Fix Blockers)
- Created root `Dockerfile` for supervisor service (python:3.11-slim)
- Removed duplicate class body in `src/core/gitlab_client.py` (kept 1st definition + 1 singleton)
- Removed duplicate `app`/`SUPERVISOR_URL` declarations in `mcp-server/mcp-server.py`
- Rewrote `src/api/server.py` — removed langchain/StateStore/graph imports, uses `supervisor.process_request()`
- Added `__init__.py` to all `src/` packages (required for `python -m` execution)
- Fixed supervisor healthcheck: `wget --spider` → `curl -f` (GNU wget HEAD rejected by FastAPI)

### Result
- `docker compose build` passes for all 7 services
- `docker compose up -d` starts 9 containers, all healthy
- Imports validated: `src.core.gitlab_client`, `src.api.server` — no errors

---

# 🎯 Agent Squad v2.0 - AWS Labs Best Practices

## ✅ All Implemented Changes

### 📦 New Components

```
code/src/
├── core/
│   ├── agent_base.py          # ✨ NEW: Base class for all agents
│   ├── classifier.py          # ✨ NEW: Intelligent classifier
│   ├── state_store.py         # 🔄 REFACTORED: Storage with separated contexts
│   └── server_template.py     # ✨ NEW: Generic template for servers
│
├── agents/
│   ├── aws/
│   │   ├── agent.py           # 🔄 REFACTORED: Standardized interface
│   │   ├── server.py          # 🔄 UPDATED: New endpoint
│   │   └── server_new.py      # ✨ NEW: Server with template
│   ├── kubernetes/
│   │   ├── agent.py           # 🔄 REFACTORED
│   │   └── server_new.py      # ✨ NEW
│   ├── finops/
│   │   ├── agent.py           # 🔄 REFACTORED
│   │   └── server_new.py      # ✨ NEW
│   ├── devops/
│   │   ├── agent.py           # 🔄 REFACTORED
│   │   └── server_new.py      # ✨ NEW
│   └── observability/
│       ├── agent.py           # 🔄 REFACTORED
│       └── server_new.py      # ✨ NEW
│
└── supervisor/
    ├── agent.py               # 🔄 REFACTORED: Uses classifier
    └── server.py              # ✨ NEW: FastAPI server
```

---

## 🔑 Main Changes

### 1️⃣ **Intelligent Classifier** 🧠

```python
# Before: Supervisor decided manually
# After: Classifier analyzes with AI

from src.core.classifier import classifier

result = await classifier.classify(user_input, chat_history)
# → result.selected_agent = "aws"
# → result.confidence = 0.95
# → result.reasoning = "User asking about EC2"
```

**Features**:
- ✅ Detects follow-ups ("yes", "ok", "1")
- ✅ Intelligent context switching
- ✅ Confidence scoring
- ✅ Explainable reasoning

---

### 2️⃣ **Storage with Separated Contexts** 💾

```python
# GLOBAL (classifier sees everything)
all_chats = await storage.fetch_all_chats(user_id, session_id)

# ISOLATED (agent sees only its history)
agent_chats = await storage.fetch_chat(user_id, session_id, "aws")
```

**Benefits**:
- ✅ Classifier makes better decisions
- ✅ Agents don't get confused
- ✅ Privacy between agents

---

### 3️⃣ **Standardized Interface** 🔧

```python
# ALL agents now implement:
async def process_request(
    self,
    input_text: str,
    user_id: str,
    session_id: str,
    chat_history: List[ConversationMessage],
    additional_params: Optional[dict] = None
) -> ConversationMessage:
    ...
```

**Benefits**:
- ✅ Total consistency
- ✅ Easy to swap agents
- ✅ Simplified supervisor

---

### 4️⃣ **Simplified Supervisor** 🎯

```python
# Before: 200+ lines with LangGraph
# After: 100 lines with Classifier

# 1. Classify
classification = await classifier.classify(...)

# 2. Call agent
response = await http_client.post(agent_url, ...)

# 3. Save conversation
await storage.save_chat_message(...)
```

**Benefits**:
- ✅ Less code
- ✅ More intelligent
- ✅ Easier to maintain

---

## 🚀 How to Test

### Option 1: New Servers (Recommended)

```bash
# Terminal 1-5: Agents
python -m src.agents.aws.server_new
python -m src.agents.kubernetes.server_new
python -m src.agents.finops.server_new
python -m src.agents.devops.server_new
python -m src.agents.observability.server_new

# Terminal 6: Supervisor
python -m src.supervisor.server

# Terminal 7: Test
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "How many EC2 instances?",
    "user_id": "user123",
    "session_id": "session456"
  }'
```

### Option 2: Old Servers (Compatibility)

```bash
# Still work, but without improvements
python -m src.agents.aws.server
python -m src.agents.kubernetes.server
# ...
```

---

## 📊 Comparison

| Feature | Before | After |
|---------|--------|-------|
| **Routing** | Manual | AI Classifier |
| **Follow-ups** | ❌ | ✅ Automatic |
| **Context Switching** | ❌ Poor | ✅ Intelligent |
| **Conversation History** | Generic | Separated |
| **Agent Interface** | Inconsistent | Standardized |
| **Supervisor Code** | 200+ lines | 100 lines |
| **Confidence Score** | ❌ | ✅ |
| **Reasoning** | ❌ | ✅ |

---

## 📝 Next Steps

### Required Tests

- [ ] Test classifier with real queries
- [ ] Test follow-ups ("yes", "no", "1")
- [ ] Test context switching
- [ ] Test conversation history
- [ ] Test all 5 agents

### Deploy

- [ ] Update Dockerfiles
- [ ] Update K8s manifests
- [ ] Create DynamoDB table (terraform)
- [ ] Deploy on EKS
- [ ] Integrate with Slack

### Future Improvements (Optional)

- [ ] Streaming support
- [ ] Agent-as-tools pattern (AWS Labs SupervisorAgent)
- [ ] Parallel agent execution
- [ ] Metrics and observability

---

## 🐛 Troubleshooting

### Import Error
```bash
export PYTHONPATH=/path/to/code:$PYTHONPATH
```

### DynamoDB Error
```bash
cd terraform/
terraform apply
```

### Agent Not Responding
```bash
curl http://localhost:8001/health
```

---

## 📚 Documentation

- `docs/MIGRATION.md` - Detailed migration guide
- `docs/ARCHITECTURE.md` - Updated architecture
- `docs/LOCAL_DEVELOPMENT.md` - Local setup

---

## ✅ Status

- [x] Classifier implemented
- [x] Storage refactored
- [x] Base Agent class created
- [x] All agents refactored (5/5)
- [x] Supervisor refactored
- [x] Servers updated
- [x] Documentation created
- [ ] Tests performed
- [ ] Deploy on EKS

**Version**: 2.0  
**Date**: 2026-02-14  
**Status**: ✅ Implemented, awaiting tests
