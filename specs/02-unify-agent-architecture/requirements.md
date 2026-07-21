---
spec: 02-unify-agent-architecture
status: done
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Unify Agent Architecture

**Spec**: `02-unify-agent-architecture`
**Severidade**: 🟠 High
**Achados**: A1, A2, A3, H1 (ver `../AUDIT.md`)

Convergir todos os agentes para um único padrão: a classe base `Agent` (`src/core/agent_base.py`), implementada async, com tracing, validação e uso real de `chat_history`. Hoje só o `aws` segue esse padrão; k8s/finops/devops/observability reimplementam classes próprias nos `server.py` e ignoram o `agent.py` correspondente.

## User Stories

WHEN o supervisor roteia uma query para qualquer agente THEN o agente SHALL processar via `process_request(input_text, user_id, session_id, chat_history, additional_params)` herdado de `Agent`.

WHEN qualquer agente responde THEN o payload HTTP SHALL ter o mesmo contrato `{role, content, timestamp, agent_id}`.

WHEN o supervisor envia `chat_history` para um agente THEN o agente SHALL incorporar esse histórico no contexto do prompt (multiturno funcional).

WHEN um `server.py` instancia o agente THEN ele SHALL importar a classe de `agent.py` (não redefinir lógica inline).

## Acceptance Criteria

- [ ] Os 5 `server.py` importam e usam a classe de `agent.py` (zero classes de agente redefinidas em server).
- [ ] Os 5 agentes herdam de `Agent` e implementam `process_request` async.
- [ ] Contrato de resposta idêntico nos 5 endpoints `/process` (`role, content, timestamp, agent_id`).
- [ ] `chat_history` é formatado e injetado no prompt nos 5 agentes.
- [ ] Supervisor lê só `content` (remove o fallback `get("content", get("response"))`).
- [ ] `observability/agent.py` passa a existir com o padrão `Agent` (hoje só há server).
- [ ] FinOps `agent.py` (Athena+Kubecost+RAG) é a versão que roda; o server inline é descartado.
- [ ] Testes: contrato de resposta + uso de history em pelo menos 1 agente.

## Fora de escopo

- Cache key e observabilidade → spec 03.
- Autenticação e non-root → spec 04.
- Separar `requirements.txt` por agente (H1) é desejável mas opcional aqui; se feito, não pode quebrar build.
