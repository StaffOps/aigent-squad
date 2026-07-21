---
spec: 07-readiness-probes
status: done
completed: 2026-06-17
superseded_by: null
depends_on: []
deferred: []
---

# Feature: Health Probes + Graceful Shutdown

**Spec**: `07-readiness-probes`
**Severidade**: 🔴 High (pré-requisito de código que a `05-helm-chart` já assume)
**Origem**: `../ANALYSIS.md` CONV-4, sre R5/R6, dev F4
**Depende de**: `02-unify-agent-architecture` (complementa `06` no graceful shutdown)

Hoje todo serviço tem só `/health` que retorna `{"status":"healthy"}` incondicional — mente. O K8s nunca detecta pod quebrado. Esta spec separa **liveness** (processo vivo) de **readiness** (consegue servir — dependências OK).

## User Stories

WHEN o K8s checa liveness (`/healthz`) THEN SHALL retornar 200 se o processo responde (sem checar dependências) — reinício só em deadlock.

WHEN o K8s checa readiness (`/ready`) THEN SHALL checar dependências (Redis ping, Bedrock/credenciais, DynamoDB para o supervisor) e retornar 503 com detalhe se alguma crítica estiver fora.

WHEN uma checagem de dependência é feita THEN SHALL ter timeout curto (2s) e o resultado SHALL ser cacheado (~5s) para não martelar a dependência a cada probe.

WHEN o pod recebe SIGTERM THEN SHALL drenar requests in-flight, flush OTel e fechar conexões (alinha `06`).

## Acceptance Criteria

- [ ] `/healthz` (liveness): 200 se o processo responde; sem checagem de dependência.
- [ ] `/ready` (readiness): supervisor checa Redis+DynamoDB(+≥1 agente); agentes checam Redis+credenciais Bedrock; 503+JSON detalhando a dependência fora.
- [ ] Timeout 2s por checagem; resultado cacheado ~5s.
- [ ] `/health` legado mantido (ou redirecionado) para compat.
- [ ] docker-compose healthchecks migram para `/ready`.
- [ ] Graceful shutdown via `lifespan` (compartilhado com `06`).
- [ ] Testes (test-author ≠ autor, ≥90%): readiness 503 quando dep fora (mock), liveness 200 sempre, cache da checagem, shutdown drena.

## Fora de escopo
- Lógica de fail-open em si → spec 06.
- Manifests/probes Helm → spec 05 (esta entrega os endpoints que ela consome).
