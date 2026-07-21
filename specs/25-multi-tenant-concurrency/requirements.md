---
spec: 25-multi-tenant-concurrency
status: in-progress
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Multi-Tenant Concurrency

**Spec**: `25-multi-tenant-concurrency`
**Severidade**: 🟠 High (necessário para produção real com >5-10 usuários simultâneos)
**Origem**: análise de buracos em multi-conversation handling (chat session 2026-06-14)
**Depende de**: `06-resilience-patterns` (async já feito), `17-multi-agent-collaboration` (fan-out já feito)

O sistema atual atende bem 5-10 usuários simultâneos no mesmo pod, mas tem buracos para escala real: circuit breaker in-memory (não multi-replica safe), sem session locking (race em mensagens rápidas), sem rate limit / budget guard, sem semáforo no Bedrock (pode estourar TPS limit), e sem teste de carga.

Esta spec endereça os 5 buracos para tornar o sistema **multi-tenant production-ready**.

---

## User Stories

WHEN há N replicas do supervisor THEN o circuit breaker SHALL ter estado **compartilhado** (uma replica abre → todas respeitam imediatamente).

WHEN um usuário envia 2 mensagens muito rápido na mesma session THEN a segunda SHALL aguardar a primeira terminar (locking por session_id), evitando race no histórico.

WHEN um usuário ultrapassa o limite de queries/min OU custo/dia THEN o sistema SHALL retornar erro `429 Too Many Requests` com nota de quando voltar.

WHEN o custo total do dia ultrapassa o budget global THEN novas queries SHALL ser bloqueadas (`503 Service Unavailable`) até o reset diário.

WHEN há muitas chamadas Bedrock simultâneas THEN o sistema SHALL serializar via semáforo (default: 10 concorrentes) — evita estourar TPS.

WHEN N usuários simultâneos enviam queries THEN o sistema SHALL processar todos sem cross-contamination de contexto (já testado isolamento; agora **load test** com k6 confirma).

WHEN a load test roda 100 usuários × 10 queries simultâneas THEN p99 < 10s e zero erros 5xx **NÃO causados pelo Bedrock** (rate limit do modelo é aceitável e cai em retry).

---

## Acceptance Criteria

### 1. Distributed circuit breaker
- [ ] CircuitBreaker state movido para Redis (key: `cb:<name>:state`, `cb:<name>:failures`, `cb:<name>:last_failure`)
- [ ] Lock atomic via Redis SETNX nas transições de estado
- [ ] Fail-open mantido: se Redis cair, breaker funciona em-memória local
- [ ] TTL na key (auto-cleanup após recovery_timeout * 2)

### 2. Session locking
- [ ] Lock no Redis (key: `lock:session:<session_id>`) via SETNX com TTL=30s
- [ ] Wait por até 5s se outro request tem o lock
- [ ] Liberado em finally (sempre) + TTL como fallback
- [ ] Se Redis cair: lock é skipped (degradação aceita)

### 3. Rate limit / budget
- [ ] Per-user rate: 60 queries/min (configurável) — Redis sliding window
- [ ] Global daily budget em USD (default: $50/dia) — Redis counter com TTL=24h
- [ ] Per-user daily soft cap: 20% do budget global por padrão
- [ ] Headers de resposta: `X-RateLimit-Remaining`, `X-Budget-Remaining-USD`
- [ ] Custo estimado **antes** da chamada (max_tokens + system prompt) para pre-check
- [ ] Métrica `aigent.rate_limit.blocks` (counter, labels: reason=user/global)

### 4. Bedrock semaphore
- [ ] `asyncio.Semaphore(10)` (configurável) no `BedrockClient`
- [ ] Métrica `aigent.bedrock.queue_depth` (gauge)
- [ ] Métrica `aigent.bedrock.queue_wait_ms` (histogram)
- [ ] Timeout de aquisição: 30s (raise antes de esperar pra sempre)

### 5. Load testing
- [ ] Script `tests/load/scenario_basic.js` (k6) — 50 usuários × queries variadas (single + cross-domain)
- [ ] Script `tests/load/scenario_burst.js` — 200 usuários × 30s (stress)
- [ ] CI workflow `.github/workflows/load.yml` (manual / nightly)
- [ ] Dashboard Grafana com KPIs: p50/p99 latência, error rate, throughput
- [ ] Documentação em `docs/LOAD-TESTING.md` com baseline esperado

### 6. Documentação
- [ ] `docs/MULTI-TENANCY.md` explicando isolamento, scaling, limits
- [ ] `docs/METRICS.md` atualizado com novas métricas (queue, rate_limit)

---

## Fora de escopo

- Quotas por organização/tenant complexas (multi-org com billing) — futuro
- LLM caching de respostas (cache de queries idênticas) — separado
- Queue persistente para queries (Kafka/SQS) — exagero pra esse perfil de uso
- Auto-scaling do supervisor (HPA) — já vem do Helm chart, não muda aqui
