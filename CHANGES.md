# Changelog

## [Unreleased] - 2026-06-14

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
- Tightened `.kiro/steering/milestone-criteria.md`: operational docs (ARCHITECTURE/SETUP/SECURITY/etc) now listed as mandatory milestone gate; new anti-pattern: "stale docs are worse than no docs"

### Added (Metrics audit — covering specs 06, 17, 18, 21)
13 new custom metrics + instrumentation in existing code:
- Spec 06: `aigent.circuit_breaker.transitions`
- Spec 17: `aigent.fanout.calls`, `aigent.fanout.agents_consulted`, `aigent.fanout.agents_failed`, `aigent.synthesizer.calls`
- Spec 18: `aigent.investigation.started`, `.completed`, `.duration`, `.evidence_count`
- Spec 21: `aigent.kb.distillation.cost`, `.items_created`, `.rag.queries`, `.rag.hits`, `.budget.exhausted`
- Updated `docs/METRICS.md` with full reference (table per domain + label cardinality)
- Updated `.kiro/steering/milestone-criteria.md` to make metrics a mandatory milestone gate (equal weight to tests/docs)

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
