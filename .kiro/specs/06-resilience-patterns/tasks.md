# Tasks: Resilience Patterns + Async-First

> Pré-requisito de velocidade de 17 (fan-out) e 18 (RCA). Sem async, o `gather` roda serial.

- [ ] T1: Tornar `bedrock.py` não-bloqueante (`asyncio.to_thread`/aioboto3); `time.sleep`→`asyncio.sleep`; retry c/ jitter + botocore `Config(retries=adaptive)`
- [ ] T2: Tornar `state_store.py` (DynamoDB) não-bloqueante + **fail-open** (fetch→[], save→log) (depends on: —)
- [ ] T3: `cache.py` (Redis) não-bloqueante + **fail-open** (get→None, set→swallow+log)
- [ ] T4: `gitlab_client.py` (requests→`httpx.AsyncClient`) + `docs_portal.py` async; pools por destino
- [ ] T5: Classifier fallback (rule/keyword OU último agente OU pedir escolha) quando Bedrock falha (depends on: T1)
- [ ] T6: `circuit_breaker.py` por agente (closed→open→half-open), thresholds configuráveis (depends on: —)
- [ ] T7: Supervisor — timeouts configuráveis + bulkhead (pool httpx por agente) + usa circuit breaker (depends on: T6)
- [ ] T8: Graceful shutdown via FastAPI `lifespan` (drain, flush OTel, close pools) nos 6 services
- [ ] T9 (test-author DIFERENTE do autor): pytest ≥90% — **paralelismo (tempo≈max)**, fail-open Redis/DynamoDB, classifier fallback, circuit breaker, retry+jitter (depends on: T1–T8)
- [ ] T10: Review independente (`code-review`): zero bloqueio em path async, fail-open correto, sem retry empilhado (depends on: T9)
- [ ] T11: Smoke via Docker — RCA mockada com 5 agentes completa em ~1× (paralelo), não 5× (depends on: T10)

## Ordem sugerida
T1/T2/T3/T4 em paralelo; T5 (após T1); T6→T7; T8; T9→T10→T11.

## Notas
- **Async é o pré-requisito de TODA a velocidade** (ver rationale no design): async=velocidade, réplicas=vazão, topologia de pod=irrelevante.
- Não migrar tudo pra aioboto3 de uma vez — `asyncio.to_thread` onde não houver client async maduro.
- Pipeline de verificação (`verification-independence.md`): T1–T8/T11 autor; T9 test-author em sessão diferente; T10 code-review.
- Test harness: spec 23 (mocks offline).
