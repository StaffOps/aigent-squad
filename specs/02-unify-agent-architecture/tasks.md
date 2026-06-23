# Tasks: Unify Agent Architecture

- [x] T1: Reescrever `src/agents/kubernetes/server.py` para usar `KubernetesAgent` de `agent.py`
- [x] T2: Reescrever `src/agents/devops/server.py` para usar `DevOpsAgent` de `agent.py`
- [x] T3: Reescrever `src/agents/finops/server.py` para usar `FinOpsAgent` de `agent.py`
- [x] T4: `observability/agent.py` já existia com padrão correto — nenhuma ação necessária
- [x] T5: Reescrever `src/agents/observability/server.py` para usar o `agent.py`
- [x] T6: Contrato `{role, content, timestamp, agent_id}` padronizado nos 5 servers
- [x] T7: Supervisor simplificado para `agent_response["content"]` (fallback removido)
- [x] T8: `chat_history` usado via `_format_history` nos 5 agentes (já estava)
- [x] T9: `docker compose build` + `up` + smoke test passam
- [x] T10: Docs atualizados + commit

## Concluído: 2026-06-14
