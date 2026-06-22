# Design: Resilience Patterns + Async-First

## Arquitetura

Duas camadas sobre os agentes unificados (spec 02):

```
async-first        → todo I/O não-bloqueante; chamadas ao Bedrock paralelizáveis
   └── resiliência → fail-open (Redis/DynamoDB) · circuit breaker (agentes) ·
                     classifier fallback · retry c/ jitter · graceful shutdown
```

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| Async clients | Bedrock/DynamoDB via `aioboto3` ou `asyncio.to_thread`; HTTP/GitLab via `httpx.AsyncClient` | `src/core/{bedrock,state_store,gitlab_client}.py` |
| Fail-open wrappers | Redis/DynamoDB: erro → degradação, não exceção | `src/core/{cache,state_store}.py` |
| Classifier fallback | rule/keyword OU último agente OU pedir escolha | `src/core/classifier.py` |
| CircuitBreaker | por agente; closed→open→half-open | `src/core/circuit_breaker.py` (novo) |
| Retry+jitter | backoff com jitter + botocore adaptive | `src/core/bedrock.py` |
| Lifespan | graceful shutdown (drain, flush OTel, close pools) | cada `server.py` |

## RATIONALE CRÍTICO: de onde vem a velocidade de RCA

> Registro da análise de 2026-06-02 (decisão de não investir em comunicação/gRPC/topologia).

**O custo dominante é a chamada ao modelo (~5s). Tudo o mais é ruído.**

| Operação | Ordem de grandeza |
|----------|-------------------|
| Chamada Bedrock/Claude/Kiro | **~2.000–8.000 ms** |
| Boot de CLI-sidecar (modelo chaitops) | ~2.000–15.000 ms |
| HTTP entre pods | ~1–3 ms |
| gRPC / mesmo pod (localhost) | ~0,1–2 ms |

**Conclusão — a velocidade de RCA vem de paralelizar as chamadas ao modelo, não de acelerar o encanamento:**

| Estratégia | 1 RCA com 5 agentes | Natureza |
|-----------|---------------------|----------|
| Síncrono/bloqueante (código atual) | 5 × 5s = **25s** | ❌ serial |
| **Async fan-out, 1 réplica** | **~5s** (5 chamadas esperam juntas) | ✅ **o ganho** |
| gRPC vs HTTP | ~5s nos dois | irrelevante (Δ ms) |
| Mesmo pod vs pods distintos | ~5s nos dois | irrelevante (Δ ms) + acopla escala/falha |
| 10 réplicas | ainda ~5s **para UMA** RCA | não acelera 1 investigação |

**Async = velocidade. Réplicas = vazão. Topologia de pod = irrelevante (e mesmo-pod atrapalha: acopla escala e falha).**

- **Async (esta spec) + fan-out (17)**: paralelizam as N chamadas ao Bedrock → 25s vira ~5s. É o único ganho de tempo de 1 RCA.
- **Réplicas**: NÃO aceleram 1 RCA (uma investigação não se divide entre réplicas). Servem pra **vazão** — muitos alertas simultâneos. E com código async, **1 réplica** já aguenta muitas RCAs concorrentes (o tempo é quase todo I/O wait esperando o Bedrock; durante a espera o processo trabalha em outras). Réplicas só entram quando 1 réplica satura (CPU/mem ou cota de throughput do Bedrock).
- **gRPC / mesmo pod**: ganho de ms sobre operação de segundos = 0,06%. Não construir por velocidade (gRPC removido — ver `ECOSYSTEM.md`).

### Por que async é PRÉ-REQUISITO (não opcional)
`asyncio.gather` só paraleliza se as chamadas **liberarem o event loop** durante a espera (I/O await). Hoje (`bedrock.py` síncrono + `time.sleep`) o loop fica **bloqueado** — `gather` de chamadas bloqueantes roda **em série**. Sem async, o fan-out da spec 17 é uma ilusão: parece paralelo, executa serial. Por isso 06 vem antes de 17/18.

## Decisões e trade-offs

### Decisão 1: Fail-open em dependências não-críticas (Redis, histórico)
**Escolha**: Redis/DynamoDB indisponíveis → degradar (cache miss / histórico vazio), não falhar.
**Justificativa**: cache e histórico são *aceleradores*, não fonte de verdade. Falhar a query inteira por causa deles = outage auto-infligido (CONV-2). RCA tem que funcionar mesmo com backing degradado.
**Trade-off**: respostas sem contexto/cache durante o outage — aceitável (degradação > indisponibilidade).
**Quando reabrir**: se alguma dependência virar fonte de verdade (não é o caso).

### Decisão 2: `asyncio.to_thread` para boto3, não migração total pra aioboto3 já
**Escolha**: onde houver cliente async maduro, usar; senão, `asyncio.to_thread()` em volta do boto3 síncrono.
**Justificativa**: `to_thread` resolve o bloqueio do event loop com mudança mínima e baixo risco; migrar tudo pra `aioboto3` de uma vez é refactor grande e arriscado pré-MVP.
**Trade-off**: threads custam um pouco mais que async nativo — irrelevante no volume atual.
**Quando reabrir**: se o volume crescer a ponto de o overhead de thread pesar → migrar os hot paths pra aioboto3.

## Invariantes
- Nenhum I/O bloqueante em path `async`; zero `time.sleep` async.
- Dependência não-crítica **nunca** derruba a query (fail-open).
- Classifier **nunca** causa falha total (sempre há fallback).
- Circuit breaker e timeouts **configuráveis** (specs 19/22).

## Dependências externas
| Lib | Uso |
|-----|-----|
| `aioboto3` / botocore `Config(retries=adaptive)` | Bedrock/DynamoDB async + retry |
| `httpx.AsyncClient` | HTTP agentes / GitLab / docs |
| `fakeredis`, `respx`, `moto` | testes offline (spec 23) |

## Verificação
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Teste decisivo: `gather` de 3 chamadas mockadas com `sleep(0.5)` completa em ~0.5s (paralelo), não ~1.5s (serial).

## Riscos
- `to_thread` mal aplicado ainda bloqueia → cobrir com teste de paralelismo (tempo≈max).
- Circuit breaker mal calibrado abre cedo demais → thresholds configuráveis + métricas.
- Fail-open mascara problema real → sempre logar + emitir métrica no caminho degradado.
