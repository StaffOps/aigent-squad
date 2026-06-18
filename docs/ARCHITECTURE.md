# Architecture

## Overview

A single supervisor process runs all specialist agents in-process, loaded from YAML config at startup. No inter-container HTTP between agents — just function calls.

```
                   ┌─────────────┐
                   │  Kiro CLI   │
                   └──────┬──────┘
                          │ HTTP (MCP protocol)
                          ▼
                   ┌─────────────┐
                   │ MCP Server  │ :8006
                   └──────┬──────┘
                          │ X-Internal-Token
                          ▼
┌──────────────────────────────────────────────────────────┐
│                  Supervisor (:8000)                       │
│                                                          │
│  ┌────────────┐  ┌────────────────┐  ┌──────────────┐   │
│  │ Classifier │  │ AgentRegistry  │  │ Fan-out/RCA  │   │
│  │ (routing)  │  │ (auto-discover)│  │ (orchestr.)  │   │
│  └────────────┘  └────────────────┘  └──────────────┘   │
│                                                          │
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
    │ cache  │ │pgvector│ │(history)│ │  → Prometheus│
    └────────┘ └────────┘ └─────────┘ └──────────────┘
```

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| Single image, in-process agents | No inter-container HTTP; simpler deploy; lower resource footprint |
| Config-driven agents (`agent.yaml` + `prompt.md`) | Zero-code agent creation; git-managed; hot-reload on restart |
| DatasourceAdapter pattern | Decouples data fetching from agent logic; enforces read-only at the code level |
| Bedrock-direct (no framework) | No LangGraph or similar — direct `bedrock-runtime` API calls for full control of prompts and cost |

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

| Port | Service |
|------|---------|
| 8000 | Supervisor API |
| 8006 | MCP Server |

All specialist agents run in-process in the supervisor — no per-agent ports.

### Supervisor API

| Path | Method | Auth | Purpose |
|------|:------:|:----:|---------|
| `/healthz` | GET | — | Liveness probe (always 200) |
| `/ready` | GET | — | Readiness probe (checks Redis + DynamoDB + agents) |
| `/health` | GET | — | Legacy alias for `/healthz` |
| `/query` | POST | `X-Internal-Token` | Single query (optional `mode=investigate`) |
| `/alerts/incoming` | POST | `X-Internal-Token` | Alertmanager webhook → auto-RCA |
| `/v1/models` | GET | Bearer | OpenAI-compatible model list |
| `/v1/chat/completions` | POST | Bearer | OpenAI-compatible chat (LibreChat) |

## Data stores

| Store | Purpose | Persistence |
|-------|---------|-------------|
| Redis | Datasource cache (per-agent TTL 1–60 min) | Ephemeral |
| PostgreSQL + pgvector | KB items + RAG embeddings | Persistent |
| DynamoDB | Conversation history (TTL 24h) | TTL-based |
