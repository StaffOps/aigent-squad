# Tasks: Portable Deployment Profiles

**Spec**: `46-portable-deployment-profiles` · see `requirements.md`, `design.md`

## Phase 1 — History store abstraction

> Done 2026-11-24 (local branch `feat/spec-46-portable-profiles`). T3 note: the shared contract
> suite runs against Redis (fakeredis) and Postgres (fake pool; live test opt-in via
> `HISTORY_PG_LIVE=1`); the DynamoDB store keeps its existing `tests/test_state_store.py`.

- [x] T1 `HistoryStore` protocol + factory (`HISTORY_BACKEND`, default `redis`, invalid → startup error, startup log names the backend) (AC 1)
- [x] T2 Move `ChatStorage` behind the protocol as `DynamoDBHistoryStore`, no behavior change (AC 1, Decision 4)
- [x] T3 Shared backend contract test suite (save, last-N per agent / all agents, isolation, expiry, fail-open) (AC 2)
- [x] T4 `RedisHistoryStore` (sorted set, EXPIRE 24 h, trim to cap) (AC 2)
- [x] T5 `PostgresHistoryStore` (idempotent schema, expiry on read + purge on write, no pgvector) (AC 3)
- [x] T6 `/ready` history check follows the backend; metric `aigent.history.backend_errors{backend}` + `docs/METRICS.md` (AC 4, 5)

## Phase 2 — Environment-addressed agents/MCPs

> Done 2026-11-24 (`src/core/agent_env.py`, wired in `AgentRegistry.discover`). Side fix: `http`
> datasource `headers` were never expanded before (the devops agent sent a literal
> `${GITLAB_TOKEN}`); expansion now happens once, at load, for every address field.

- [x] T7 `${VAR}` expansion in allowed datasource fields; unset → datasource disabled + WARNING + `/ready` degraded (AC 6)
- [x] T8 Reject expansion/override on `tools`, `read_only`, `capability_tier`, `required_env` (AC 7)
- [x] T9 `AGENT_<NAME>_ENABLED` override (AC 9 app side)
- [x] T10 Default `agents/*/agent.yaml` MCP URLs → `${…}`; document env names (AC 8)

## Phase 3 — Chart profiles (helm-charts repo)

- [ ] T11 `agentOverrides` map → env vars, no prompt copies (AC 9)
- [ ] T12 `values-aws.yaml` (today's behavior, `HISTORY_BACKEND=dynamodb`) + `values-offaws.yaml` (placeholder example: Redis history, OpenAI-compatible LLM, MCP addresses, aws/finops off; no environment-specific values) (AC 10)
- [ ] T13 Render tests + assertion that every `${VAR}` of enabled agents is set (AC 10)

## Phase 4 — Verification and docs

- [ ] T14 Docs: ARCHITECTURE, HOW-TO-NEW-AGENT, AGENTS.md env table, chart README, CHANGELOG upgrade note (AWS must set `HISTORY_BACKEND=dynamodb`), RELEASE.md check (AC 12)
- [ ] T15 Live verification on a Kubernetes cluster outside AWS: `/ready` 200 without DynamoDB, history across 2 turns, 1 agentic MCP call via an OpenAI-compatible LLM (AC 11)
- [ ] T16 Independent test authorship (different session than the implementer)
- [ ] T17 Independent code review (fresh context)
- [ ] T18 Security review (mandatory: datasource addressing / data egress, header secrets, capability widening)

## Order

Phase 1 and Phase 2 are independent and can run in parallel. Phase 3 needs T9/T10. T15 needs
Phases 1–3. T16–T18 close the spec.
