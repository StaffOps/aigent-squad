# Changelog

## [Unreleased] - 2026-06-14

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
