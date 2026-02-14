# Migration Guide - AWS Labs Best Practices

## ? Implemented Changes

### ? 1. Storage with Context Separation

**Before**: Generic StateStore without separation  
**After**: ChatStorage with isolated contexts

```python
# GLOBAL history (for classifier)
chat_history = await storage.fetch_all_chats(user_id, session_id)

# ISOLATED history (for specific agent)
agent_history = await storage.fetch_chat(user_id, session_id, agent_id)
```

**Benefits**:
- Classifier sees complete context for better decisions
- Agents don't get confused with other agents' conversations
- Privacy between agents

---

### ? 2. Intelligent Classifier

**New component**: `src/core/classifier.py`

```python
from src.core.classifier import classifier

# Classify intent with global history
result = await classifier.classify(user_input, chat_history)
# result.selected_agent = "aws" | "kubernetes" | "finops" | ...
# result.confidence = 0.95
# result.reasoning = "User is asking about EC2 instances"
```

**Features**:
- Detects follow-ups ("yes", "ok", "1") -> keeps same agent
- Intelligent context switching
- Confidence scoring
- Explainable reasoning

---

### ? 3. Standardized Interface `process_request()`

**Before**: Each agent had different method (`process()`, `handle()`, etc)  
**After**: ALL agents implement:

```python
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
- Total standardization
- Easy to swap agents
- Supervisor can call any agent the same way

---

### ? 4. Base Agent Class

**New component**: `src/core/agent_base.py`

```python
from src.core.agent_base import Agent

class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            agent_id="my-agent",
            name="My Agent",
            description="What this agent does"
        )
    
    async def process_request(...):
        # Implementation
```

**Benefits**:
- Common inheritance
- Shared utility methods
- Consistency between agents

---

### ? 5. Refactored Supervisor

**Before**: LangGraph with manual routing  
**After**: Classifier + HTTP routing

```python
# 1. Classifier decides which agent
classification = await classifier.classify(user_input, chat_history)

# 2. Supervisor calls agent via HTTP
response = await http_client.post(agent_url, json={...})

# 3. Saves conversation automatically
await storage.save_chat_message(...)
```

**Benefits**:
- More intelligent routing
- Less manual code
- Better context management

---

## ? How to Use

### Test Locally

```bash
# 1. Install dependencies
cd code/
pip install -r requirements.txt

# 2. Configure .env.local
cp .env.example .env.local
# Edit with your AWS credentials

# 3. Run agents (each in separate terminal)
python -m src.agents.aws.server_new
python -m src.agents.kubernetes.server_new
python -m src.agents.finops.server_new
python -m src.agents.devops.server_new
python -m src.agents.observability.server_new

# 4. Run supervisor
python -m src.supervisor.server

# 5. Test
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "How many EC2 instances are running?",
    "user_id": "user123",
    "session_id": "session456"
  }'
```

---

## ? Comparison: Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| **Routing** | Manual via LangGraph | Intelligent Classifier |
| **Conversation History** | Generic | Separated (global vs isolated) |
| **Agent Interface** | Inconsistent | Standardized `process_request()` |
| **Follow-up Detection** | ? None | ? Automatic |
| **Context Switching** | ? Poor | ? Intelligent |
| **Confidence Scoring** | ? None | ? Yes |
| **Agent Isolation** | ? None | ? Isolated history |

---

## ? Next Steps (Optional)

### Streaming Support (Nice-to-have)

```python
async def process_request(...) -> AsyncIterable[str]:
    async for chunk in bedrock.stream(...):
        yield chunk
```

### Agent-as-Tools Pattern (Advanced)

Implement AWS Labs SupervisorAgent that uses agents as tools:

```python
SupervisorAgent(
    lead_agent=BedrockLLMAgent(...),
    team=[aws_agent, k8s_agent, ...],
    storage=storage
)
```

---

## ? Important Notes

1. **Files `*_new.py`**: New servers created to not break old ones
2. **Gradual migration**: Can test new servers before replacing
3. **Backward compatibility**: Old agents still work
4. **DynamoDB schema**: Need to create table with `pk` and `sk` (see terraform)

---

## ? Troubleshooting

### Error: "No module named 'src.core.agent_base'"
```bash
# Add PYTHONPATH
export PYTHONPATH=/path/to/agent-squad:$PYTHONPATH
```

### Error: "Table not found"
```bash
# Create DynamoDB table
cd terraform/
terraform apply
```

### Error: "Agent not responding"
```bash
# Check if agent is running
curl http://localhost:8001/health
```

---

## ? Migration Checklist

- [x] Storage refactored with context separation
- [x] Classifier implemented
- [x] Base Agent class created
- [x] AWS Agent refactored
- [x] Kubernetes Agent refactored
- [x] FinOps Agent refactored
- [x] DevOps Agent refactored
- [x] Observability Agent refactored
- [x] Supervisor refactored
- [x] Servers updated
- [ ] Local tests
- [ ] Deploy on EKS
- [ ] Integration with Slack
- [ ] Monitoring and logs

---

**Date**: 2026-02-14  
**Version**: 2.0  
**Status**: Implemented, awaiting tests
