# Auditoria — AIgent-squad

**Data**: 2026-05-30
**Branch**: `dev`
**Commit base**: `da53438` (main)
**Escopo**: leitura integral de 24 arquivos Python, `docker-compose.yaml`, 6 Dockerfiles, docs (`docs/*`, READMEs) e arquivos de versão.

Este documento consolida os achados que originam as specs em `.kiro/specs/`. Severidade:

- 🔴 **Blocker** — impede o sistema de rodar ou de buildar.
- 🟠 **High** — quebra comportamento esperado, dívida arquitetural ou risco de segurança.
- 🟡 **Medium** — inconsistência, viola steering, ou degrada operação.
- ⚪ **Low** — cosmético, higiene, documentação.

---

## Visão geral da arquitetura (estado real)

```
MCP Server (8006) ─▶ Supervisor (8000) ─▶ [aws 8001 | k8s 8002 | finops 8003 | devops 8004 | obs 8005]
                          │                         │
                          ▼                         ▼
                   Classifier (Bedrock)        Redis cache + Bedrock
                   DynamoDB (histórico)
```

Conceito sólido: supervisor + classifier + 5 especialistas read-only, cache Redis, histórico em DynamoDB com isolamento por agente, OTel. O problema é a **distância entre o README ("v2.0, Production Ready") e o estado real do código**.

---

## 🔴 Blockers (spec 01-fix-blockers)

### B1 — Dockerfile da raiz ausente
- `docker-compose.yaml` → serviço `supervisor` usa `dockerfile: Dockerfile` (raiz).
- Só existem Dockerfiles em `src/agents/*/Dockerfile` e `mcp-server/Dockerfile`.
- **Efeito**: `docker-compose build` do supervisor falha; `setup-local.sh` quebra no `build --parallel`.

### B2 — `src/api/server.py` quebrado (código morto)
Importa símbolos inexistentes:
- `from src.core.state_store import StateStore` → a classe real é `ChatStorage`.
- `from langchain_core.messages import HumanMessage` → langchain removido do `requirements.txt`.
- `supervisor.graph.invoke(...)` → `SupervisorAgent` não tem `.graph` (usa classifier + HTTP).
- **Efeito**: arquivo nunca importa. É a (suposta) integração Slack. Decidir: reescrever ou remover.

### B3 — `src/core/gitlab_client.py` com corpo duplicado
- A partir da linha ~330 os métodos (`search_documentation`, `get_file_content`, `list_projects`, `get_repository_tree`, `search_code`, `get_docs_url`, encoders) aparecem **uma segunda vez**.
- Singleton `gitlab_client = GitLabClient()` declarado **duas vezes**.
- A segunda `search_code` tem comportamento diferente (`/search` global vs `Company`).
- **Efeito**: código morto inalcançável + confusão de manutenção.

### B4 — `mcp-server/mcp-server.py` com cabeçalho duplicado
- `app = FastAPI(...)` e `SUPERVISOR_URL = os.getenv(...)` declarados duas vezes.
- Não quebra execução (idempotente), mas é sintoma da mesma colagem ruim.

---

## 🟠 High — Inconsistência arquitetural (spec 02-unify-agent-architecture)

### A1 — Dois padrões de agente convivendo; servers usam o errado

| Agente | `agent.py` (herda `Agent` base) | `server.py` usa o `agent.py`? |
|--------|--------------------------------|-------------------------------|
| aws | async, history, tracing, validação | ✅ sim |
| kubernetes | idem | ❌ reimplementa classe própria |
| finops | idem (Athena + Kubecost + history) | ❌ **outra** `FinOpsAgent` (RAG, sem Athena, sem history) |
| devops | idem (usa `gitlab_client`) | ❌ classe própria sem gitlab |
| observability | (não há `agent.py` base completo) | ❌ classe própria |

- Os `agent.py` de **k8s, finops, devops, observability são código morto**.
- Os servers são síncronos, **ignoram `chat_history`** (recebem mas não usam), sem tracing/validação, divergem entre si.
- FinOps é o pior caso: a versão que **roda** (server.py) não tem a integração Athena/Kubecost que o README anuncia.

### A2 — Contrato de resposta divergente
- aws/k8s `agent.py`-based → `{role, content, timestamp, agent_id}`.
- finops/devops/observability server → `{response}`.
- Supervisor mascara com `agent_response.get("content", agent_response.get("response", ""))`. Frágil.

### A3 — `chat_history` recebido e descartado
- Supervisor busca histórico isolado por agente e envia, mas os servers síncronos (k8s/finops/devops/obs) só passam `query`. Conversação multiturno fica quebrada nesses agentes.

---

## 🟠 High — Segurança (spec 04-harden-security)

### S1 — Nenhum endpoint tem autenticação
- Supervisor, 5 agents e MCP server expõem HTTP aberto.
- Dentro do cluster, qualquer pod invoca o agente de AWS/K8s.
- Mínimo: mTLS (Istio Ambient, per `cloud-security.md`) + NetworkPolicy, ou token compartilhado.

### S2 — Containers rodam como root
- Dockerfiles `python:3.12-alpine` sem `USER`.
- Viola `k8s-best-practices` (runAsNonRoot, readOnlyRootFilesystem, drop ALL caps).

### S3 — Redis sem auth/TLS no compose
- `REDIS_SSL=false`, sem password. Aceitável em dev, mas precisa estar documentado e diferente em prod.

### S4 — Prompt injection
- Input do usuário + saídas de GitLab/docs/inventory interpolados direto no prompt do Bedrock.
- Read-only mitiga dano material, mas permite manipular a resposta. Delimitar dados não-confiáveis.

### S5 — `~/.aws` montado em todos os containers
- OK para dev local. Prod **deve** usar IRSA (per `cloud-security.md`). Documentar e separar.

---

## 🟠 High — Cache (spec 03-fix-cache-observability)

### C1 — `hash()` nativo na cache key
- `cache_key = f"query:{hash(input_text)}"` em todos os agentes.
- `hash()` de str é **randomizado por processo** (`PYTHONHASHSEED`). Entre réplicas/restarts nunca dá hit.
- **Fix**: `hashlib.sha256(input_text.encode()).hexdigest()`.

### C2 — Cache key ignora identidade e contexto
- Não inclui `user_id`/`session_id`/`chat_history`.
- Efeitos: (a) follow-ups ("yes", "me explica mais") batem no cache e retornam resposta anterior errada; (b) usuários diferentes compartilham respostas.
- Para agente conversacional, quebra o contexto. Reavaliar se faz sentido cachear a resposta do LLM por query.

---

## 🟡 Medium — Observabilidade (spec 03-fix-cache-observability)

### O1 — `ConsoleSpanExporter` hardcoded
- `logger.py` exporta spans para console, não OTLP. O `requirements.txt` traz `opentelemetry-exporter-otlp` mas não é usado.
- Viola `observability-principles.md` (App SDK → OTel Collector via `OTEL_EXPORTER_OTLP_ENDPOINT`).

### O2 — `JSONFormatter` perde os campos extras
- Lê `record.extra`, mas o `logging` não cria esse atributo — `logger.info(msg, extra={...})` injeta as chaves como atributos diretos do `record`.
- **Efeito**: todo o contexto estruturado (`agent_id`, `user_id`, `duration_ms`…) **não aparece** no log JSON.

### O3 — `service.name` fixo
- `Resource.create({"service.name": "agent-squad"})` igual para todos os serviços. Impossível distinguir agentes no tracing.
- Deveria vir de env (`OTEL_SERVICE_NAME`/`SERVICE_NAME`) por agente.

### O4 — `PROMETHEUS_URL` ignorado
- Observability agent hardcoda `http://prometheus.monitoring.svc.cluster.local:9090` apesar de a env var existir no compose.
- Viola 12-factor III (config via env).

---

## ⚪ Low — Versão, datas e docs

### D1 — Modelo Bedrock divergente (3 valores)
- README: "Claude 3.5 Sonnet".
- `config.py` default: `anthropic.claude-sonnet-4-5-20250929-v1:0`.
- `.env.example`: `anthropic.claude-3-5-sonnet-20240620-v1:0`.
- Precisa de fonte única.

### D2 — "Production Ready" inflado
- README: "v2.0 / Production Ready / Last Updated 2026-02-14".
- Roadmap fase 1 ainda é "testing"; **zero testes** no repo.
- Per `version-management.md`, "Production Ready" sem deploy real nem testes é versão inflada.

### D3 — `docs/ARCHITECTURE.md` descreve LangGraph (removido)
- Supervisor descrito como "LangGraph + Bedrock". LangGraph foi removido (comentado no `requirements.txt`).

### D4 — Encoding corrompido em docs
- "Responif", "Seforted", "Licenif", "Baif", emojis viraram "?" em `READ_ONLY_POLICY.md` e README.
- Find/replace ou conversão de charset corrompeu o texto.

### D5 — `datetime.utcnow()` deprecado (3.12)
- Usado em `state_store.py`, agentes, supervisor → `datetime.now(timezone.utc)`.

---

## ⚪ Low — Higiene

| ID | Achado |
|----|--------|
| H1 | `requirements.txt` monolítico: cada container instala kubernetes+slack+mcp etc. Separar por agente reduz imagem e superfície. |
| H2 | Sem `.dockerignore` (o `.gitignore` lista `.dockerignore` — provável engano). |
| H3 | `gitlab_client` com grupos hardcoded ("Company", "your-organization") — mover para config. |
| H4 | `GitLabClient.base_url` hardcoded `gitlab.com`, ignora `settings.gitlab_url`. |
| H5 | `version: '3.8'` no compose é obsoleto (Compose v2 ignora). |

---

## Sem testes

- Nenhum arquivo de teste no repo. `pytest` não configurado. Bloqueia o critério de verificação do steering.
- Specs de fix devem incluir testes mínimos (cache key determinística, contrato de resposta, parsing do classifier).

---

## Pontos fortes (preservar)

- Separação supervisor/classifier/especialistas é limpa e escalável.
- Isolamento de histórico por agente no DynamoDB (`pk = user#session`, `sk = agent#timestamp`).
- `agent_base.py` + `bedrock.py` (retry com backoff) + `aws/agent.py` são o **padrão de referência correto** — a unificação deve convergir para eles.
- Política read-only bem pensada (4 camadas).
- OTel já presente nas dependências; falta cabear corretamente.

---

## Mapa achado → spec

| Spec | Achados cobertos |
|------|------------------|
| `01-fix-blockers` | B1, B2, B3, B4 |
| `02-unify-agent-architecture` | A1, A2, A3, H1 |
| `03-fix-cache-observability` | C1, C2, O1, O2, O3, O4, D5 |
| `04-harden-security` | S1, S2, S3, S4, S5 |
| `ROADMAP.md` + README | D1, D2, D3, D4, H2–H5, testes |
