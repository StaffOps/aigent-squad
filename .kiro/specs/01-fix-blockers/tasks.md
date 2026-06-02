# Tasks: Fix Blockers

- [ ] T1: Criar `Dockerfile` na raiz para o supervisor (B1)
- [ ] T2: Validar `docker compose build supervisor` (depends on: T1)
- [ ] T3: Limpar duplicação em `src/core/gitlab_client.py` — manter 1ª definição + 1 singleton (B3)
- [ ] T4: Confirmar que `devops/agent.py` só usa métodos da definição mantida (depends on: T3)
- [ ] T5: Remover cabeçalho duplicado em `mcp-server/mcp-server.py` (B4)
- [ ] T6: Reescrever `src/api/server.py` usando `supervisor.process_request` + `ChatStorage`, sem langchain/StateStore/graph (B2)
- [ ] T7: Validar import de `src.api.server` e `src.core.gitlab_client` via container (depends on: T3, T6)
- [ ] T8: Rodar `setup-local.sh` até health-check (ou `docker compose up -d`) e confirmar build verde (depends on: T1, T6)

## Ordem sugerida
T3 → T4, T5, T1 → T2 podem rodar em paralelo. T6 depois. T7/T8 fecham.
