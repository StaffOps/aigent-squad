# Design: Unify Agent Architecture

## Reference pattern

`src/agents/aws/{agent.py,server.py}` + `src/core/agent_base.py`. All unification converges to it.

### Target structure for each agent

```
src/agents/<name>/
├── agent.py     # class <Name>Agent(Agent): async process_request(...)
├── server.py    # Thin FastAPI: instantiates the agent, exposes /process and /health
├── prompt.md
└── Dockerfile
```

### `agent.py` (contract)

```python
class XAgent(Agent):
    def __init__(self):
        super().__init__(agent_id="x", name="X Agent", description="...")
        self.system_prompt = self._load_prompt()
        # specific clients (boto3, k8s, httpx...)

    async def process_request(self, input_text, user_id, session_id,
                              chat_history, additional_params=None) -> ConversationMessage:
        # 1. validate input (non-empty, <10000)
        # 2. check cache (deterministic key — detail in spec 03)
        # 3. collect domain-specific context (inventory/cluster/costs/docs/metrics)
        # 4. build prompt with _format_history(chat_history)
        # 5. bedrock.invoke(...)
        # 6. cache + return ConversationMessage(role,content,timestamp,agent_id)
        # tracing + log_request/log_response/log_error at every step
```

### `server.py` (single pattern — mirror the aws one)

```python
app = FastAPI(title="<X> Agent Service")
agent = XAgent()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

class ProcessRequest(BaseModel):
    input_text: str; user_id: str; session_id: str
    chat_history: List[ChatMessage] = []
    additional_params: Optional[dict] = None

@app.post("/process")  # returns {role, content, timestamp, agent_id}
@app.get("/health")
```

## Changes per agent

| Agent | Action |
|-------|--------|
| aws | Reference. No change (except cache/obs in spec 03). |
| kubernetes | `agent.py` is already correct. Rewrite `server.py` to use `KubernetesAgent` from `agent.py` (currently redefines). |
| finops | `agent.py` (Athena+Kubecost+history) is the correct one. Rewrite `server.py` to use it. Move RAG init (`rag_client`) from server inline to `agent.py`. |
| devops | `agent.py` (uses `gitlab_client`+docs) is the correct one. Rewrite `server.py` to use it. |
| observability | Create `agent.py` (`ObservabilityAgent(Agent)`) with the logic that currently lives in server (metrics via `PROMETHEUS_URL`, anomalies). Server becomes thin. |

## Response contract

Standardize to `{role, content, timestamp, agent_id}`. After that, in the supervisor:

```python
# before
response_text = agent_response.get("content", agent_response.get("response", ""))
# after
response_text = agent_response["content"]
```

## Invariants

- `process_request` is the only logical entry point for each agent.
- No business logic in `server.py` (only HTTP transport + instrumentation).
- `chat_history` always formatted via `_format_history` and included in the context.
- Read-only preserved (prompts unchanged).

## External dependencies

| Service | Agent |
|---------|-------|
| Bedrock | all |
| EC2/CE (boto3) | aws, finops |
| Athena (boto3) | finops |
| Kubernetes API | kubernetes |
| GitLab API + Docs Portal | devops |
| Prometheus HTTP | observability |

## Verification

```bash
# contract: all /process return the 4 keys
# unit test with mocked bedrock validating ConversationMessage
docker run --rm -v $(pwd):/app -w /app python:3.11-slim \
  sh -c "pip install -q -r requirements.txt pytest && pytest tests/ -v"
```

## Risks

- FinOps has two divergent implementations; when choosing `agent.py`, validate that the optional RAG (flag `rag_enabled`) continues working.
- Observability never had an `agent.py`; create with parity to the current server before switching.
