# Análise Cross-Domain — AIgent-squad

**Data**: 2026-06-02
**Branch**: `dev`
**Método**: 8 specialists (dev, security, observability, aws, finops, gitops, sre, documentation) leram o projeto em paralelo, cada um pela sua lente.
**Escopo**: achados **além** do `AUDIT.md` (B1–B4, A1–A3, S1–S5, C1–C2, O1–O4, D1–D5, H1–H5). Nada aqui repete o AUDIT — só aprofunda ou descobre o que ele não viu.

> Achados marcados ✅ foram verificados diretamente no código. Os demais vêm dos specialists com `file:line` citado e devem ser confirmados na implementação.

---

## TL;DR — O que o AUDIT não viu

O AUDIT focou em *"não builda / não roda"*. Esta análise mostra que **mesmo depois de buildar, o sistema tem problemas estruturais graves** em 3 eixos:

1. **Não tem concorrência** — todo I/O (boto3, requests, httpx.Client) é síncrono dentro de handlers `async`. Um request bloqueia todos os outros. Athena chega a bloquear o event loop por 30s.
2. **Falha fechada** — Redis e DynamoDB sem tratamento de erro. Qualquer outage de backing service = outage total. O correto é falhar **aberto** (cache miss / histórico vazio, mas o sistema responde).
3. **É invisível e indefensável** — telemetria quebrada (6/7 serviços sem instrumentação, propagação de trace quebrada, zero métricas), e read-only é só prompt — não há IAM deny nem RBAC real por trás.

Esses três pontos não estão em nenhuma spec atual. Eles são pré-requisito para "deploy real" tanto quanto os blockers do AUDIT.

---

## 🔴 Achados convergentes (≥2 specialists, alta severidade)

### CONV-1 — I/O síncrono dentro de handlers async → zero concorrência ✅
**Flagueado por**: dev (F1–F12), sre (R4), aws (F7), observability (F9)
**Evidência verificada**:
- `src/core/bedrock.py:50` — `self.client.invoke_model()` síncrono; `:78` — `time.sleep(delay)` no retry (bloqueia event loop por até 1+2+4=7s).
- `src/core/cache.py` — `redis.Redis` síncrono.
- `src/core/gitlab_client.py` — biblioteca `requests` (síncrona) em todo o arquivo.
- `src/core/docs_portal.py:12` — `httpx.Client` (síncrono), nunca fechado.
- `src/agents/observability/agent.py:109` + `server.py:47` — `httpx.get()` síncrono.
- `src/agents/finops/agent.py` — polling de Athena com `time.sleep(1)` × até 30 iterações = **30s de event loop travado**.
- `src/core/state_store.py` — `put_item`/`query` síncronos declarados como `async def` (mascaram bloqueio).

**Impacto**: com uvicorn single-worker, o sistema **serializa todos os requests**. É o maior bug de performance do projeto — pior que o cache quebrado.
**Fix**: `aioboto3` para Bedrock/DynamoDB, `httpx.AsyncClient` no gitlab/docs, `asyncio.to_thread()` para o kubernetes-client, `asyncio.sleep()` no lugar de `time.sleep()`.

### CONV-2 — Dependências falham fechadas (Redis + DynamoDB) ✅
**Flagueado por**: sre (R2, R3), dev (F6)
**Evidência verificada**: `src/core/cache.py` e `src/core/state_store.py` **não têm nenhum try/except**. `CacheStore.get/set/exists` e `ChatStorage.save/fetch_*` propagam `ConnectionError`/`ClientError` direto pro handler.
**Impacto**: Redis fora → todo request de agente falha (cache é dependência **não-crítica**). DynamoDB fora → o supervisor (`agent.py:50` faz `fetch_all_chats`) falha 100% das queries.
**Fix**: fail-open. Redis down = cache miss + log. DynamoDB down = histórico vazio (classifier e agentes seguem funcionando, só sem contexto).

### CONV-3 — Classifier é SPOF + caro (modelo errado) ✅
**Flagueado por**: sre (R1), finops (F1), aws (F11)
**Evidência verificada**: `src/core/classifier.py` chama `bedrock.invoke()` com o **mesmo** `settings.bedrock_model_id` (Sonnet 4.5) usado nas respostas. Toda query faz **2 invocações Bedrock**.
**Impacto duplo**:
- **Confiabilidade**: se o Bedrock estiver throttled, 100% das queries falham antes de chegar em qualquer agente. Sem fallback (regra por keyword, "último agente da sessão", ou perguntar ao usuário).
- **Custo**: Sonnet 4.5 para roteamento é ~10–13× mais caro que Haiku. ~$24/mês desperdiçados só no classifier @ 200 queries/dia.
**Fix**: `classifier_model_id` separado (Haiku 3.5) + fallback rule-based quando o Bedrock falha.

### CONV-4 — Health endpoints mentem ✅
**Flagueado por**: sre (R6), dev (F4)
**Evidência verificada**: todos os `server.py` têm `/health` retornando `{"status": "healthy"}` incondicional — nunca checa Redis, DynamoDB ou Bedrock.
**Impacto**: probes do K8s nunca detectam pod quebrado. Pod com Redis morto fica no Service endpoints e falha todo request. A spec `05-helm-chart` já assume `/healthz` + `/ready` que **não existem**.
**Fix**: `/healthz` (liveness, sempre 200 se o processo responde) + `/ready` (readiness, checa deps com timeout 2s + cache de 5s).

### CONV-5 — Bedrock blocking + retry sem jitter
**Flagueado por**: aws (F2), dev (F1), sre (R4)
**Evidência**: `bedrock.py` — retry hand-rolled sem jitter (thundering herd) e empilha **em cima** do retry default do botocore (até 9 chamadas reais por invocação lógica). `botocore` adaptive retry não está configurado.
**Fix**: `Config(retries={"mode": "adaptive", "max_attempts": 3})` + jitter no fallback de app + diferenciar erros transitórios de permanentes.

---

## Por domínio — achados NOVOS (não no AUDIT)

### DEV
- **F14 — `_load_prompt()` quebrado** ✅: `agent_base.py:18` resolve `Path(__file__).parent / "prompt.md"` = `src/core/prompt.md` (não existe). Todo agente que usa o método da **classe base** recebe o texto **fallback**, não o prompt real. Só funcionam os que reimplementam `_load_prompt()` local.
- **F5 — parsing frágil do classifier**: não trata JSON em markdown (` ```json `), JSON truncado por `max_tokens`, `confidence` como string, ou `selected_agent` fora do conjunto conhecido (→ `KeyError` no `AGENT_URLS[...]`).
- **F13 — sem `__init__.py`** em `src/` e subpacotes (depende de `PYTHONPATH=.` nos Dockerfiles).
- **F15 — bare `except:`** em `kubernetes/agent.py:25` (engole SystemExit/KeyboardInterrupt).
- **F4 — `@app.on_event` deprecado** (FastAPI 0.109+) → usar `lifespan`.

### SECURITY
- **SEC-D4 — read-only é PROMPT-ONLY** (HIGH): `docs/READ_ONLY_POLICY.md` promete 4 camadas, mas só a camada 1 (prompt) existe. O mount `~/.aws` dá ao agente as permissões **completas** do dev. `finops/agent.py` tem `boto3.client('athena')` que pode DROP database. Não há IAM deny nem RBAC K8s real.
- **SEC-D1/D2 — SSRF / exfiltração** (HIGH): `devops/agent.py` passa a query do usuário direto pro `gitlab_client.search_in_company()` sobre a **org inteira** ("Company"); resultados (código interno) entram no prompt do Bedrock. `docs_portal`/`PROMETHEUS_URL` configuráveis sem allowlist (risco de apontar pra `169.254.169.254`).
- **SEC-D3 — prompt injection mais fundo que S4**: o **próprio inventário é controlável pelo atacante** (nome de instância EC2 = payload). XML tags do S4 não bastam — precisa encoding de dados não-confiáveis + output filtering + usar a API `messages` corretamente (dados como documento, não instrução).
- **SEC-D12 — `user_id` auto-declarado** (MEDIUM): qualquer cliente reivindica qualquer `user_id`/`session_id` → acessa histórico de outros usuários no DynamoDB. Sem authn não há como validar.
- **SEC-D8 — MCP server gateway aberto**: porta 8006 sem auth, `user_id` hardcoded `"kiro-user"`, todas as queries MCP compartilham uma sessão DynamoDB.
- **SEC-D5/D6/D7** — secrets como env var inline (viola 12-factor), `python:3.12-alpine` não-pinado (devia ser `3.11-slim`), deps sem hash.

### OBSERVABILITY
- **F1 — 6/7 serviços sem instrumentação**: só `aws/server.py` chama `FastAPIInstrumentor`/`HTTPXClientInstrumentor`. Supervisor e os outros 4 agentes não.
- **F2 — propagação de trace quebrada**: `supervisor/agent.py` cria `httpx.AsyncClient` mas nunca instrumenta → não injeta `traceparent`. Cada agente inicia um trace **novo e desconexo**. O fan-out fica invisível no Tempo.
- **F4 — zero métricas de aplicação**: nenhum `MeterProvider`. `prometheus-client` no requirements nunca é importado. Sem RED, sem tokens/custo, sem cache hit ratio, sem confiança do classifier.
- **F5 — violação de cardinalidade**: `user_id`/`session_id` como span attributes (proibido por `observability-principles`).
- **F6 — `JSONFormatter` perde os `extra`** (confirma O2 do AUDIT com a causa exata: `hasattr(record, 'extra')` é sempre False no logging padrão).
- **F10 — sem OTel Collector no stack** (App → Collector → Backend é o único fluxo permitido).

### AWS
- **F3 — race no DynamoDB**: `put_item` sem `ConditionExpression`; SK = `agent#timestamp` → 2 mensagens no mesmo microssegundo se sobrescrevem.
- **F5 — query sem paginação**: `fetch_chat`/`fetch_all_chats` leem 1 página; resposta LLM grande pode estourar 1MB e perder mensagens silenciosamente.
- **F6 — `ec2.describe_instances()` sem paginação** + bloqueia event loop.
- **F4 — TTL 24h hardcoded** (curto demais, não configurável).
- **F1 — sem `boto3.Session` compartilhada**: 8 clients independentes, refresh de token IRSA redundante.
- **F9/F12 — sem validação de boot**: nem do acesso ao modelo Bedrock, nem da existência da tabela DynamoDB → falha no primeiro request, não no boot.
- **F10 — client `ce` morto** no aws-agent (amplia escopo IAM à toa).

### FINOPS
- **F2 — prompt caching desligado** ✅: `bedrock.py:28-29` comentado. System prompt (~6400 tokens) reenviado a cada chamada. **~$103/mês desperdiçados** @ 200 queries/dia (caching dá 90% de desconto no input cacheado).
- **Custo por query**: ~$0.026–0.052 (Sonnet). Modelo de custo otimizado: **$207→$99/mês** (Haiku no classifier + caching + scale-to-zero).
- **F7 — RAG tem ROI negativo no volume atual**: README estima +$1327–5595/mês. A $0.22/query de overhead de infra. Adiar até >2000 queries/dia; usar injeção de contexto estático (~$0.005/query) antes disso.
- **F5 — 13 pods always-on** para um ChatOps de baixo tráfego → KEDA scale-to-zero economiza ~$28/mês.

### GITOPS
- **F8 — ZERO CI/CD** (HIGH): docs referenciam GitLab CI, mas o repo é **GitHub** (`github.com:karlipegomes/AIgent-squad`). Não existe `.gitlab-ci.yml` nem `.github/workflows/`. **Nada builda as imagens que a spec `05-helm-chart` assume existir.** É o elo perdido entre código e deploy.
- **F4 — Dockerfiles**: single-stage, `python:3.12-alpine` (devia ser `3.11-slim`), sem multi-arch (BDC roda Graviton/arm64), sem `USER`.
- **F3 — cada imagem contém `src/` inteiro** (todos os 5 agentes) — superfície + tamanho.
- **F5 — `.gitignore` ignora `.dockerignore`** (lógica invertida) → build context manda o repo todo.
- **F9 — `requirements.txt` monolítico** → cada imagem ~400MB+ (k8s+slack+mcp em todas), cold-start lento pro KEDA.
- **F2 — `dynamodb-local` sem healthcheck** → supervisor sobe antes do DynamoDB estar pronto.

### SRE
- **R5 — sem graceful shutdown nos agentes**: só o supervisor tem hook. SIGTERM nos 5 especialistas aborta Bedrock no meio, vaza conexões → connection reset a cada rollout no EKS.
- **R7 — sem circuit breaker**: agente morto → toda query classificada pra ele espera 25s de timeout. Com pool de 100 conexões, 100 queries concorrentes esgotam o pool e travam até as chamadas saudáveis.
- **R8 — supervisor não verifica disponibilidade dos agentes no boot** (URLs hardcoded, sem probe).
- **R9 — sem correlation ID** propagado supervisor→agente.

### DOCUMENTATION
- **D6/D10 — docs fantasma** ✅: `CHANGES.md` e `docs/MIGRATION.md` mandam rodar `server_new.py` (5 arquivos) e `cd terraform/` — **nada disso existe** (o `IMPLEMENTATION_HISTORY.md` até diz que removeu os `server_new.py`).
- **D7 — "v2.0" em 10 arquivos** vs "0.x pre-release" do README (23 matches de `v2.0`). Dissonância: o projeto é simultaneamente pre-release e "Production Ready" dependendo do arquivo.
- **D8 — encoding corrompido em 7 arquivos** (AUDIT D4 só viu 2): "docker-compoif", "Responif", "sefordo" — find/replace quebrado (`se`→`if`, `re`→`this`).
- **D9 — colisão de porta**: `docs/LOCAL_DEVELOPMENT.md` lista DynamoDB Local na 8001 (devia ser 8100).
- **D11 — sem `CHANGELOG.md`** (Keep a Changelog) apesar do ROADMAP exigir.
- **D13 — `docs/PREREQUISITES.md` descreve schema DynamoDB errado** (`session_id` em vez de `pk`+`sk`).

---

## Novas specs propostas

Ordenadas por dependência. As 4 specs do AUDIT (01–04) continuam sendo o pré-requisito; estas **estendem** o roadmap.

| # | Spec proposta | Origem | Severidade | Depende de |
|---|---------------|--------|-----------|------------|
| **06** | `resilience-patterns` — timeouts, retries c/ jitter, circuit breaker, fail-open Redis/DynamoDB, **async-first refactor** (`aioboto3`/`httpx.AsyncClient`/`asyncio.to_thread`), classifier fallback | CONV-1, CONV-2, CONV-3, CONV-5, sre R1–R9, dev F1–F12 | 🔴 | 02 |
| **07** | `readiness-probes` — `/healthz` (liveness) + `/ready` (checa deps) + graceful shutdown (`lifespan`, flush OTel, drain) | CONV-4, sre R5/R6, dev F4 | 🔴 | 02 (pré-req de 05) |
| **08** | `ci-cd-pipeline` — GitHub Actions (test→build-dev→demo→release), multi-arch amd64+arm64, Trivy, cosign, SBOM, coverage gate 90%, push ECR/Harbor | gitops F8, AUDIT "sem testes" | 🔴 | 01 |
| **09** | `otel-instrumentation` — `setup_telemetry(service_name)`, instrumentar 7 serviços, propagação de trace, OTel Collector no stack, sampler explícito | obs F1–F12 | 🟠 | 02 |
| **10** | `metrics-and-cost-observability` — catálogo RED + LLM (tokens, **custo $**, confiança do classifier, cache hit), emitido via OTel; dashboards Grafana | obs (metrics-catalog), finops F-cost-obs | 🟠 | 09 |
| **11** | `bedrock-resilience-cost` — Haiku no classifier, **prompt caching** (re-ligar), model tiering, cross-region failover, token budget por sessão | CONV-3, finops F1/F2, aws F2/F11 Prop3 | 🟠 | 06 |
| **12** | `terraform-infra` — DynamoDB (PITR, TTL habilitado, GSIs), ElastiCache, 7 ECR repos, IRSA, Secrets Manager, S3 Athena | aws Prop1/Prop4, ROADMAP Fase 2 | 🟠 | — |
| **13** | `iam-least-privilege` — política read-only por agente + **deny explícito** (terminate/delete/iam:*), só 3/7 serviços precisam de AWS além de Bedrock | SEC-D4, aws Prop2 | 🟠 | 12 |
| **14** | `security-hardening` — threat model (STRIDE), authn/authz + audit log, NetworkPolicy + Istio mTLS, **prompt-injection guardrails** (input scan + context isolation + output filter + canary) | SEC-D1/D2/D3/D8/D12, sec Prop1–4 | 🟠 | 04 |
| **15** | `sli-slo-framework` — SLIs (disponibilidade, latência p95, taxa de sucesso do classifier/agente), SLOs Tier-3, error budget, burn-rate alerts | sre Prop2, obs Prop3 | 🟡 | 10 |
| **16** | `incident-runbooks` — playbooks (agente down, Bedrock throttled, Redis/DynamoDB down, alta latência, outage total) | sre Prop4 | 🟡 | 06, 07 |
| **17** | `multi-agent-collaboration` — classifier multi-agente, **fan-out/fan-in paralelo** p/ queries cross-domain + síntese, **agent-as-tools** (1 salto) | ANALYSIS "comportamentos novos" | 🟢 | 06, 09 |
| **18** | `rca-investigation-workflow` — **o diferencial**: sintoma → fan-out de evidência → timeline → correlação cross-signal → RCA (confiança + evidência + prevenção). Read-only | ganho esperado (RCA), `investigation-protocol` | 🟢 | 17, 09, 19 |
| **19** | `config-driven-platform` — config file (YAML) + env override + secrets fora do YAML; registry de agentes/datasources/limites/modelos por papel. **Pré-req de produto** | requisito do usuário, hardcodes | 🟠 | — |
| ~~**20**~~ | ~~`grpc-inter-agent-mesh`~~ — **REMOVIDA** (decisão 2026-06-02): ganho de latência irrelevante (~3ms sobre chamadas de modelo de ~5s). gRPC não se justifica por velocidade de RCA | — | ❌ | — |
| **21** | `incident-memory-learning` — persiste investigações `{assinatura, evidência, root cause, fix}` + recupera incidentes similares. Loop de aprendizado **simples** (sem Knowledge Base cara) | ganho esperado (aprendizado) | 🟢 | 18 |
| **22** | `agent-capability-manifest` — roster **aberto** de especialistas via manifesto YAML; colaboração dirigida por metadados (`capabilities`, `evidence_types`, `delegates_to`). **Substitui a 19** | requisito do usuário (roster aberto + colaboração via metadados) | 🟠 | — |
| **23** | `test-harness-docker` — `Dockerfile.test` + comando único: `pytest --cov-fail-under=90` com mocks (fakeredis/moto/respx), sem serviços reais; mesmo harness dev↔CI | requisito do usuário (testes via Dockerfile), `dev-environment` | 🔴 | — |
| **24** | `docs-portal-mkdocs` — consolidar docs espalhadas num portal MkDocs Material (`src`→`public`, estilo portal DevOps); README vira índice; ADRs; mata docs fantasma/encoding | requisito do usuário (organizar docs open-source), D6–D15 | 🟠 | — |

> **Ver [`ECOSYSTEM.md`](ECOSYSTEM.md)**: decisão tomada (2026-06-02) = **manter o AIgent-squad SEPARADO** (não merge, não virar coletor do chaitops). Specs como 14/17/19/21 existem mais maduras no `staffops-chaitops` e devem ser **reusadas por cópia** (AgentRegistry, BudgetGuard, KbDelta, contrato de payload do anomaly-detection), não por dependência. gRPC (20) removida. Docs em inglês.

### Testes & verificação (transversal — afeta toda spec com código)

Estado atual ✅ verificado: **zero** testes, sem `pytest`/`tox`, sem CI (`.github`/`.gitlab-ci.yml`), sem deps de teste no `requirements.txt`. Isso é o pré-requisito de verificação do steering global — bloqueia declarar qualquer coisa "done". O **harness dockerizado** que operacionaliza tudo isso é a spec **`23-test-harness-docker`**.

Regras a aplicar em **todas** as specs 06–22 que tocam código:
- **Cobertura ≥90%** medida via Docker (`pytest --cov=<pkg> --cov-fail-under=90`, `python:3.11-slim`) — gate de build, não meta aspiracional (steering `dev-environment.md`).
- **Autor do código ≠ autor dos testes** — implementação e testes em sessões/agents diferentes; testes escritos contra o contrato/spec, não contra a implementação (steering global `verification-independence.md`).
- **Review independente obrigatório** via subagent `code-review` — não basta "o teste passou".
- A spec **08-ci-cd-pipeline** materializa o gate (coverage gate no GitHub Actions); o pipeline de 3 stages (`implement` → `write-tests` → `review`) é o operacional do dia-a-dia.

Suíte mínima de maior valor (prioridade por branch/error-path): parsing do classifier (JSON em markdown, truncado, agente desconhecido), determinismo da cache key (sha256), contrato de resposta (`{role,content,timestamp,agent_id}`), roteamento do supervisor (timeout/500/fallback), retry do Bedrock, fail-open de Redis/DynamoDB.

### Comportamentos novos (não-spec, embutir nas specs acima)
- **Async-first** é a mudança de comportamento mais impactante — pertence à 06, mas atravessa todo o código.
- **Fail-open** em todas as dependências não-críticas (Redis, histórico) — invariante a adicionar no `steering/project.md`.
- **Model tiering** (Haiku roteia, Sonnet responde) — muda o contrato do `bedrock.invoke()`.
- **Streaming de respostas** (já há `AsyncIterable` no `agent_base`, nunca implementado) — UX e percepção de latência.
- **Agent-as-tools + colaboração cross-domain** — agora especificado em **`17-multi-agent-collaboration`** (fan-out/fan-in paralelo + síntese; agent-as-tools com 1 salto). Depende de 06 (async) e 09 (trace).

### Documentação (transversal)
- **Restruturar docs** (Diátaxis): 11 docs sobrepostos → ~8 focados. Deletar `CHANGES.md`, `VERSIONS.md`, `GENERIC_VERSION.md`, `MIGRATION.md` (fantasma).
- **ADR log**: 001-remoção-LangGraph, 002-Bedrock-direto, 003-design-read-only, 004-classifier-vs-tool-use, 005-HTTP-per-agent-vs-in-process.
- **`CHANGELOG.md`** Keep a Changelog + reset honesto para `0.1.0`.
- **Audit de Rationale (Nível 3)** nas specs 01–04 antes de implementar.

---

## Recomendação de priorização

1. **AUDIT 01** (blockers) — sem isso nada builda. *Imutável.*
2. **Spec 06 (resilience/async)** + **02 (unify)** juntas — o async-first refactor é mais barato de fazer durante a unificação dos agentes do que depois.
3. **Spec 07 (probes)** — pré-requisito de código que a `05-helm-chart` já assume.
4. **Spec 08 (CI/CD)** — em paralelo; é o elo perdido entre código e deploy, e traz o coverage gate que cobre a lacuna "zero testes".
5. **09/11** (telemetria + Bedrock cost) — antes do deploy real, senão sobe cego e caro.
6. **12/13/14** (infra + IAM + security) — Fase 2, junto com 05-helm-chart.
7. **15/16** (SLO + runbooks) — quando houver tráfego real pra medir.

> O AUDIT respondeu *"por que não roda"*. Esta análise responde *"por que, mesmo rodando, não está pronto pra produção"*: sem concorrência, falha fechada, cego e indefensável. As specs 06–14 fecham essa distância.
