---
spec: 46-portable-deployment-profiles
status: done
completed: 2026-11-24
superseded_by: null
depends_on: ["28-llm-provider-abstraction", "37-agentic-tool-calling"]
deferred: []
---

# Feature: Portable Deployment Profiles (history store + per-environment agents/MCPs)

**Spec**: `46-portable-deployment-profiles`
**Severity**: 🟠 Architectural (removes two hard AWS/environment couplings without changing the read-only or fail-closed invariants)
**Origin**: a cluster test outside AWS, 2026-11-24. Running outside AWS exposed two couplings:
(1) the conversation history only has a DynamoDB backend, so the supervisor `/ready` fails and the
gateway preflight returns 503 for every query (worked around with an in-memory DynamoDB Local);
(2) MCP URLs are baked into the image's `agents/*/agent.yaml` as one specific cluster's Service
names, which do not exist in other clusters, so the agentic tool path could not be exercised at all.
**Depends on**: `28-llm-provider-abstraction` (same provider-by-env pattern, already proven),
`37-agentic-tool-calling` (MCP datasources + fail-closed allowlist)

---

## Problem

The squad is meant to run on AWS and off AWS (any Kubernetes). Spec 28 made the LLM
portable; two other pieces are still bound to one environment:

1. **History store.** `src/core/state_store.py::ChatStorage` talks to DynamoDB only. The data is a
   per-agent, per-session, 24 h list of messages — key/value with TTL, no similarity search. Off AWS
   there is no DynamoDB; DynamoDB Local is in-memory and not a production option.
2. **Agents and MCPs.** Each environment has different MCP servers, names and namespaces, and some
   agents make no sense in some environments (`aws`/`finops` without an AWS account). Today the only
   per-environment lever is `agentsSource.type=configmap|git`, which replaces each agent's whole
   `agent.yaml` + `prompt.md`. Changing one URL means copying the prompt into every environment's
   values, and those copies drift from the repo.

## Thesis

Environment differences are **addresses and on/off switches**, never **capabilities**. An
environment chooses where history lives, which agents run, and where each MCP is; it never chooses
what an agent may do. The read-only tool allowlist stays in the versioned `agent.yaml`.

---

## User Stories

WHEN the squad runs off AWS with `HISTORY_BACKEND=redis` THEN conversation history SHALL be stored in
the Redis the deployment already requires, expire after 24 h, keep per-agent isolation
(`user#session` + `agent#timestamp` semantics), and `/ready` SHALL report the history check from the
selected backend, not DynamoDB.

WHEN `HISTORY_BACKEND` is unset THEN the backend SHALL be `redis` (user decision 2026-11-24): Redis is
already required everywhere, so the default works in any environment with no extra dependency.

WHEN the squad runs on AWS THEN the AWS profile SHALL set `HISTORY_BACKEND=dynamodb` explicitly, so the
existing AWS deployment keeps DynamoDB history with no behavior change.

WHEN `HISTORY_BACKEND=postgres` THEN history SHALL be stored in a plain relational table (no vector
column) in the Postgres already used by the KB, with expiry enforced by the store.

WHEN the history backend is unreachable THEN the query SHALL still be answered with empty history
(fail-open for availability, AGENTS.md invariant 6), exactly as with DynamoDB today.

WHEN an `agent.yaml` datasource field contains `${VAR}` THEN it SHALL be replaced at load time with
the value of env var `VAR`.

WHEN a `${VAR}` used by a datasource is unset THEN that datasource SHALL be disabled, a startup
WARNING SHALL name the agent, datasource and variable (never a value), `/ready` SHALL expose it as a
degraded datasource, and the agent SHALL still load.

WHEN a deployment sets an agent override (enabled flag and/or env values) in the chart values THEN the
agent SHALL be enabled/disabled or re-addressed WITHOUT copying its prompt or config.

WHEN an override tries to change a datasource's `tools` allowlist, `read_only`, or capability tier
THEN it SHALL be rejected at startup — environment profiles cannot widen capabilities.

WHEN a team deploys to a Kubernetes cluster outside AWS THEN a single example profile values file
SHALL give a working squad once its placeholders are filled: Redis history, an OpenAI-compatible LLM
(spec 28), the cluster's MCP addresses, AWS-only agents off. Environment-specific values live in the
deploying team's own GitOps repo, never in this product repo.

---

## Acceptance Criteria

1. `HistoryStore` protocol with `redis` (default), `dynamodb` (current code) and `postgres` backends,
   selected by `HISTORY_BACKEND`; invalid value fails loudly at startup. The AWS chart profile and the
   AWS overlay set `HISTORY_BACKEND=dynamodb` explicitly.
2. Redis backend: 24 h TTL native, last-N read per agent and across agents, per-agent isolation
   proven by tests equivalent to the current DynamoDB ones.
3. Postgres backend: schema created idempotently on startup, expiry enforced, no pgvector dependency.
4. `/ready` history check follows the selected backend; check name stays stable for dashboards.
5. ≥1 new metric: `aigent.history.backend_errors{backend}` (no high-cardinality labels), in
   `metrics.py` + `docs/METRICS.md`.
6. `${VAR}` expansion in `agent.yaml` datasource fields (`url`, `headers`, `tool_arguments`, and
   `name`); unset var → datasource disabled + WARNING + `/ready` degraded detail; values that come
   from secrets are never logged.
7. Expansion and overrides cannot touch `tools`, `read_only`, `capability_tier`, `required_env`
   semantics; any attempt fails startup (test-proven).
8. Default `agents/*/agent.yaml` MCP URLs become `${…}` with documented env names; the current
   values move to the AWS profile so the AWS deployment is unchanged.
9. Chart: `agentOverrides` map (`enabled`, `env`) rendered as env vars / agent enable flags, without
   duplicating prompts; `agentsSource` keeps working.
10. Chart profiles: `values-aws.yaml` (today's behavior, `HISTORY_BACKEND=dynamodb`) and
    `values-offaws.yaml` (example with placeholders: Redis history, OpenAI-compatible LLM, MCP
    addresses, `aws`/`finops` off); `helm lint` + render tests for both. No environment-specific
    hostnames, Service names or endpoints in either file.
11. Live verification on a Kubernetes cluster outside AWS using the off-AWS profile: `/ready` 200
    without DynamoDB, one query with history, one agentic query that calls a real MCP tool through
    an OpenAI-compatible LLM.
12. Docs: `docs/ARCHITECTURE.md`, `docs/HOW-TO-NEW-AGENT.md` (env-addressed MCPs), AGENTS.md env
    table, chart README.

## Out of scope

- Vector search over conversation history (the KB already owns pgvector; history is key/value).
- Removing DynamoDB from the AWS deployment.
- Distributed MCP topology changes (spec 42).
- Migrating existing DynamoDB history between backends (24 h TTL data — not worth migrating).
