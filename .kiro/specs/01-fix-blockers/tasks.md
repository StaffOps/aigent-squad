# Tasks: Fix Blockers

- [x] T1: Criar `Dockerfile` na raiz para o supervisor (B1)
- [x] T2: Validar `docker compose build supervisor` (depends on: T1)
- [x] T3: Limpar duplicação em `src/core/gitlab_client.py` — manter 1ª definição + 1 singleton (B3)
- [x] T4: Confirmar que `devops/agent.py` só usa métodos da definição mantida (depends on: T3)
- [x] T5: Remover cabeçalho duplicado em `mcp-server/mcp-server.py` (B4)
- [x] T6: Reescrever `src/api/server.py` usando `supervisor.process_request` + `ChatStorage`, sem langchain/StateStore/graph (B2)
- [x] T7: Validar import de `src.api.server` e `src.core.gitlab_client` via container (depends on: T3, T6)
- [x] T8: Rodar `docker compose up -d` e confirmar build verde + health-checks (depends on: T1, T6)

## Concluído: 2026-06-14

Mudanças adicionais necessárias durante execução:
- Criados `__init__.py` em todos os diretórios `src/` (Python packages)
- Healthcheck do supervisor: `wget --spider` → `curl -f` (GNU wget envia HEAD, FastAPI rejeita 405)
- Supervisor Dockerfile usa `python:3.11-slim` + curl (agents usam `python:3.12-alpine` + BusyBox wget)
