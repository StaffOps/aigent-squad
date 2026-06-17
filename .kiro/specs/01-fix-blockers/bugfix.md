# Bugfix: Fix Blockers

**Spec**: `01-fix-blockers`
**Severidade**: 🔴 Blocker
**Achados**: B1, B2, B3, B4 (ver `../AUDIT.md`)

Objetivo: deixar `docker-compose up` funcional e remover código morto/quebrado que engana quem lê o repo.

---

## B1 — Dockerfile da raiz ausente

**Current behavior**: `docker-compose.yaml` aponta o serviço `supervisor` para `dockerfile: Dockerfile` na raiz; o arquivo não existe. `docker-compose build` e `setup-local.sh` falham.

**Expected behavior**: existe `Dockerfile` na raiz que builda a imagem do supervisor (e serve de base reutilizável), e o build do compose passa.

**Unchanged**: Dockerfiles dos agentes e do mcp-server.

---

## B2 — `src/api/server.py` quebrado

**Current behavior**: importa `StateStore` (classe real: `ChatStorage`), `langchain_core.messages.HumanMessage` (langchain removido) e usa `supervisor.graph.invoke()` (`SupervisorAgent` não tem `.graph`). O módulo nunca importa.

**Expected behavior**: a integração Slack funciona via `supervisor.process_request(...)` e `ChatStorage`, OU o arquivo é removido se a integração Slack não for prioridade.

**Decisão**: reescrever de forma mínima usando a API atual do supervisor (o supervisor já é o ponto de entrada `/query`; o `api/server.py` vira apenas o webhook Slack que chama `supervisor.process_request`). Sem langchain, sem StateStore, sem graph.

**Unchanged**: `src/supervisor/agent.py`, `src/supervisor/server.py`.

---

## B3 — `gitlab_client.py` com corpo duplicado

**Current behavior**: métodos e singleton declarados duas vezes (linha ~330 em diante). Segunda metade é inalcançável e diverge da primeira (`search_code` global vs `Company`).

**Expected behavior**: uma única definição de cada método; um único `gitlab_client = GitLabClient()`. Comportamento preservado = primeira definição (a que está em uso).

**Unchanged**: assinatura pública dos métodos consumidos por `devops/agent.py` (`search_documentation`, `get_file_content`, `list_projects`, `get_repository_tree`, `search_code`, `search_in_company`, `list_all_projects`, `get_docs_url`).

---

## B4 — `mcp-server.py` cabeçalho duplicado

**Current behavior**: `app = FastAPI(...)` e `SUPERVISOR_URL` declarados duas vezes. Idempotente, mas sujo.

**Expected behavior**: declaração única.

**Unchanged**: rotas `/health`, `/query` e contrato `QueryRequest`/`QueryResponse`.

---

## Critérios de aceite

- [ ] `docker compose build` conclui sem erro para todos os serviços.
- [ ] `python -c "import src.api.server"` (via container) não lança ImportError — ou o arquivo foi removido.
- [ ] `python -c "import src.core.gitlab_client"` importa; `grep -c "gitlab_client = GitLabClient()"` retorna 1.
- [ ] `mcp-server.py` tem uma única atribuição de `app`.
- [ ] `setup-local.sh` chega ao health-check sem falha de build.
