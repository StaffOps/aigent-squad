# Agent Squad - Multi-Agent System for AWS/Kubernetes Operations

[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/StaffOps/aigent-squad/badge)](https://scorecard.dev/viewer/?uri=github.com/StaffOps/aigent-squad)

**Version**: 0.4.0
**Status**: ✅ Cluster-validated (devops-core, 2026-07) — see `specs/ROADMAP.md`
**Architecture**: Two-tier (edge gateway → supervisor), in-process specialists, Bedrock-direct

> **Real state**: stabilization (Phase 0) and hardening are done — the squad runs
> end-to-end in a real EKS cluster (gateway + supervisor, IRSA → Bedrock, Istio
> HTTPRoute) with defense-in-depth anti-prompt-injection (spec 14, L1–L6).
> Remaining work is tracked in [`specs/ROADMAP.md`](specs/ROADMAP.md) (authoritative)
> and [`HANDOFF.md`](HANDOFF.md) (session state). The original audit that seeded the
> spec backlog is kept as a historical record in [`specs/AUDIT.md`](specs/AUDIT.md).
> Work happens on the `dev` branch.

---

## 🎯 Overview

Agent Squad is a multi-agent system with 1 supervisor + 6 specialist agents for AWS/Kubernetes operations, designed for ChatOps integration with Slack and proactive monitoring.

**Key Features**:
- 🤖 Intelligent classifier-based routing
- 💬 Conversation history with context switching
- 🔍 RAG (Retrieval-Augmented Generation) support
- 📚 Agent skills (lazy-loaded markdown knowledge, shared across agents)
- 📊 OpenTelemetry distributed tracing
- 🔒 Read-only by default (current posture; execution is an open roadmap item, gated by guardrails + human-in-the-loop)
- 🚀 Kubernetes-native deployment
- 🔌 MCP integration (squad as server for Kiro + agents as **agentic** MCP clients — the LLM selects read-only tools+args via Bedrock Converse, spec 37)
- 🎚️ Complexity-aware model **pre-routing** (spec 38) — simple→Haiku, standard→Sonnet, complex→Opus 4.5, one-shot from the classifier; **live on all request paths** (auto-route/fan-out/force_agent/investigation)
- ✂️ Agentic **context-trimming** (spec 40) — keeps the last N tool-result turns verbatim + summarizes older, so deep multi-step queries don't exhaust the token budget
- 🧭 **Self-service** posture — never suggests `kubectl`; the agent fetches data itself or points to the specific Grafana/ArgoCD dashboard
- 🧠 Collapsible **Thinking** trace (LibreChat `<think>`) with model narration + routing focus
- 🤖 OpenAI-compatible API (`/v1`) — plugs into LibreChat or any OpenAI client ([docs](docs/LIBRECHAT.md))
- 💲 Per-agent Bedrock cost attribution (Application Inference Profiles + token metrics)

---

## 🏗️ Architecture

### System Architecture (two-tier, spec 31)

```
        Clients: LibreChat (/v1) · curl (/query) · Alertmanager · MCP Server (8006)
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       EDGE GATEWAY (:8000, public)                       │
│   edge auth · rate limit + daily budget (Redis) · WorkerPool             │
│   backpressure (503 + Retry-After) · OpenAI /v1 shaping · /jobs cancel   │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ POST /internal/process (SUPERVISOR_INTERNAL_TOKEN)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SUPERVISOR (:8001, backend-only)                      │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  InputScanner (L2) → Classifier (Bedrock Haiku) → routes 1–3     │  │
│  │  agents · fan-out (asyncio.gather) → synthesizer · RCA           │  │
│  │  investigation · Bedrock Guardrail (fail-closed 403) ·           │  │
│  │  canary tokens + output filter · per-agent history (DynamoDB)    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│   In-process specialists (GenericAgent, config-driven, NO ports):       │
│      aws · kubernetes · finops · devops · observability                 │
│   Each: agents/<name>/agent.yaml + prompt.md → datasource adapters      │
│   (boto3, kubernetes, http, athena, mcp) + lazy skills                  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Shared Services                                  │
│  Bedrock (Haiku classifier / Sonnet agents, prompt caching, AIP cost    │
│  attribution) · DynamoDB (history, 24h TTL) · Redis (datasource cache,  │
│  rate/budget counters) · PostgreSQL+pgvector (incident-memory KB)       │
└─────────────────────────────────────────────────────────────────────────┘
```

Specialists are **in-process** (spec 02/22): the supervisor instantiates
`GenericAgent` per config directory — there are no per-agent HTTP services or
ports. Adding an agent = adding `agents/<name>/agent.yaml` + `prompt.md`
(zero code, auto-discovered at startup).

### Agent Communication Flow

```
User Query: "How many EC2 instances are running?"
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│ Supervisor                                                   │
│                                                              │
│  Step 1: Classifier Analysis                                │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Input: "How many EC2 instances are running?"          │ │
│  │ History: [previous conversation context]              │ │
│  │                                                        │ │
│  │ Classifier (Claude Sonnet):                           │ │
│  │  - Analyzes intent                                    │ │
│  │  - Detects follow-ups                                 │ │
│  │  - Considers context                                  │ │
│  │                                                        │ │
│  │ Output:                                               │ │
│  │  - selected_agent: "aws"                              │ │
│  │  - confidence: 0.95                                   │ │
│  │  - reasoning: "User asking about EC2 instances"       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 2: Fetch Conversation History                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ DynamoDB Query:                                        │ │
│  │  - Global history (for classifier)                    │ │
│  │  - Agent-specific history (for AWS agent)             │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 3: Route to Agent (in-process)                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ await generic_agent.process_request(                   │ │
│  │   input_text="How many EC2 instances...",              │ │
│  │   user_id="user123",                                   │ │
│  │   session_id="session456",                             │ │
│  │   chat_history=[...],                                  │ │
│  │ )   # 1–3 agents in parallel when cross-domain         │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ AWS Agent (GenericAgent, in-process)                        │
│                                                              │
│  Step 1: Check Cache                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Redis GET "ec2:instances:us-east-1"                    │ │
│  │ TTL: 5 minutes                                         │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 2: Fetch Real Data (if cache miss)                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ AWS API: ec2.describe_instances()                      │ │
│  │ Result: 640 instances                                  │ │
│  │ Cache result in Redis                                  │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 3: RAG Enhancement (optional)                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Bedrock Knowledge Base Query:                          │ │
│  │  - Search: "EC2 best practices"                        │ │
│  │  - Returns: historical context, recommendations       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 4: Generate Response                                  │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Bedrock Claude Sonnet 4.5:                             │ │
│  │  - System prompt: AWS Senior Principal Engineer       │ │
│  │  - Context: 640 instances + RAG data                  │ │
│  │  - Generate: detailed analysis + recommendations      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Response: "Found 640 EC2 instances in us-east-1..."       │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Supervisor                                                   │
│                                                              │
│  Step 4: Save to History                                    │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ DynamoDB PUT:                                          │ │
│  │  - User message                                        │ │
│  │  - Agent response                                      │ │
│  │  - Metadata (agent, confidence, timestamp)            │ │
│  │  - TTL: 24 hours                                       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  Step 5: Return to User                                     │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
                    User receives response
```

### Production Deployment (EKS)

> ✅ **Validated deploy (devops-core, 2026-07)**: the real cluster runs the
> **inProcess topology** — `gateway` + `supervisor` Deployments only (specialists
> in-process), Istio Gateway API HTTPRoute, IRSA → Bedrock, DynamoDB, Bedrock
> Guardrail, Redis StatefulSet, agents from ConfigMap or git. The diagram below
> shows the **aspirational distributed topology** (per-agent pods) which the
> chart can render but the supervisor does not yet support (see
> `specs/ROADMAP.md` backlog — deliberately deferred per ADR-001).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                                   │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                         EKS Cluster                                 │ │
│  │                                                                     │ │
│  │  ┌─────────────────────────────────────────────────────────────┐  │ │
│  │  │  Namespace: agent-squad-prod                                │  │ │
│  │  │                                                              │  │ │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │  │ │
│  │  │  │  Supervisor  │  │  AWS Agent   │  │  K8s Agent   │     │  │ │
│  │  │  │  (2-10 pods) │  │  (3-15 pods) │  │  (2-10 pods) │     │  │ │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘     │  │ │
│  │  │                                                              │  │ │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │  │ │
│  │  │  │ FinOps Agent │  │ DevOps Agent │  │  Obs Agent   │     │  │ │
│  │  │  │  (1-5 pods)  │  │  (1-5 pods)  │  │  (2-8 pods)  │     │  │ │
│  │  │  └──────────────┘  └──────────────┘  └──────────────┘     │  │ │
│  │  │                                                              │  │ │
│  │  │  ┌──────────────┐  ┌──────────────────────────────────┐   │  │ │
│  │  │  │  MCP Server  │  │      OpenSearch Cluster          │   │  │ │
│  │  │  │  (2-5 pods)  │  │  (Vector store for Knowledge     │   │  │ │
│  │  │  └──────────────┘  │   Bases - 3 nodes)               │   │  │ │
│  │  │                    └──────────────────────────────────┘   │  │ │
│  │  │                                                              │  │ │
│  │  │  ┌──────────────────────────────────────────────────────┐  │  │ │
│  │  │  │              Ingress Controller                       │  │ │ │
│  │  │  │  - /api/supervisor → Supervisor                       │  │ │ │
│  │  │  │  - /api/mcp → MCP Server                              │  │ │ │
│  │  │  └──────────────────────────────────────────────────────┘  │  │ │
│  │  └─────────────────────────────────────────────────────────────┘  │ │
│  │                                                                     │ │
│  │  ┌─────────────────────────────────────────────────────────────┐  │ │
│  │  │  Namespace: monitoring                                      │  │ │
│  │  │  - Prometheus                                               │  │ │
│  │  │  - Grafana                                                  │  │ │
│  │  │  - OpenTelemetry Collector                                 │  │ │
│  │  └─────────────────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      AWS Managed Services                           │ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐│ │
│  │  │  DynamoDB    │  │ ElastiCache  │  │   Bedrock                ││ │
│  │  │  (Sessions)  │  │    Redis     │  │   - Claude Sonnet 4.5    ││ │
│  │  │              │  │  Serverless  │  │   - Knowledge Bases (5)  ││ │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘│ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐│ │
│  │  │     ECR      │  │      S3      │  │   Secrets Manager        ││ │
│  │  │  (Images)    │  │  (KB docs)   │  │   (Tokens, Keys)         ││ │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      External Integrations                          │ │
│  │                                                                     │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐│ │
│  │  │    Slack     │  │    GitLab    │  │   Cost Explorer          ││ │
│  │  │  (ChatOps)   │  │  (DevOps)    │  │   (FinOps)               ││ │
│  │  └──────────────┘  └──────────────┘  └──────────────────────────┘│ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### Components

**Edge Gateway (:8000, public — spec 31)**:
- Edge auth (`X-Internal-Token` or `GATEWAY_API_KEYS` allowlist, fail-closed)
- WorkerPool backpressure (local semaphore, `503 + Retry-After`)
- Global admission: per-user rate limit + daily budget (Redis, fail-open)
- Hosts `/query`, OpenAI `/v1/*`, `/jobs/{id}/cancel`
- Clients (LibreChat, MCP server, Alertmanager, anomaly-detection) hit the
  gateway, never the supervisor directly — see
  [`docs/site/architecture.md`](docs/site/architecture.md)

**Supervisor (:8001, backend-only)**:
- `/internal/process` + `/internal/agents`, gated by `SUPERVISOR_INTERNAL_TOKEN`
- Classifier routing (Bedrock Haiku), fan-out + synthesizer, RCA investigation
- Anti-prompt-injection defense-in-depth (spec 14, L1–L6): Bedrock Guardrail
  (fail-closed 403), InputScanner, context isolation, output filter, canary tokens
- Manages per-agent isolated conversation history (DynamoDB)

**Specialist Agents** (in-process `GenericAgent`, config-driven — no ports):
1. **aws**: EC2, RDS, S3, Lambda, VPC, IAM
2. **kubernetes**: Pods, nodes, deployments, services
3. **finops**: AWS costs (Cost Explorer), Kubecost, optimization
4. **devops**: CI/CD, GitLab, documentation
5. **observability**: Metrics, logs, anomalies, SIEM-like correlation

**Infrastructure**:
- **DynamoDB**: Conversation state (24h TTL)
- **Redis**: Datasource cache (1-60min TTL) + rate/budget counters + job lifecycle
- **Bedrock**: complexity-aware model tiering (spec 11 + **spec 38**) — Haiku classifier;
  agents **pre-routed** per query complexity (fast Haiku / standard Sonnet / deep Opus 4.5 one-shot,
  live on all paths); prompt caching; **context-trimming** (spec 40) to bound per-turn context;
  Application Inference Profiles for cost attribution
- **Knowledge Base**: incident-memory RAG via PostgreSQL+pgvector (spec 21). The Bedrock Knowledge Bases / OpenSearch path in the diagram above is an aspirational alternative, not the current implementation.

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- AWS CLI configured (`~/.aws` mounted read-only in dev)
- kubectl configured (optional — for the kubernetes agent)
- No local Python needed — everything (run, tests, lint) goes through Docker

### Local Development

```bash
# 1. Clone repository
git clone git@github.com:StaffOps/staffops-aigent-squad.git
cd staffops-aigent-squad

# 2. Configure environment
cp .env.example .env
# Edit .env with your AWS credentials and settings

# 3. Run setup script
./setup-local.sh

# 4. Verify services
curl http://localhost:8000/ready   # gateway (public front door)
```

**Services** (two-tier, spec 31):
- Gateway (public): http://localhost:8000  — `/query`, `/v1/*`, `/jobs/{id}/cancel`
- Supervisor (backend): http://localhost:8001  — `/internal/*` (gateway-only)
- MCP Server: http://localhost:8006  (→ gateway)
- Redis: localhost:6379
- DynamoDB Local: http://localhost:8100
- PostgreSQL (KB, optional): localhost:5432
- Prometheus: http://localhost:9099 · Grafana: http://localhost:3001

---

## 📖 Documentation

### Product & Decisions
- [`docs/prd/aigent-squad.md`](docs/prd/aigent-squad.md) - **PRD**: problem, personas, success metrics, scope (initiative level)
- [`docs/architecture/decisions/`](docs/architecture/decisions/README.md) - **ADRs** 0001–0006: Bedrock-direct, in-process agents, read-only posture, fail-closed vs fail-open, two-tier gateway, standalone product

### Specs & Planning (spec-driven — `specs/`)
- [`specs/README.md`](specs/README.md) - **How the spec process works** — lifecycle, status frontmatter (the SSOT), spec tiers (full spec vs `bugfix.md`), verification pipeline, conventions
- [`specs/ROADMAP.md`](specs/ROADMAP.md) - Phased plan + the single **canonical status table** (CI-validated by `scripts/specs_status.py`; status itself is authored in each spec's frontmatter)
- [`specs/BACKLOG.md`](specs/BACKLOG.md) - Live items: findings (`F-*`), product backlog (`B-*`), dormant work, deferred register
- [`specs/VISION.md`](specs/VISION.md) - Long-term maturity levels (autonomous multi-agent north star)
- [`specs/AUDIT.md`](specs/AUDIT.md) - Historical audit (2026-05-30) that seeded the spec backlog — findings since fixed (frozen)
- [`HANDOFF.md`](HANDOFF.md) - Current session + next steps (overwritten each session; prior sessions in `archive/handoffs/`)
- [`specs/01-fix-blockers/`](specs/01-fix-blockers/) - Unblock build and broken code
- [`specs/02-unify-agent-architecture/`](specs/02-unify-agent-architecture/) - Unify agents on the base pattern
- [`specs/03-fix-cache-observability/`](specs/03-fix-cache-observability/) - Deterministic cache + OTel
- [`specs/04-harden-security/`](specs/04-harden-security/) - Auth, non-root, prompt injection
- [`specs/05-helm-chart/`](specs/05-helm-chart/) - Helm chart for EKS (Phase 2 — deploy)
- [`specs/26-agent-skills/`](specs/26-agent-skills/) - Skills: per-agent lazy-loaded markdown knowledge
- [`specs/27-bedrock-cost-attribution/`](specs/27-bedrock-cost-attribution/) - Bedrock cost attribution (AIP per model + per-agent showback)
- [`specs/ADR-001-bedrock-direct-vs-strands.md`](specs/ADR-001-bedrock-direct-vs-strands.md) - Decision: Bedrock-direct vs. the Strands framework
- [`steering/project.md`](steering/project.md) - Project rules and invariants

### Getting Started
- [QUICKSTART.md](QUICKSTART.md) - Setup in 3 steps
- [CHANGES.md](CHANGES.md) - Changelog
- [archive/IMPLEMENTATION_HISTORY.md](archive/IMPLEMENTATION_HISTORY.md) - Historical roadmap (v2.0, Phases 1-13)

### Technical Docs
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture
- [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) - MCP: squad as server (Kiro) + agents as clients (`type: mcp`)
- [docs/LIBRECHAT.md](docs/LIBRECHAT.md) - LibreChat integration via the OpenAI-compatible bridge (`/v1`)
- [docs/HOW-TO-NEW-AGENT.md](docs/HOW-TO-NEW-AGENT.md) - Create an agent (datasources, skills, MCP)
- [docs/KNOWLEDGE-BASE.md](docs/KNOWLEDGE-BASE.md) - KB / RAG (pgvector)
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) - Logging & tracing
- [docs/METRICS.md](docs/METRICS.md) - Custom metrics catalog
- [docs/ALERTING.md](docs/ALERTING.md) - Alertmanager ingestion + Slack post-back
- [docs/READ_ONLY_POLICY.md](docs/READ_ONLY_POLICY.md) - Read-only policy (4 layers)
- [docs/SECURITY.md](docs/SECURITY.md) - Security model
- [docs/SETUP.md](docs/SETUP.md) - Local setup
- [docs/PREREQUISITES.md](docs/PREREQUISITES.md) - Infrastructure requirements
- [docs/COMPETITIVE-ANALYSIS.md](docs/COMPETITIVE-ANALYSIS.md) - Comparison vs. AI SRE agents (Aurora, OpenSRE, etc.) + positioning

### Infrastructure (Terraform)
- [infra/terraform/README.md](infra/terraform/README.md) - AWS modules (IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP)
- [infra/terraform/bedrock-aip/README.md](infra/terraform/bedrock-aip/README.md) - Application Inference Profiles + cost attribution

### Agents
Agents are config-driven (`agent.yaml` + `prompt.md`) under [`agents/`](agents/) — see [docs/HOW-TO-NEW-AGENT.md](docs/HOW-TO-NEW-AGENT.md). Supervisor internals: [src/supervisor/README.md](src/supervisor/README.md).

### AI tooling (tool-agnostic)
Repo guidance is **tool-neutral**: [`AGENTS.md`](AGENTS.md) is the canonical guide
that any AI coding assistant reads (Claude Code, Cursor, Copilot, Aider, …), with
detailed rules in [`steering/`](steering/) and plans in [`specs/`](specs/).
Tool-specific files are thin pointers — [`CLAUDE.md`](CLAUDE.md) is just
`See @AGENTS.md`. Per-tool local dirs (`.claude/`, `.cursor/`, …) are git-ignored.

---

## 🧪 Testing

### Test the gateway
```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{
    "user_input": "How many EC2 instances are running?",
    "user_id": "test-user",
    "session_id": "test-session"
  }'
```

### Test a specific agent (OpenAI bridge, bypasses the classifier)
```bash
# List available models (aigent-squad + aigent-squad-<agent> per agent)
curl -H 'X-Internal-Token: dev-secret-token' http://localhost:8000/v1/models

curl -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Internal-Token: dev-secret-token' \
  -d '{
    "model": "aigent-squad-aws",
    "messages": [{"role": "user", "content": "List EC2 instances"}]
  }'
```

> Agents are in-process — there is no per-agent port. Use the gateway's
> `aigent-squad-<agent>` model to force a specific specialist.

---

## 🔒 Security

### Read-Only Policy (4 Layers) — current posture

Read-only is today's operating mode (not a permanent lock — executing actions is
an open roadmap item, conditional on the spec 14 guardrails + human-in-the-loop).
See [docs/READ_ONLY_POLICY.md](docs/READ_ONLY_POLICY.md) and
[specs/14-security-hardening/](specs/14-security-hardening/).

1. **System Prompts**: Explicit read-only instructions
2. **IAM Policies**: Explicit deny on write operations
3. **K8s RBAC**: Only get, list, watch verbs
4. **Response Templates**: Suggest automation instead of manual changes

### IAM Policy Example
```json
{
  "Effect": "Deny",
  "Action": [
    "*:Create*", "*:Delete*", "*:Update*",
    "*:Put*", "*:Modify*", "*:Terminate*"
  ],
  "Resource": "*"
}
```

---

## 💰 Cost Estimates

### Basic (No RAG)
- DynamoDB: $5-15/month
- Redis: $20-40/month
- Bedrock: $5-15/month
- EKS: $10-30/month
- **Total**: $40-100/month

### With RAG (5 Knowledge Bases)
- Bedrock: $50-150/month
- Knowledge Bases: $500-3750/month
- OpenSearch: $700-1500/month
- **Total**: $1327-5595/month

### With Advanced Features (Phases 8-13)
- Cohere Rerank: $50-200/month
- ML Models: $100-500/month
- **Total**: $950-3700/month

**Strategy**: Start basic, add RAG incrementally

---

## 🛠️ Technology Stack

- **Language**: Python 3.11 (Alpine runtime image; `python:3.11-slim` for tests)
- **Framework**: FastAPI (gateway + supervisor)
- **LLM**: AWS Bedrock (Claude) — model tiering per role (spec 11): `BEDROCK_CLASSIFIER_MODEL_ID` (Haiku) / `BEDROCK_MODEL_ID` (Sonnet), resolved in `src/core/model_tier.py`, config-driven (see `src/core/config.py`)
- **State**: DynamoDB
- **Cache**: Redis
- **Observability**: OpenTelemetry + JSON logging
- **Deployment**: Docker + Kubernetes (EKS)
- **CI/CD**: GitHub Actions (`.github/workflows/`)

---

## 📊 Roadmap

> **Authoritative roadmap**: [`specs/ROADMAP.md`](specs/ROADMAP.md)
> (spec-numbered, phase-based, reflects the real state). The phase list below is
> the original aspirational outline, kept for reference — where the two differ,
> ROADMAP.md wins.

### Phase 1: Testing & Validation (1-2 days)
- Test all agents with 10+ questions each
- Validate conversation history
- Optimize Kubernetes Agent timeout

### Phase 2: RAG & Knowledge Bases (2-3 days)
- Create 5 Bedrock Knowledge Bases
- Enable RAG in all agents

### Phase 3: Production Deploy (3-5 days)
- Terraform infrastructure — ✅ modules ready (IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP); see [infra/terraform/](infra/terraform/)
- CI/CD pipeline (GitHub Actions)
- EKS deployment

### Phase 4: Slack Integration (2-3 days)
- Slack App setup
- Event handlers
- Interactive buttons

### Phase 5: Proactive Agents (2-3 days)
- CronJobs for monitoring
- Intelligent alerts

### Phases 6-13: Advanced Features
- Observability MCP Servers
- Advanced RAG (hybrid search, re-ranking)
- Long-term memory
- Continuous learning
- Enriched context (Confluence, Jira, GitHub)
- Predictive analysis
- Multi-modal (screenshots, diagrams)

**See**: [archive/IMPLEMENTATION_HISTORY.md](archive/IMPLEMENTATION_HISTORY.md) for complete roadmap

---

## 🤝 Contributing

This is a reference implementation based on AWS Labs Agent Squad best practices.

**Key Principles**:
- Classifier-based routing (not manual)
- Sorted conversation contexts (global + isolated)
- Standardized agent interface
- Read-only by default (execution = future, gated)
- Production-grade observability

**Workflow**: work on `dev` (every push runs lint + tests + coverage ≥90% +
dep/SAST scans); release via PR `dev → main` (scan-gated image publish) and a
`vX.Y.Z` tag. See [docs/site/CI-CD.md](docs/site/CI-CD.md) for the full pipeline and
versioning model.

---

## 📚 References

- **AWS Labs Agent Squad**: https://github.com/awslabs/agent-squad
- **AWS Bedrock**: https://aws.amazon.com/bedrock/
- **Model Context Protocol**: https://modelcontextprotocol.io/

---

## 📝 License

Apache 2.0 — See [LICENSE](LICENSE) for details.

---

**Last Updated**: 2026-07-16
**Version**: 0.4.0
**Status**: ✅ Cluster-validated (devops-core) — see `specs/ROADMAP.md` for what's next
