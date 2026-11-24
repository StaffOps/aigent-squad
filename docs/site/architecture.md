# Architecture

## Overview

A thin **edge gateway** fronts a **supervisor** backend (spec 31). The gateway is
the only externally-exposed tier — it does edge auth, admission control (worker
pool + backpressure, global rate/budget), and protocol translation (native
`/query` + OpenAI `/v1`). It forwards to the supervisor over an internal-only
`/internal/process`. The supervisor runs all specialist agents in-process,
loaded from YAML config at startup — no inter-container HTTP between agents,
just function calls.

```
        ┌─────────────┐   ┌───────────┐   ┌─────────────────────────────┐
        │  LibreChat  │   │ Kiro CLI  │   │ In-cluster callers          │
        │  (OpenAI)   │   │ (via MCP) │   │ (Alertmanager, anomaly-det, │
        └──────┬──────┘   └─────┬─────┘   │  Falco — NetworkPolicy      │
               │                │         │  allowFrom)                 │
               │                │         └──────────────┬──────────────┘
               └────────────────┴────────────────────────┘
                                │  edge auth (X-Internal-Token / X-API-Key / Bearer)
                                ▼
        ┌──────────────────────────────────────────────────────────┐
        │                   GATEWAY (:8000)                         │
        │  edge auth · WorkerPool (backpressure) · admission        │
        │  (rate + budget) · OpenAI /v1 + /query · /jobs/{id}/cancel│
        └───────────────────────────┬──────────────────────────────┘
                                     │ /internal/process
                                     │ X-Supervisor-Token (distinct secret)
                                     │ NetworkPolicy: gateway-only
                                     ▼
┌──────────────────────────────────────────────────────────┐
│                  Supervisor (:8001, backend)              │
│                                                          │
│  ┌────────────┐  ┌────────────────┐  ┌──────────────┐   │
│  │ Classifier │  │ AgentRegistry  │  │ Fan-out/RCA  │   │
│  │ (routing)  │  │ (auto-discover)│  │ (orchestr.)  │   │
│  └────────────┘  └────────────────┘  └──────────────┘   │
│  ┌─ Guardrail (spec 14, fail-closed) on every invoke ─┐  │
│  ┌─────┐ ┌─────┐ ┌───────┐ ┌───────┐ ┌─────┐          │
│  │ AWS │ │ K8s │ │FinOps │ │DevOps │ │ Obs │  ...     │
│  └──┬──┘ └──┬──┘ └───┬───┘ └───┬───┘ └──┬──┘          │
│     └───────┴─────────┴─────────┴────────┘              │
│                  DatasourceAdapter layer                  │
│        Boto3 │ Kubernetes │ HTTP │ Athena │ MCP          │
└──────────────────────────────────────────────────────────┘
         │          │           │            │
    ┌────────┐ ┌────────┐ ┌─────────┐ ┌──────────────┐
    │ Redis  │ │Postgres│ │DynamoDB │ │ OTel Collect.│
    │cache + │ │pgvector│ │(history,│ │  → Prometheus│
    │history │ │        │ │ opt-in) │ │              │
    └────────┘ └────────┘ └─────────┘ └──────────────┘
```

> **Why two tiers?** The edge is cheap/stateless/IO-bound; the supervisor is
> expensive/Bedrock-bound. Splitting them lets each scale on its own signal
> (gateway on RPS, supervisor on Bedrock concurrency) and gives a clean admission
> point that sheds load (`503 + Retry-After`) before the expensive process is
> touched. See [spec 31](../../specs/31-edge-gateway-worker-pool/) for the full
> rationale. Both tiers run the **same image**, differing only in the launch
> command (`src.gateway.main` vs `src.supervisor.server`).

## Concurrency model (spec 31)

Two distinct, complementary limits — they protect different resources:

| Limit | Where | Scope | Protects |
|-------|-------|-------|----------|
| Worker pool semaphore | Gateway, in-memory | **per-replica** | This pod's resources (event loop, memory). Survives Redis outage. |
| Rate limit + daily budget | Gateway, Redis | **global** (all replicas) | Account-wide resources (Bedrock TPS, $/day) |

The local pool fails *open-to-reject* (`503` when full); the global guards fail
*open-to-allow* (Redis down → permit, availability over a hard cap). This is the
opposite of the spec-14 guardrail, which fails **closed** (security over
availability).

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| Edge gateway in front of the supervisor | Independent scaling; admission/backpressure before the expensive tier; client-protocol isolation (spec 31) |
| One image, command override per tier | No second build; gateway and supervisor can never drift in deps |
| Gateway is the single front door | External (LibreChat) AND in-cluster callers (Alertmanager, an anomaly-detection system, Falco) go through one authenticated, rate-limited entry — never the supervisor directly |
| Single image, in-process agents | No inter-container HTTP between agents; simpler; lower footprint |
| Config-driven agents (`agent.yaml` + `prompt.md`) | Zero-code agent creation; git-managed; hot-reload on restart |
| DatasourceAdapter pattern | Decouples data fetching from agent logic; enforces read-only at the code level |
| Bedrock-direct (no framework) | No LangGraph — direct `bedrock-runtime` API calls for full control of prompts and cost |
| Pluggable LLM transport (spec 28, ADR-0009) | `LLMService` common layer (breaker, retry, guardrail, token/cost metrics) + provider transport: Bedrock (default) or any OpenAI-compatible `/chat/completions` (`LLM_PROVIDER=openai`, Bedrock Guardrail L1 auto-off) |

## Core components

### Classifier
Routes user queries to the best-fit agent using a fast-path keyword match and, when ambiguous, a lightweight LLM classification call (Haiku tier).

### AgentRegistry
Auto-discovers agents from the `agents/` directory at startup. Each agent directory contains:

- `agent.yaml` — datasources, routing keywords, model tier, cache TTL, skills allowlist
- `prompt.md` — system prompt sent to the LLM

### Fan-out (spec 17)
Multi-agent parallel execution when a query spans N≥2 domains. Results are synthesized by a separate LLM call.

### RCA Investigation (spec 18)
Structured investigation: symptom → parallel evidence collection → LLM synthesis with confidence scoring (alta / média / baixa).

### Skills (spec 26)
Reusable markdown knowledge files (`skills/<name>/SKILL.md`) lazy-injected into the prompt only when the query matches the skill's keywords. Reduces prompt size (and cost) for queries that don't need the extra knowledge.

### Knowledge Base (spec 21)
Learns from completed investigations. Distills RCA results into KB items stored in Postgres+pgvector. Injects similar past cases via RAG on new investigations.

## Ports and endpoints

| Port | Service | Exposure |
|------|---------|----------|
| 8000 | Gateway API | Public (Ingress + in-cluster callers) |
| 8001 | Supervisor (`/internal/*`) | Internal — gateway-only (NetworkPolicy) |
| 8006 | MCP Server | Public (Kiro CLI facade) — calls the gateway |

### Gateway API (public, `:8000`)

| Path | Method | Auth | Purpose |
|------|:------:|:----:|---------|
| `/healthz` | GET | — | Liveness (always 200) |
| `/ready` | GET | — | Readiness (Redis + pool — NOT supervisor, decoupled) |
| `/query` | POST | `X-Internal-Token` / `X-API-Key` | Native query (admission → forward) |
| `/v1/models` | GET | edge auth | OpenAI-compatible model list |
| `/v1/chat/completions` | POST | edge auth | OpenAI-compatible chat (LibreChat) |
| `/jobs/{id}/cancel` | POST | edge auth | Cancel an in-flight job |

Edge auth accepts `X-Internal-Token`, `X-API-Key`, or `Authorization: Bearer <token>`
(any one). The OpenAI surface accepts **any** `model` value: `aigent-squad-<agent>` forces a
specialist, and any other id (`aigent-squad`, `base`, `large`, …) auto-routes via the classifier.

Backpressure: `503` with `Retry-After` and a body `error.type` of
`service_overloaded` (pool full, self-healing) or `backend_unavailable`
(supervisor unreachable). Admission denials: `429 rate_limited` /
`503 budget_exhausted` with `X-RateLimit-Remaining` / `X-Budget-Remaining-USD`.

### Supervisor (internal, `:8001`)

| Path | Method | Auth | Purpose |
|------|:------:|:----:|---------|
| `/healthz` | GET | — | Liveness |
| `/ready` | GET | — | Readiness: gates on Redis + ≥1 agent; reports the history backend (informational — history is fail-open) and degraded datasources |
| `/internal/process` | POST | `X-Supervisor-Token` | Orchestration entry (gateway-only) |
| `/internal/agents` | GET | `X-Supervisor-Token` | Agent names for `/v1/models` |
| `/alerts/incoming` | POST | `X-Internal-Token` | Alertmanager webhook → auto-RCA |

The guardrail (spec 14) runs inside `process_request` on **every** call, so the
injection-defense trust boundary is the supervisor, not the gateway. All
specialist agents run in-process — no per-agent ports.

## Data stores

| Store | Purpose | Persistence |
|-------|---------|-------------|
| Redis | Datasource cache (per-agent TTL 1–60 min); conversation history by default (`HISTORY_BACKEND=redis`, 24h) | Ephemeral |
| PostgreSQL + pgvector | KB items + RAG embeddings; optionally conversation history (`HISTORY_BACKEND=postgres`, plain table, 24h) | Persistent |
| DynamoDB | Conversation history when `HISTORY_BACKEND=dynamodb` (AWS deployments, TTL 24h) | TTL-based |

What each store can contain (prompts, infrastructure data), exact retention per backend, who can
read it and how to change it: [Data classification and retention](https://github.com/StaffOps/aigent-squad/blob/main/docs/SECURITY.md#data-classification-and-retention)
in `docs/SECURITY.md`. Conversation history is kept 24 h on every backend; the value is a fixed
constant today, not a setting. KB items have no expiry.

Every history backend caps the stored `content` of one message at `HISTORY_MAX_MESSAGE_CHARS`
(default 16000, truncated with a marker).

**Redis history and memory.** History shares Redis memory with the datasource cache, the
rate/budget counters and job state, and `session_id` is chosen by the client, so the number of
sessions is not bounded by the server. Set `maxmemory` and `maxmemory-policy volatile-ttl`:
under memory pressure Redis then evicts only keys that carry a TTL (history, cache, counters),
shortest remaining TTL first, and never keys without one. Note that the shortest-lived keys are the
per-minute rate counters, so sustained pressure weakens rate limiting (it fails open) before it
drops history; size `maxmemory` so eviction stays exceptional.

**Postgres history.** When the `aigent_history` table does not exist, the first connect creates it
and its indexes, which needs `CREATE` on the schema. To run with a DML-only role
(SELECT/INSERT/DELETE), create the table beforehand with a migration role (DDL in
`src/core/history_stores.py`, `_PG_SCHEMA`); an existing table is used as-is. Expired rows are
deleted in bounded batches: on every write, once when the store connects, and on reads at most
every 5 minutes. TLS: `POSTGRES_SSLMODE`.
