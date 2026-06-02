# Tasks: Unify Agent Architecture

- [ ] T1: Reescrever `src/agents/kubernetes/server.py` para usar `KubernetesAgent` de `agent.py` (espelhar aws/server.py)
- [ ] T2: Reescrever `src/agents/devops/server.py` para usar `DevOpsAgent` de `agent.py`
- [ ] T3: Reescrever `src/agents/finops/server.py` para usar `FinOpsAgent` de `agent.py`; migrar init do RAG para o `agent.py`
- [ ] T4: Criar `src/agents/observability/agent.py` (`ObservabilityAgent(Agent)`) com paridade ao server atual (depends on: —)
- [ ] T5: Reescrever `src/agents/observability/server.py` para usar o novo `agent.py` (depends on: T4)
- [ ] T6: Padronizar contrato de resposta `{role, content, timestamp, agent_id}` nos 5 servers (depends on: T1,T2,T3,T5)
- [ ] T7: Simplificar leitura no supervisor para `agent_response["content"]` (depends on: T6)
- [ ] T8: Garantir `chat_history` formatado e injetado nos 5 agentes (k8s/finops/devops/obs hoje ignoram)
- [ ] T9: Criar `tests/` com pytest: contrato de resposta + uso de history (bedrock mockado) (depends on: T6,T8)
- [ ] T10: Build + testes via Docker; `docker compose up` e smoke test de 1 query por agente (depends on: T9)

## Ordem sugerida
T4 → T5; T1/T2/T3 em paralelo; depois T6 → T7/T8 → T9 → T10.

## Notas
- Não tocar em cache key/observabilidade aqui (spec 03) nem authz/non-root (spec 04).
- Preservar prompts (`prompt.md`) e política read-only.
