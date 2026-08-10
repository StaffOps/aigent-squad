# Tasks: Unify Agent Architecture

- [x] T1: Rewrite `src/agents/kubernetes/server.py` to use `KubernetesAgent` from `agent.py`
- [x] T2: Rewrite `src/agents/devops/server.py` to use `DevOpsAgent` from `agent.py`
- [x] T3: Rewrite `src/agents/finops/server.py` to use `FinOpsAgent` from `agent.py`
- [x] T4: `observability/agent.py` already existed with the correct pattern — no action needed
- [x] T5: Rewrite `src/agents/observability/server.py` to use the `agent.py`
- [x] T6: Contract `{role, content, timestamp, agent_id}` standardized across all 5 servers
- [x] T7: Supervisor simplified to `agent_response["content"]` (fallback removed)
- [x] T8: `chat_history` used via `_format_history` in all 5 agents (was already in place)
- [x] T9: `docker compose build` + `up` + smoke test pass
- [x] T10: Docs updated + commit

## Completed: 2026-06-14
