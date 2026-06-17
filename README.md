# Agent Squad - Multi-Agent System for AWS/Kubernetes Operations

**Version**: 0.x (pre-release)
**Status**: 🚧 Stabilizing — see `.kiro/specs/ROADMAP.md`
**Architecture**: AWS Labs Best Practices

> ⚠️ **Real state**: this project is in **Phase 0 (stabilization)**, not in production. There are known blockers (build, architecture, security, tests) documented in the audit at [`.kiro/specs/AUDIT.md`](.kiro/specs/AUDIT.md). The work plan is in [`.kiro/specs/ROADMAP.md`](.kiro/specs/ROADMAP.md). Work happens on the `dev` branch.

---

## 🎯 Overview

Agent Squad is a multi-agent system with 1 supervisor + 5 specialist agents for AWS/Kubernetes operations, designed for ChatOps integration with Slack and proactive monitoring.

**Key Features**:
- 🤖 Intelligent classifier-based routing
- 💬 Conversation history with context switching
- 🔍 RAG (Retrieval-Augmented Generation) support
- 📚 Agent skills (lazy-loaded markdown knowledge, shared across agents)
- 📊 OpenTelemetry distributed tracing
- 🔒 Read-only by default (current posture; execution is an open roadmap item, gated by guardrails + human-in-the-loop)
- 🚀 Kubernetes-native deployment
- 🔌 MCP integration (squad as server for Kiro + agents as MCP clients)
- 💲 Per-agent Bedrock cost attribution (Application Inference Profiles + token metrics)

---

## 🏗️ Architecture

### System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              User Interface                              │
│                    (Slack / Kiro CLI / HTTP API)                        │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          MCP Server (8006)                               │
│                    HTTP API for external clients                         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Supervisor (8000)                                │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  1. Receives user query                                          │  │
│  │  2. Classifier analyzes intent → selects agent                   │  │
│  │  3. Fetches conversation history (DynamoDB)                      │  │
│  │  4. Routes to specialist agent via HTTP                          │  │
│  │  5. Saves response to history                                    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
        ┌────────▼─────┐  ┌─────▼──────┐  ┌────▼─────────┐
        │ AWS Agent    │  │ K8s Agent  │  │ FinOps Agent │
        │   (8001)     │  │   (8002)   │  │    (8003)    │
        └──────────────┘  └────────────┘  └──────────────┘
                 │               │               │
        ┌────────▼─────┐  ┌─────▼──────────────────────┐
        │ DevOps Agent │  │ Observability Agent        │
        │   (8004)     │  │      (8005)                │
        └──────────────┘  └────────────────────────────┘
                 │
                 │  All agents use:
                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Shared Services                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │
│  │   Bedrock    │  │  DynamoDB    │  │    Redis     │                 │
│  │ Claude Sonnet│  │ Conversation │  │    Cache     │                 │
│  │   Sonnet     │  │   History    │  │  (1-60min)   │                 │
│  └──────────────┘  └──────────────┘  └──────────────┘                 │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │              Bedrock Knowledge Bases (RAG)                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│  │
│  │  │  FinOps  │ │  DevOps  │ │   AWS    │ │   K8s    │ │  Obs   ││  │
│  │  │    KB    │ │    KB    │ │    KB    │ │    KB    │ │   KB   ││  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘│  │
│  │                    (OpenSearch in EKS)                           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

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
│  Step 3: Route to Agent                                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ HTTP POST http://aws-agent:8001/process               │ │
│  │ {                                                      │ │
│  │   "input_text": "How many EC2 instances...",          │ │
│  │   "user_id": "user123",                               │ │
│  │   "session_id": "session456",                         │ │
│  │   "chat_history": [...]                               │ │
│  │ }                                                      │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ AWS Agent (8001)                                            │
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

**Supervisor (Port 8000)**:
- Orchestrates all specialist agents
- Intelligent routing via classifier
- Manages conversation history
- Slack integration (optional)

**Specialist Agents**:
1. **AWS Agent (8001)**: EC2, RDS, S3, Lambda, VPC, IAM
2. **Kubernetes Agent (8002)**: Pods, nodes, deployments, services
3. **FinOps Agent (8003)**: AWS costs, Kubecost, optimization
4. **DevOps Agent (8004)**: CI/CD, GitLab, documentation
5. **Observability Agent (8005)**: Metrics, logs, anomalies, SIEM-like correlation

**Infrastructure**:
- **DynamoDB**: Conversation state (24h TTL)
- **Redis**: Cache for inventories (1-60min TTL)
- **Bedrock**: Claude Sonnet 4.5 LLM (via `us.` inference profile)
- **Knowledge Base**: incident-memory RAG via PostgreSQL+pgvector (spec 21). The Bedrock Knowledge Bases / OpenSearch path in the diagram above is an aspirational alternative, not the current implementation.

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- AWS CLI configured
- kubectl configured (for Kubernetes Agent)
- Python 3.12+

### Local Development

```bash
# 1. Clone repository
git clone git@github.com:karlipegomes/staffops-aigent-squad.git
cd staffops-aigent-squad

# 2. Configure environment
cp .env.example .env
# Edit .env with your AWS credentials and settings

# 3. Run setup script
./setup-local.sh

# 4. Verify services
curl http://localhost:8000/health
```

**Services**:
- Supervisor: http://localhost:8000
- AWS Agent: http://localhost:8001
- Kubernetes Agent: http://localhost:8002
- FinOps Agent: http://localhost:8003
- DevOps Agent: http://localhost:8004
- Observability Agent: http://localhost:8005
- MCP Server: http://localhost:8006
- Redis: localhost:6379
- DynamoDB Local: http://localhost:8100

---

## 📖 Documentation

### Specs & Planning (spec-driven — `.kiro/`)
- [`.kiro/specs/AUDIT.md`](.kiro/specs/AUDIT.md) - Audit of the real state (findings + severity)
- [`.kiro/specs/ROADMAP.md`](.kiro/specs/ROADMAP.md) - Roadmap by phases (Phase 0 = stabilization)
- [`.kiro/specs/01-fix-blockers/`](.kiro/specs/01-fix-blockers/) - Unblock build and broken code
- [`.kiro/specs/02-unify-agent-architecture/`](.kiro/specs/02-unify-agent-architecture/) - Unify agents on the base pattern
- [`.kiro/specs/03-fix-cache-observability/`](.kiro/specs/03-fix-cache-observability/) - Deterministic cache + OTel
- [`.kiro/specs/04-harden-security/`](.kiro/specs/04-harden-security/) - Auth, non-root, prompt injection
- [`.kiro/specs/05-helm-chart/`](.kiro/specs/05-helm-chart/) - Helm chart for EKS (Phase 2 — deploy)
- [`.kiro/specs/26-agent-skills/`](.kiro/specs/26-agent-skills/) - Skills: per-agent lazy-loaded markdown knowledge
- [`.kiro/specs/27-bedrock-cost-attribution/`](.kiro/specs/27-bedrock-cost-attribution/) - Bedrock cost attribution (AIP per model + per-agent showback)
- [`.kiro/specs/ADR-001-bedrock-direct-vs-strands.md`](.kiro/specs/ADR-001-bedrock-direct-vs-strands.md) - Decision: Bedrock-direct vs. the Strands framework
- [`.kiro/steering/project.md`](.kiro/steering/project.md) - Project rules and invariants

### Getting Started
- [QUICKSTART.md](QUICKSTART.md) - Setup in 3 steps
- [IMPLEMENTATION_HISTORY.md](IMPLEMENTATION_HISTORY.md) - Complete roadmap (Phases 1-13)
- [VERSIONS.md](VERSIONS.md) - Package versions
- [CHANGES.md](CHANGES.md) - Changelog

### Technical Docs
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture
- [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md) - MCP: squad as server (Kiro) + agents as clients (`type: mcp`)
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
- [terraform/README.md](terraform/README.md) - AWS modules (IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP)
- [terraform/bedrock-aip/README.md](terraform/bedrock-aip/README.md) - Application Inference Profiles + cost attribution

### Agents
Agents are config-driven (`agent.yaml` + `prompt.md`) under [`agents/`](agents/) — see [docs/HOW-TO-NEW-AGENT.md](docs/HOW-TO-NEW-AGENT.md). Supervisor internals: [src/supervisor/README.md](src/supervisor/README.md).

### AI tooling (Kiro CLI + Claude Code)
The project is spec-driven with `.kiro/` as the single source of truth. It also
works with **Claude Code**: [`CLAUDE.md`](CLAUDE.md) is the entrypoint and the
[`.claude/`](.claude/) directory mirrors `.kiro/` (rules/skills via symlink, agents
converted). Regenerate with `./scripts/sync-claude.sh` after changing steering,
skills, or agent definitions. See [`.claude/README.md`](.claude/README.md).

---

## 🧪 Testing

### Test Supervisor
```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{
    "user_input": "How many EC2 instances are running?",
    "user_id": "test-user",
    "session_id": "test-session"
  }'
```

### Test Individual Agent
```bash
curl -X POST http://localhost:8001/process \
  -H 'Content-Type: application/json' \
  -d '{
    "input_text": "List EC2 instances",
    "user_id": "test-user",
    "session_id": "test-session",
    "chat_history": []
  }'
```

---

## 🔒 Security

### Read-Only Policy (4 Layers) — current posture

Read-only is today's operating mode (not a permanent lock — executing actions is
an open roadmap item, conditional on the spec 14 guardrails + human-in-the-loop).
See [docs/READ_ONLY_POLICY.md](docs/READ_ONLY_POLICY.md) and
[.kiro/specs/14-security-hardening/](.kiro/specs/14-security-hardening/).

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

- **Language**: Python 3.12
- **Framework**: FastAPI
- **LLM**: AWS Bedrock (Claude) — single model via env `BEDROCK_MODEL_ID` (see `src/core/config.py`)
- **State**: DynamoDB
- **Cache**: Redis
- **Observability**: OpenTelemetry + JSON logging
- **Deployment**: Docker + Kubernetes (EKS)
- **CI/CD**: GitHub Actions (`.github/workflows/`)

---

## 📊 Roadmap

> **Authoritative roadmap**: [`.kiro/specs/ROADMAP.md`](.kiro/specs/ROADMAP.md)
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
- Terraform infrastructure — ✅ modules ready (IAM/IRSA, DynamoDB, Bedrock endpoints, cost AIP); see [terraform/](terraform/)
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

**See**: [IMPLEMENTATION_HISTORY.md](IMPLEMENTATION_HISTORY.md) for complete roadmap

---

## 🤝 Contributing

This is a reference implementation based on AWS Labs Agent Squad best practices.

**Key Principles**:
- Classifier-based routing (not manual)
- Sorted conversation contexts (global + isolated)
- Standardized agent interface
- Read-only by default (execution = future, gated)
- Production-grade observability

---

## 📚 References

- **AWS Labs Agent Squad**: https://github.com/awslabs/agent-squad
- **AWS Bedrock**: https://aws.amazon.com/bedrock/
- **Model Context Protocol**: https://modelcontextprotocol.io/

---

## 📝 License

MIT License - See LICENSE file for details

---

**Last Updated**: 2026-06-17
**Version**: 0.x (pre-release)
**Status**: 🚧 Stabilizing (Phase 0) — see `.kiro/specs/ROADMAP.md`
