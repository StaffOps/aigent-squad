# Design: Health Probes + Graceful Shutdown

## Arquitetura

Dois endpoints com semânticas distintas + checagem de dependência cacheada.

```
/healthz (liveness)  → 200 se o processo responde. K8s reinicia só em deadlock.
/ready   (readiness) → checa deps (timeout 2s, cache 5s). 503 se crítica fora →
                        K8s tira o pod do Service até recuperar.
```

## Componentes

| Componente | Responsabilidade | Onde |
|-----------|------------------|------|
| `/healthz`, `/ready` | endpoints por serviço | cada `server.py` |
| `DependencyChecker` | ping de deps com timeout + cache TTL | `src/core/health.py` (novo) |
| Lifespan | graceful shutdown (compartilhado c/ 06) | cada `server.py` |

Checagem por papel:

| Serviço | `/ready` checa |
|---------|----------------|
| supervisor | Redis ping · DynamoDB describe-table · ≥1 agente `/healthz` |
| agentes | Redis ping · credenciais Bedrock válidas (sts:GetCallerIdentity) |
| mcp-server | supervisor alcançável |

## Decisões e trade-offs

### Decisão 1: Separar liveness de readiness (não um `/health` único)
**Escolha**: `/healthz` nunca checa dependência; `/ready` checa.
**Justificativa**: misturar causa reinício em loop — se Redis cai e o `/health` (usado como liveness) falha, o K8s **reinicia** o pod, que sobe e cai de novo (Redis continua fora). Liveness deve refletir só "processo vivo"; readiness reflete "consigo servir". Separar evita restart-storm e ainda tira o pod do LB corretamente.
**Trade-off**: dois endpoints em vez de um — trivial.

### Decisão 2: Cachear o resultado da checagem (~5s)
**Escolha**: `/ready` não pinga as deps a cada request de probe; cacheia ~5s.
**Justificativa**: probes rodam a cada poucos segundos × N réplicas → sem cache, marteladas desnecessárias em Redis/DynamoDB/STS. Cache curto mantém a informação fresca sem custo.
**Trade-off**: até ~5s de defasagem na detecção — aceitável pro intervalo de probe.

## Invariantes
- `/healthz` **nunca** depende de serviço externo.
- `/ready` 503 quando dependência **crítica** está fora (fail-closed para tráfego — o pod sai do LB), mas a app em si segue fail-open para requests (spec 06).
- Checagem com timeout (2s) — nunca trava a probe.

## Dependências externas
| Serviço | Uso na checagem |
|---------|-----------------|
| Redis | `PING` |
| DynamoDB | `describe-table` (supervisor) |
| STS/Bedrock | `GetCallerIdentity` (agentes) |

## Verificação
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Testes: `/ready` 503 com Redis mockado fora; `/healthz` 200 mesmo com dep fora; segunda chamada usa cache (não repinga).

## Riscos
- `/ready` lento (deps somadas) → timeout por dep (2s) + paralelizar checagens + cache.
- Liveness acidentalmente checando dep → revisão garante `/healthz` puro.
