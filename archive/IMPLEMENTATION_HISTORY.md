# Agent Squad - Implementation History and Next Steps

> ⚠️ **HISTORICAL (frozen 2026-02-14).** Describes an early architecture (5 agents, supervisor-only,
> pre-gateway). The current system is gateway + supervisor, 6 agents, agentic tool-calling (spec 37+),
> tier routing (spec 38), MCP-bound observability/kubernetes. For current state see `CHANGES.md`,
> `specs/ROADMAP.md`, and `HANDOFF.md`. Kept for historical context only.

**Date**: February 14, 2026  
**Version**: 2.0  
**Status**: ✅ 100% Operational Locally

---

## ✅ WHAT WAS IMPLEMENTED

### 1. Base Architecture (v2.0 - AWS Labs Best Practices)

#### Intelligent Classifier
- ✅ Automatic routing based on intent
- ✅ Follow-up detection ("yes", "ok", "1")
- ✅ Intelligent context switching
- ✅ Confidence scoring + reasoning
- **File**: `src/core/classifier.py`

#### Storage with Separated Contexts
- ✅ `fetch_all_chats()` - Global history for classifier
- ✅ `fetch_chat(agent_id)` - Isolated history per agent
- ✅ DynamoDB with correct schema (pk + sk)
- ✅ 24-hour TTL
- **File**: `src/core/state_store.py`

#### Standardized Interface
- ✅ Base class `Agent` with `process_request()` method
- ✅ All 5 agents using unified interface
- ✅ Request: `input_text`, `user_id`, `session_id`, `chat_history`
- ✅ Response: `ConversationMessage` with role, content, timestamp
- **File**: `src/core/agent_base.py`

### 2. Simplified Supervisor

- ✅ Uses Classifier (removed manual LangGraph)
- ✅ ~100 lines vs 200+ before
- ✅ Manages conversation history automatically
- ✅ 25s timeout per agent
- ✅ Retry with exponential backoff
- ✅ Complete OpenTelemetry tracing
- **File**: `src/supervisor/agent.py`

### 3. Specialist Agents (5 of 5 Working)

#### AWS Agent (Port 8001) ✅
- ✅ 640 EC2 instances in us-east-1
- ✅ Integration with boto3
- ✅ Redis cache for inventories
- ✅ Senior Principal Engineer prompt (15+ years)
- **Test**: "How many EC2 instances are running?"

#### Kubernetes Agent (Port 8002) ✅
- ✅ 993 pods, 44 nodes, 28 namespaces
- ✅ AWS CLI installed for EKS authentication
- ✅ Fallback: in-cluster → local kubeconfig
- ✅ Redis cache for cluster state
- **Test**: "Show cluster pods"
- **Note**: Takes ~30s (timeout sometimes)

#### FinOps Agent (Port 8003) ✅
- ✅ $138,099.99 AWS cost (last 30 days)
- ✅ Cost Explorer API integration
- ✅ Athena for Kubecost
- ✅ **RAG implemented** (Bedrock Knowledge Base)
- **Test**: "What is the total AWS cost?"

#### DevOps Agent (Port 8004) ✅
- ✅ Company GitOps documentation
- ✅ GitLab client (full Company org access)
- ✅ Priority: devops-docs → devops/ → infrastructure/
- ✅ Recommends website (not GitLab) for docs
- **Test**: "How to deploy on EKS?"

#### Observability Agent (Port 8005) ✅
- ✅ Metrics and anomaly analysis
- ✅ Prometheus integration (when available)
- ✅ CloudWatch Logs Insights
- ✅ DNS/network problem detection
- **Test**: "Show system metrics"


### 4. Observability & Error Handling

#### JSON Structured Logging
- ✅ `python-json-logger` in all components
- ✅ Trace ID in all messages
- ✅ Levels: DEBUG, INFO, WARNING, ERROR
- **File**: `src/core/logger.py`

#### OpenTelemetry Distributed Tracing
- ✅ Spans for all operations
- ✅ Trace ID propagated between services
- ✅ Automatic instrumentation (FastAPI, httpx)
- ✅ Console exporter (logs)
- **Packages**: opentelemetry-api, opentelemetry-sdk, opentelemetry-instrumentation-*

#### Retry Logic
- ✅ Bedrock: 3 attempts, exponential backoff
- ✅ Supervisor: 2 HTTP attempts
- ✅ Timeout: 30s total (5s connect, 25s read)
- **File**: `src/core/bedrock.py`

#### Input Validation
- ✅ Non-empty strings
- ✅ Max 10k characters
- ✅ Input sanitization
- **All agents**

### 5. Configuration & Deploy

#### Local Docker Compose
- ✅ 9 services running (supervisor + 5 agents + mcp + redis + dynamodb)
- ✅ Health checks in all services
- ✅ Mounted volumes: ~/.aws, ~/.kube
- ✅ Isolated network
- ✅ Environment variables via .env
- **File**: `docker-compose.yaml`

#### Optimized Dockerfiles
- ✅ Python 3.12 Alpine (lightweight images)
- ✅ Multi-stage build (removed)
- ✅ Kubernetes Agent: AWS CLI installed
- ✅ All agents: same structure
- **Files**: `src/agents/*/Dockerfile`

#### Environment Variables
- ✅ `.env.example` complete
- ✅ `.env.local` with real values
- ✅ GitLab token configured
- ✅ Athena/Kubecost configured
- ✅ RAG configured (disabled by default)

#### Setup Script
- ✅ `setup-local.sh` with health checks
- ✅ Creates DynamoDB table automatically
- ✅ Checks dependencies (Docker, AWS CLI)
- ✅ Builds all images
- **File**: `setup-local.sh`

### 6. MCP Server (Kiro CLI Integration)

#### HTTP API
- ✅ Port 8006
- ✅ Endpoint: `POST /query`
- ✅ Request: `{"question": "...", "user_id": "..."}`
- ✅ Response: `{"agent": "...", "response": "...", "confidence": 0.95}`
- **File**: `mcp-server/mcp-server.py`

#### Kiro CLI Configuration
- ✅ `~/.kiro/mcp.json` created
- ✅ URL: `http://localhost:8006/query`
- ✅ Ready for use with `kiro-cli chat`
- **File**: `~/.kiro/mcp.json`

#### Documentation
- ✅ Complete setup
- ✅ Usage examples
- ✅ Troubleshooting
- **File**: `docs/MCP_INTEGRATION.md`

### 7. RAG (Retrieval-Augmented Generation)

#### FinOps Agent RAG
- ✅ RAG Client implemented
- ✅ Bedrock Knowledge Base integration
- ✅ Combines real data + historical context
- ✅ Graceful fallback if RAG fails
- ✅ Configurable via `RAG_ENABLED`
- **Files**: `src/core/rag_client.py`, `docs/RAG_IMPLEMENTATION.md`

### 8. Versions & Dependencies

#### LLM Model
- ✅ Claude 3.5 Sonnet v1 (`anthropic.claude-3-5-sonnet-20240620-v1:0`)
- ✅ Prompt caching disabled (compatibility)
- ✅ Max tokens: 4096
- ✅ Temperature: 0.7

#### Python Packages
- ✅ boto3==1.35.80
- ✅ fastapi==0.115.8
- ✅ uvicorn==0.32.1
- ✅ httpx==0.28.1
- ✅ kubernetes==31.0.0
- ✅ pydantic==2.10.5
- ✅ opentelemetry-*==1.29.0 / 0.50b0
- **File**: `requirements.txt`

### 9. Complete Documentation

#### Guides
- ✅ `README.md` - Overview
- ✅ `QUICKSTART.md` - 3-step setup
- ✅ `VERSIONS.md` - Package versions
- ✅ `CHANGES.md` - v2.0 summary

#### Technical Docs
- ✅ `docs/ARCHITECTURE.md` - Architecture v2.0
- ✅ `docs/MCP_INTEGRATION.md` - Kiro CLI
- ✅ `docs/OBSERVABILITY.md` - Logging & tracing
- ✅ `docs/RAG_IMPLEMENTATION.md` - RAG setup
- ✅ `docs/LOCAL_DEVELOPMENT.md` - Local dev

#### Agent READMEs
- ✅ `src/supervisor/README.md`
- ✅ `src/agents/aws/README.md`
- ✅ `src/agents/kubernetes/README.md`
- ✅ `src/agents/finops/README.md`
- ✅ `src/agents/devops/README.md`
- ✅ `src/agents/observability/README.md`

### 10. Organization & Cleanup

#### Clean Structure
- ✅ MCP Server in own directory (`mcp-server/`)
- ✅ Removed `STRUCTURE.md` (duplicate)
- ✅ Removed `server_new.py` (4 files)
- ✅ Removed unused templates (2 files)
- ✅ `.gitignore` created

#### Fixed Code
- ✅ Bedrock: `type: "text"` in system block
- ✅ Kubernetes: Fallback kubeconfig
- ✅ DevOps: Fixed syntax
- ✅ All agents: Standardized interface

---

## 🎯 CURRENT STATUS

### Running Services (9/9)
```
✅ Supervisor       (8000) - Healthy
✅ AWS Agent        (8001) - Healthy - 640 EC2 instances
✅ Kubernetes Agent (8002) - Healthy - 993 pods, 44 nodes
✅ FinOps Agent     (8003) - Healthy - $138k AWS cost
✅ DevOps Agent     (8004) - Healthy - EKS deploy docs
✅ Observability    (8005) - Healthy - Metrics & anomalies
✅ MCP Server       (8006) - Healthy - Kiro CLI ready
✅ Redis            (6379) - Healthy - Cache
✅ DynamoDB Local   (8100) - Healthy - Conversation history
```

### Tests Performed
- ✅ AWS Agent via Supervisor: 640 EC2 instances
- ✅ Kubernetes Agent via Supervisor: 993 pods (timeout sometimes)
- ✅ FinOps Agent via Supervisor: $138k cost
- ✅ DevOps Agent via Supervisor: EKS deploy
- ✅ Observability Agent via Supervisor: Metrics analysis
- ✅ Classifier routing correctly
- ✅ Conversation history working
- ✅ All health checks passing

---

## 📋 NEXT STEPS

### Phase 1: Testing & Validation (1-2 days)

#### 1.1 Complete Local Tests
- [ ] Test all 5 agents with 10+ questions each
- [ ] Validate conversation history (follow-ups)
- [ ] Test context switching between agents
- [ ] Validate timeout and retry logic
- [ ] Test MCP Server via Kiro CLI

#### 1.2 Performance & Optimization
- [ ] Measure latency per agent (p50, p95, p99)
- [ ] Optimize Kubernetes Agent (reduce timeout)
- [ ] Adjust cache TTL based on real usage
- [ ] Configure Bedrock prompt caching (if available)

#### 1.3 Observability
- [ ] Configure OTLP exporter (Jaeger/Tempo)
- [ ] Create Grafana dashboards (latency, errors, costs)
- [ ] Configure alerts (error rate > 5%)
- [ ] Test structured logs in production

### Phase 2: RAG & Knowledge Base (2-3 days)

#### 2.1 FinOps Knowledge Base
- [ ] Create Bedrock Knowledge Base in console
- [ ] Prepare documents (cost reports, best practices)
- [ ] Upload to S3 and sync
- [ ] Test retrieval in console
- [ ] Enable RAG in FinOps Agent (`RAG_ENABLED=true`)
- [ ] Validate responses with historical context

#### 2.2 DevOps Knowledge Base
- [ ] Create KB for GitLab documentation
- [ ] Index repos: devops-docs, infrastructure
- [ ] Implement RAG in DevOps Agent
- [ ] Test with internal process questions

#### 2.3 AWS Agent Knowledge Base
- [ ] Create KB for AWS best practices
- [ ] Index Well-Architected Framework
- [ ] Implement RAG in AWS Agent
- [ ] Test architecture recommendations

#### 2.4 Kubernetes Agent Knowledge Base
- [ ] Create KB for K8s troubleshooting
- [ ] Index internal cluster documentation
- [ ] Implement RAG in Kubernetes Agent
- [ ] Test problem diagnosis

#### 2.5 Observability Agent Knowledge Base ⚠️ CRITICAL
- [ ] **Create KB for anomaly detection**:
  - [ ] Historical incidents (last 6 months)
  - [ ] Runbooks and playbooks (troubleshooting)
  - [ ] Known anomaly patterns (CPU spike, memory leak, etc)
  - [ ] Root cause analysis from past incidents
  - [ ] Correlation between metrics and incidents
- [ ] Implement RAG in Observability Agent
- [ ] **Test anomaly detection with historical context**:
  - [ ] "High CPU in service X" → RAG finds similar incidents
  - [ ] "Memory leak in pod Y" → RAG suggests runbook
  - [ ] "Latency increased 300%" → RAG correlates with recent deploy

### Phase 3: Production Deploy (3-5 days)

#### 3.1 Infrastructure (Terraform)
- [ ] **Update Terraform with new resources**:
  - [ ] DynamoDB table (prod) with GSI for queries
  - [ ] ElastiCache Redis Serverless
  - [ ] IAM roles with IRSA (EKS) - 6 roles (supervisor + 5 agents)
  - [ ] ECR repositories (7 repos: supervisor + 5 agents + mcp)
  - [ ] **5 Bedrock Knowledge Bases** (FinOps, DevOps, AWS, K8s, Obs)
  - [ ] **S3 buckets for Knowledge Bases** (5 buckets)
  - [ ] **OpenSearch Serverless** (vector store for KBs)
  - [ ] VPC endpoints (Bedrock, DynamoDB, S3, ECR)
  - [ ] CloudWatch Log Groups (7 groups)
  - [ ] **Secrets Manager** (GitLab token, Slack token, etc)
  - [ ] **Parameter Store** (non-sensitive configs)

#### 3.2 CI/CD (GitLab)
- [ ] Configure `.gitlab-ci.yml`
- [ ] Automatic image build (7 images)
- [ ] Push to ECR
- [ ] Automatic deploy to EKS (ArgoCD)
- [ ] Integration tests in pipeline
- [ ] Post-deploy smoke tests

#### 3.3 Kubernetes (EKS)
- [ ] Create namespaces (agent-squad-prod, agent-squad-staging)
- [ ] Apply manifests (deployments, services, ingress)
- [ ] Configure HPA (2-10 replicas per agent)
- [ ] Configure PodDisruptionBudget
- [ ] Configure NetworkPolicies
- [ ] Configure ServiceMonitor (Prometheus)
- [ ] **Test all agents running on EKS**
- [ ] **Validate pod-to-pod communication**
- [ ] **Test health checks and readiness probes**

#### 3.4 Secrets & Config
- [ ] Create secrets in K8s (GitLab token, Slack token)
- [ ] Configure ConfigMaps (prompts, configs)
- [ ] Configure IRSA (IAM roles)
- [ ] Test permissions (Bedrock, DynamoDB, Cost Explorer)
- [ ] **Validate access to 5 Knowledge Bases**

### Phase 4: Slack Integration (2-3 days)

#### 4.1 Slack App Setup
- [ ] Create Slack App in workspace
- [ ] Configure Bot Token Scopes
- [ ] Configure Event Subscriptions
- [ ] Configure Slash Commands
- [ ] Install app in workspace

#### 4.2 Supervisor Slack Integration
- [ ] Implement `src/supervisor/slack.py`
- [ ] Configure event handlers (app_mention, message)
- [ ] Implement threading (responses in thread)
- [ ] Implement typing indicator
- [ ] Test in #ops-test channel

#### 4.3 Slack Features
- [ ] Interactive buttons (thumbs up/down)
- [ ] Slash commands (`/agent-squad ask`, `/agent-squad status`)
- [ ] Proactive alerts (CronJobs → Slack)
- [ ] Rich formatting (markdown, code blocks)

### Phase 5: Proactive Agents (2-3 days)

#### 5.1 Kubernetes CronJobs
- [ ] Daily Health Check (8am) - AWS + K8s + Obs
- [ ] Weekly Cost Report (Monday 9am) - FinOps
- [ ] Hourly Anomaly Detection - Observability
- [ ] Create manifests `k8s_manifests/cronjobs.yaml`

#### 5.2 Intelligent Alerts
- [ ] Detect cost spikes (>20% vs average)
- [ ] Detect crashlooping pods (restart > 5)
- [ ] Detect unhealthy nodes
- [ ] Detect high error rate (5xx > 5%)
- [ ] Post to Slack #ops-alerts


### Phase 6: Improvements & Features (Continuous)

#### 6.1 Observability Agent - Critical Improvements
- [ ] **Add MCP Servers for Observability**:
  - [ ] Prometheus MCP Server (real-time metrics)
  - [ ] Grafana MCP Server (dashboards and alerts)
  - [ ] CloudWatch MCP Server (AWS logs and metrics)
  - [ ] PagerDuty MCP Server (incidents and on-call)
  - [ ] Datadog MCP Server (APM and tracing)
- [ ] **Add team context**:
  - [ ] Team → services mapping (e.g., Team A owns service-x)
  - [ ] Team → K8s namespaces mapping
  - [ ] Team → on-call schedule mapping
  - [ ] Team → Slack channels mapping
- [ ] **Add additional information**:
  - [ ] SLOs per service (availability, latency, error rate)
  - [ ] Automatic runbooks (links to Confluence/Notion)
  - [ ] Incident history (last 30 days)
  - [ ] Service dependencies (service mesh)
  - [ ] Cost per service (FinOps integration)
- [ ] **RAG for Anomaly Detection** ⚠️ CRITICAL:
  - [ ] Implement RAG client in Observability Agent
  - [ ] Index incident history (6 months)
  - [ ] Index runbooks and playbooks
  - [ ] Index known anomaly patterns
  - [ ] Test: "High CPU" → RAG suggests root cause based on history
  - [ ] Test: "Latency increased" → RAG correlates with deploys
  - [ ] Test: "Memory leak" → RAG recommends specific runbook
- [ ] **Correlation Analysis (SIEM-like for Resilience)** ⚠️ CRITICAL:
  - [ ] Implement service dependency analysis
  - [ ] Detect cascading failures (service A → B → C)
  - [ ] Correlate timeouts with upstream services
  - [ ] Example: "Timeout in service X because service Y is slow"
  - [ ] Example: "503 in API Gateway because backend is down"
  - [ ] Implement dependency graph (service mesh data)
  - [ ] Blast radius analysis (how many services affected)
  - [ ] Incident prioritization (critical path services)
  - [ ] Mitigation suggestions based on impact

#### 6.2 Multi-Agent Collaboration
- [ ] Implement agent-to-agent communication
- [ ] Example: FinOps calls AWS Agent for details
- [ ] Example: Observability calls K8s Agent for logs

#### 6.3 Streaming Responses
- [ ] Implement SSE (Server-Sent Events)
- [ ] Streaming in Slack (updates message in real-time)
- [ ] Streaming in MCP Server

#### 6.4 Feedback Loop
- [ ] Thumbs up/down on all responses
- [ ] Save feedback to DynamoDB
- [ ] Quality dashboard (% positive per agent)
- [ ] Fine-tuning based on feedback

#### 6.5 Fallback LLM
- [ ] Support Anthropic direct (not Bedrock)
- [ ] Automatic fallback if Bedrock fails

#### 6.6 Advanced RAG
- [ ] Hybrid search (keyword + semantic)
- [ ] Re-ranking (Cohere Rerank)
- [ ] Query expansion
- [ ] Multi-hop reasoning

#### 6.7 Security & Compliance
- [ ] Audit logs (who asked what)
- [ ] PII detection and masking
- [ ] Rate limiting per user
- [ ] RBAC (permissions per agent)

#### 6.8 Version Updates (Continuous Maintenance)
- [ ] **Update to latest stable versions**:
  - [ ] Claude 4 (when available in Bedrock)
  - [ ] Python 3.13
  - [ ] FastAPI latest
  - [ ] boto3 latest
  - [ ] kubernetes client latest
  - [ ] OpenTelemetry latest
  - [ ] Pydantic v3 (when stable)
- [ ] **Test compatibility**:
  - [ ] Complete regression tests
  - [ ] Performance benchmarks
  - [ ] Breaking changes analysis
- [ ] **Document changes**:
  - [ ] CHANGELOG.md updated
  - [ ] Migration guides if needed

### Phase 7: Maintenance & SRE (Continuous)

#### 7.1 Metrics
- [ ] Latency per agent (p50, p95, p99)
- [ ] Error rate per agent
- [ ] Bedrock cost per agent
- [ ] Cache hit rate (Redis)
- [ ] Conversation length (messages/session)

#### 7.2 Alerts
- [ ] Error rate > 5% (PagerDuty)
- [ ] Latency p95 > 10s
- [ ] Bedrock cost > $500/day
- [ ] DynamoDB throttling
- [ ] Redis memory > 80%

#### 7.3 SLOs
- [ ] Availability: 99.5% (downtime < 3.6h/month)
- [ ] Latency: p95 < 5s
- [ ] Error rate: < 1%
- [ ] Cost: < $1000/month

### Phase 8: Advanced RAG (2-3 weeks)

#### 8.1 Hybrid Search
- [ ] Implement hybrid search (keyword + semantic)
- [ ] Combine BM25 (keyword) with vector search
- [ ] Adjust weights (70% semantic, 30% keyword)
- [ ] Test with ambiguous queries

#### 8.2 Re-ranking
- [ ] Integrate Cohere Rerank API
- [ ] Re-order RAG results by relevance
- [ ] Improve precision@5 (top 5 results)
- [ ] A/B test: with vs without re-ranking

#### 8.3 Query Expansion
- [ ] Expand queries automatically
- [ ] Example: "expensive EC2" → "EC2 cost optimization high spend"
- [ ] Use LLM to generate variations
- [ ] Test recall improvement

#### 8.4 Cross-Agent RAG
- [ ] Allow agents to query other agents' KBs
- [ ] Example: FinOps queries AWS Agent KB
- [ ] Example: Observability queries DevOps KB (runbooks)
- [ ] Implement permissions (who can access what)

### Phase 9: Long-term Memory (1-2 weeks)

#### 9.1 Persistent Memory
- [ ] Save important insights beyond 24h
- [ ] Separate DynamoDB table (no TTL)
- [ ] Categories: architectural decisions, accepted recommendations, lessons learned

#### 9.2 Architectural Decisions
- [ ] "Remember" why we chose X instead of Y
- [ ] Example: "Why did we use Aurora instead of RDS?"
- [ ] Index ADRs (Architecture Decision Records)

#### 9.3 Recommendation History
- [ ] Track accepted/rejected recommendations
- [ ] Learn team preferences
- [ ] Example: Team prefers Terraform over CloudFormation

### Phase 10: Continuous Learning (2-3 weeks)

#### 10.1 Feedback Loop
- [ ] Thumbs up/down on all responses
- [ ] Save feedback to DynamoDB
- [ ] Quality dashboard (% positive per agent)

#### 10.2 Fine-tuning
- [ ] Collect high-quality conversations (thumbs up)
- [ ] Fine-tune Claude with real conversations
- [ ] Test fine-tuned vs base model
- [ ] Gradual deploy (canary)

#### 10.3 A/B Testing
- [ ] A/B testing framework
- [ ] Test: with RAG vs without RAG
- [ ] Test: different prompts
- [ ] Test: different temperatures
- [ ] Metrics: latency, quality, cost

### Phase 11: Enriched Context (2-3 weeks)

#### 11.1 Confluence/Notion Integration
- [ ] MCP Server for Confluence
- [ ] MCP Server for Notion
- [ ] Index corporate documentation
- [ ] Unified search (KB + Confluence + Notion)

#### 11.2 Jira/Linear Integration
- [ ] MCP Server for Jira
- [ ] MCP Server for Linear
- [ ] Query tickets and roadmap
- [ ] Example: "What is the status of project X?"

#### 11.3 GitHub/GitLab Integration
- [ ] MCP Server for GitHub
- [ ] Expand GitLab MCP Server
- [ ] Query PRs, issues, commits
- [ ] Example: "Which PRs are open in repo X?"
- [ ] Correlate deploys with incidents

### Phase 12: Predictive Analysis (3-4 weeks)

#### 12.1 FinOps - Cost Prediction
- [ ] ML model to predict future costs
- [ ] Based on history + trends
- [ ] Alerts: "Cost will exceed budget in 2 weeks"
- [ ] Proactive optimization recommendations

#### 12.2 Observability - Failure Prediction
- [ ] ML model to detect pre-failure patterns
- [ ] Example: "CPU growing 5%/day → failure in 3 days"
- [ ] Predictive alerts (before failure)
- [ ] Preventive mitigation suggestions

#### 12.3 Proactive Optimizations
- [ ] Agents suggest improvements without being asked
- [ ] Example: "Detected 10 unused EBS volumes"
- [ ] Example: "Pod X can use 50% less CPU"
- [ ] Weekly opportunity report

### Phase 13: Multi-Modal (3-4 weeks)

#### 13.1 Screenshot Analysis
- [ ] Use Claude Vision to analyze dashboards
- [ ] Upload screenshot → automatic analysis
- [ ] Example: "What's wrong in this graph?"
- [ ] Detect visual anomalies

#### 13.2 Diagram Generation
- [ ] Generate architecture diagrams (Mermaid, PlantUML)
- [ ] Example: "Draw the architecture of service X"
- [ ] Dependency diagrams
- [ ] Data flow diagrams

#### 13.3 Graph Interpretation
- [ ] Analyze metric graphs (Grafana, CloudWatch)
- [ ] Detect visual patterns (spikes, trends, anomalies)
- [ ] Correlate multiple graphs
- [ ] Generate visual reports


---

## 💰 COST ESTIMATES

### Initial Phase (Basic Production)
| Resource | Monthly Cost |
|----------|--------------|
| DynamoDB | $5-15 |
| ElastiCache Redis | $20-40 |
| Bedrock (Claude) | $5-15 |
| EKS (incremental) | $10-30 |
| **Basic Total** | **$40-100** |

### With 5 Knowledge Bases (Complete RAG)
| Resource | Monthly Cost |
|----------|--------------|
| Bedrock (Claude 3.5) | $50-150 |
| DynamoDB | $10-30 |
| ElastiCache Redis | $30-60 |
| EKS (incremental) | $20-50 |
| 5 Bedrock Knowledge Bases | $500-3750 |
| OpenSearch Serverless | $700-1500 |
| S3 (KB storage) | $5-20 |
| Secrets Manager | $2-5 |
| CloudWatch Logs | $10-30 |
| **Total with RAG** | **$1327-5595** |

### With Advanced Features (Phases 8-13)
| Feature | Additional Cost |
|---------|-----------------|
| Cohere Rerank API | $50-200 |
| Fine-tuning Claude | $500-2000 (one-time) |
| ML Models (prediction) | $100-500 |
| **Advanced Total** | **$150-700/month** |

**Final Estimate (All Phases)**: $950-3700/month

**Phased Strategy**:
- Phase 1: Basic ($40-100/month)
- Phase 2: + FinOps KB ($150-600/month)
- Phase 3: + 4 remaining KBs ($800-3000/month)
- Phase 4: + Advanced features ($950-3700/month)

### Optimizations
- Use Savings Plans (Bedrock): -20%
- Redis Serverless (vs cluster): -50%
- DynamoDB on-demand (vs provisioned): variable
- Prompt caching (Bedrock): -90% on repeated tokens
- **OpenSearch vs Aurora vs Pinecone**: Evaluate cost/benefit
- **Share vector store between KBs**: -60% cost

**Optimized Estimate**: $800-3000/month

**Note**: High cost due to 5 Knowledge Bases. Consider:
- Phase 1: Only FinOps KB ($150-600/month)
- Phase 2: Add DevOps and Obs KB ($400-1200/month)
- Phase 3: Add AWS and K8s KB ($800-3000/month)

---

## 🎓 LESSONS LEARNED

### What Worked Well
✅ Intelligent Classifier > manual LangGraph  
✅ Separated contexts (global vs isolated) prevents confusion  
✅ Standardized interface facilitates maintenance  
✅ OpenTelemetry without changes to business code  
✅ Docker Compose for local dev = production  

### Challenges Faced
⚠️ Kubernetes Agent slow (30s timeout)  
⚠️ Prompt caching not available in all models  
⚠️ Sed broke syntax (DevOps/FinOps) - fixed manually  
⚠️ Docker build cache doesn't always invalidate - force rebuild  

### Future Improvements
💡 Implement circuit breaker (if agent fails 3x, skip)  
💡 Add fallback LLM (Anthropic direct if Bedrock fails)  
💡 Implement A/B testing (with vs without RAG)  
💡 Add multi-language support (PT-BR, EN, ES)  
💡 **RAG in Observability Agent is CRITICAL for anomaly detection**  
💡 **MCP Servers (Prometheus, Grafana, PagerDuty) essential for real-time context**  
💡 **Incident history + runbooks = intelligent pattern detection**  
💡 **Observability Agent as SIEM-like for resilience (event correlation)**  
💡 **Update to latest stable versions periodically**  
💡 **Advanced RAG: Hybrid search, re-ranking, query expansion, cross-agent**  
💡 **Long-term memory: Architectural decisions, team preferences**  
💡 **Continuous learning: Fine-tuning, feedback loop, A/B testing**  
💡 **Enriched context: Confluence, Jira, GitHub integrations**  
💡 **Predictive analysis: Predict costs, failures, proactive optimizations**  
💡 **Multi-modal: Screenshots, diagrams, graphs**

---

## 📚 REFERENCES

### AWS Labs Agent Squad
- Repo: https://github.com/awslabs/agent-squad
- Docs: https://awslabs.github.io/agent-squad/
- Local clone: `/tmp/agent-squad`

### Internal Documentation
- `README.md` - Overview
- `QUICKSTART.md` - Quick setup
- `docs/ARCHITECTURE.md` - Detailed architecture
- `docs/MCP_INTEGRATION.md` - Kiro CLI
- `docs/RAG_IMPLEMENTATION.md` - RAG setup

### Useful Commands
```bash
# Build and start
docker compose build && docker compose up -d

# Logs
docker compose logs -f supervisor

# Health checks
curl http://localhost:8000/health

# Test agent
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"user_input": "How many EC2 instances?", "user_id": "test", "session_id": "test"}'

# Recreate DynamoDB table
aws dynamodb create-table --table-name agent-sessions \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --endpoint-url http://localhost:8100
```

---

**Last Update**: 2026-02-14 16:21  
**Status**: ✅ 100% Operational Locally  
**Next Milestone**: Production Deploy (EKS)
