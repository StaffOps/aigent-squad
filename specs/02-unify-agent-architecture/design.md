# Design: Unify Agent Architecture

## Padrão de referência

`src/agents/aws/{agent.py,server.py}` + `src/core/agent_base.py`. Toda a unificação converge para ele.

### Estrutura alvo de cada agente

```
src/agents/<name>/
├── agent.py     # class <Name>Agent(Agent): async process_request(...)
├── server.py    # FastAPI fino: instancia o agent, expõe /process e /health
├── prompt.md
└── Dockerfile
```

### `agent.py` (contrato)

```python
class XAgent(Agent):
    def __init__(self):
        super().__init__(agent_id="x", name="X Agent", description="...")
        self.system_prompt = self._load_prompt()
        # clients específicos (boto3, k8s, httpx...)

    async def process_request(self, input_text, user_id, session_id,
                              chat_history, additional_params=None) -> ConversationMessage:
        # 1. valida input (não-vazio, <10000)
        # 2. checa cache (key determinística — detalhe na spec 03)
        # 3. coleta contexto específico (inventory/cluster/costs/docs/metrics)
        # 4. monta prompt com _format_history(chat_history)
        # 5. bedrock.invoke(...)
        # 6. cacheia + retorna ConversationMessage(role,content,timestamp,agent_id)
        # tracing + log_request/log_response/log_error em todos os passos
```

### `server.py` (padrão único — espelhar o do aws)

```python
app = FastAPI(title="<X> Agent Service")
agent = XAgent()
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()

class ProcessRequest(BaseModel):
    input_text: str; user_id: str; session_id: str
    chat_history: List[ChatMessage] = []
    additional_params: Optional[dict] = None

@app.post("/process")  # retorna {role, content, timestamp, agent_id}
@app.get("/health")
```

## Mudança por agente

| Agente | Ação |
|--------|------|
| aws | Referência. Sem mudança (exceto cache/obs em spec 03). |
| kubernetes | `agent.py` já está correto. Reescrever `server.py` para usar `KubernetesAgent` de `agent.py` (hoje redefine). |
| finops | `agent.py` (Athena+Kubecost+history) é o correto. Reescrever `server.py` para usá-lo. Mover init de RAG (`rag_client`) do server inline para o `agent.py`. |
| devops | `agent.py` (usa `gitlab_client`+docs) é o correto. Reescrever `server.py` para usá-lo. |
| observability | Criar `agent.py` (`ObservabilityAgent(Agent)`) com a lógica que hoje vive no server (metrics via `PROMETHEUS_URL`, anomalies). Server vira fino. |

## Contrato de resposta

Padronizar em `{role, content, timestamp, agent_id}`. Depois disso, no supervisor:

```python
# antes
response_text = agent_response.get("content", agent_response.get("response", ""))
# depois
response_text = agent_response["content"]
```

## Invariantes

- `process_request` é a única porta de entrada lógica de cada agente.
- Nenhuma lógica de negócio em `server.py` (só transporte HTTP + instrumentação).
- `chat_history` sempre formatado via `_format_history` e incluído no contexto.
- Read-only preservado (prompts inalterados).

## Dependências externas

| Serviço | Agente |
|---------|--------|
| Bedrock | todos |
| EC2/CE (boto3) | aws, finops |
| Athena (boto3) | finops |
| Kubernetes API | kubernetes |
| GitLab API + Docs Portal | devops |
| Prometheus HTTP | observability |

## Verificação

```bash
# contrato: todos os /process retornam as 4 chaves
# teste unitário com bedrock mockado validando ConversationMessage
docker run --rm -v $(pwd):/app -w /app python:3.11-slim \
  sh -c "pip install -q -r requirements.txt pytest && pytest tests/ -v"
```

## Riscos

- FinOps tem duas implementações divergentes; ao escolher o `agent.py`, validar que o RAG opcional (flag `rag_enabled`) continua funcionando.
- Observability nunca teve `agent.py`; criar com paridade ao server atual antes de trocar.
