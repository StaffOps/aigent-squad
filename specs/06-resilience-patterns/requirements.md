# Feature: Resilience Patterns + Async-First

**Spec**: `06-resilience-patterns`
**Severidade**: 🔴 Critical (motor de velocidade + sobrevivência a falhas)
**Origem**: `../ANALYSIS.md` CONV-1 (I/O síncrono), CONV-2 (fail-closed), CONV-3 (classifier SPOF), CONV-5 (retry), sre R1–R9, dev F1–F12
**Depende de**: `02-unify-agent-architecture`

Duas coisas no mesmo pacote porque andam juntas: (1) **async-first** — todo I/O (Bedrock, DynamoDB, Redis, HTTP, GitLab) deixa de ser síncrono dentro de handlers `async`, destravando paralelismo real; (2) **resiliência** — fail-open em dependências não-críticas, circuit breaker, timeouts, fallback do classifier. Sem (1), o fan-out (spec 17) e a RCA (spec 18) **não paralelizam** — é o pré-requisito de toda a velocidade do produto.

## User Stories

WHEN N agentes são acionados numa investigação THEN as N chamadas ao Bedrock SHALL ocorrer **concorrentemente** (tempo total ≈ a mais lenta, não a soma).

WHEN o código faz I/O (Bedrock, DynamoDB, Redis, HTTP, GitLab) dentro de um handler `async` THEN ele SHALL **não bloquear o event loop** (via cliente async ou `asyncio.to_thread`); `time.sleep` SHALL virar `asyncio.sleep`.

WHEN o Redis está indisponível THEN o sistema SHALL tratar como cache miss e continuar (**fail-open**), logando o erro.

WHEN o DynamoDB está indisponível THEN o sistema SHALL prosseguir com histórico vazio (**fail-open**), sem derrubar a query.

WHEN o classifier (Bedrock) falha THEN o sistema SHALL aplicar **fallback** (keyword/rule OU último agente da sessão OU pedir escolha ao usuário), nunca falhar 100% das queries.

WHEN um agente especialista está fora/lento THEN o **circuit breaker** SHALL abrir após N falhas e retornar rápido ("agente indisponível"), sem esperar o timeout completo a cada query.

WHEN uma chamada ao Bedrock é reententada THEN o backoff SHALL ter **jitter** e respeitar o retry adaptativo do botocore (sem empilhar retries).

## Acceptance Criteria

- [ ] Bedrock, DynamoDB, Redis, GitLab, docs portal acessados de forma **não-bloqueante** (cliente async ou `asyncio.to_thread`); zero `time.sleep` em path async.
- [ ] `asyncio.gather` comprovado paralelo: teste com clients mockados (sleep) mostra tempo ≈ max, não soma.
- [ ] Redis fail-open (get→None, set→swallow+log) — testado com Redis indisponível.
- [ ] DynamoDB fail-open (fetch→[], save→log) — testado com tabela inacessível.
- [ ] Classifier fallback determinístico quando Bedrock falha — testado.
- [ ] Circuit breaker por agente (closed→open→half-open) com thresholds **configuráveis** (spec 19/22).
- [ ] Timeouts configuráveis por chamada (agente, Bedrock); pool httpx por agente (bulkhead).
- [ ] Retry com jitter + botocore adaptive (`mode=adaptive`); sem retry empilhado.
- [ ] Graceful shutdown (SIGTERM): drain in-flight, flush OTel, fecha conexões (FastAPI `lifespan`).
- [ ] Testes (test-author ≠ autor, ≥90%): paralelismo, fail-open Redis/DynamoDB, classifier fallback, circuit breaker, retry+jitter.

## Fora de escopo

- Health endpoints `/healthz`+`/ready` → spec 07 (complementar).
- Escolha de modelo (Haiku no classifier) → spec 11.
- Multi-agente / fan-out em si → spec 17 (esta spec só garante que ele paraleliza de verdade).
