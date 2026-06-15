# Architecture

## Overview

Single-process supervisor with N agents loaded from config at startup.

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
│                  Supervisor (:8000)                        │
│                                                          │
│  ┌────────────┐  ┌────────────────┐  ┌──────────────┐   │
│  │ Classifier │  │ AgentRegistry  │  │ Fan-out/RCA  │   │
│  │ (routing)  │  │ (auto-discover)│  │ (orchestr.)  │   │
│  └────────────┘  └────────────────┘  └──────────────┘   │
│                                                          │
│  ┌─────┐ ┌─────┐ ┌───────┐ ┌───────┐ ┌─────┐ ┌─────┐  │
│  │ AWS │ │ K8s │ │FinOps │ │DevOps │ │ Obs │ │ Sec │  │
│  └──┬──┘ └──┬──┘ └───┬───┘ └───┬───┘ └──┬──┘ └──┬──┘  │
│     │       │        │         │        │       │      │
│  ┌──┴───────┴────────┴─────────┴────────┴───────┴──┐   │
│  │           DatasourceAdapter Layer                 │   │
│  │  Boto3 | Kubernetes | Http | Athena              │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
         │          │           │            │
         ▼          ▼           ▼            ▼
    ┌────────┐ ┌────────┐ ┌─────────┐ ┌──────────────┐
    │ Redis  │ │Postgres│ │DynamoDB │ │ OTel Collect.│
    │ cache  │ │pgvector│ │ Local   │ │  → Tempo     │
    │        │ │  (KB)  │ │(history)│ │  → Prometheus│
    └────────┘ └────────┘ └─────────┘ └──────────────┘
```

## Key design decisions

| Decision | Rationale |
|----------|-----------|
| Single image, in-process agents | Eliminates inter-container HTTP; simpler deploy; lower resource use |
| Config-driven agents (`agent.yaml + prompt.md`) | Zero-code agent creation; hot-reload; git-based management |
| DatasourceAdapter pattern | Decouples data fetching from agent logic; enforces read-only |
| Postgres+pgvector for KB | Self-contained RAG without external managed services |

## Core components

### AgentRegistry (spec 22)
Auto-discovers agents from `agents/` directories at startup. Each agent has:
- `agent.yaml` — config (datasources, routing keywords, model tier, cache TTL)
- `prompt.md` — system prompt

### Classifier
Routes user queries to the best-fit agent using LLM classification + keyword fast-path.

### Fan-out (spec 17)
Multi-agent parallel execution when a query needs N≥2 agents. Results synthesized by LLM.

### RCA Investigation (spec 18 Phase 1)
Structured investigation workflow: symptom → evidence collection (parallel) → RCA synthesis with confidence scoring.

### Knowledge Base (spec 21)
Learns from completed investigations. Distills RCA into KB items (Postgres+pgvector). Injects similar cases on new investigations via RAG.

## Data stores

| Store | Purpose | Persistence |
|-------|---------|-------------|
| Redis | Datasource cache (per-agent TTL) | Ephemeral |
| PostgreSQL+pgvector | KB items + RAG embeddings | Persistent |
| DynamoDB Local | Conversation history | TTL-based |

## Ports

| Port | Service |
|------|---------|
| 8000 | Supervisor API |
| 8006 | MCP Server |

All other ports are internal (no per-agent ports — agents run in-process).

## Specs reference

- **Spec 06** — Async resilience (circuit breaker, retry, timeout)
- **Spec 17** — Fan-out multi-agent orchestration
- **Spec 18** — RCA investigation workflow
- **Spec 21** — KB + RAG learning pipeline
- **Spec 22** — Config-driven agent loading
