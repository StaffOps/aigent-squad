# Tasks: Health Probes + Graceful Shutdown

> Pré-requisito de código da `05-helm-chart`. Compartilha o `lifespan` com a `06`.

- [ ] T1: `src/core/health.py` — `DependencyChecker` com timeout (2s) + cache (~5s) por dependência
- [ ] T2: `/healthz` (liveness, 200 sempre que o processo responde) nos 6 services (depends on: —)
- [ ] T3: `/ready` por papel — supervisor (Redis+DynamoDB+≥1 agente), agentes (Redis+Bedrock creds), mcp (supervisor) (depends on: T1)
- [ ] T4: Manter `/health` legado (redirect/alias) para compat (depends on: T2)
- [ ] T5: docker-compose healthchecks → `/ready` (depends on: T3)
- [ ] T6: Graceful shutdown via `lifespan` (coordenar com `06` para não duplicar) (depends on: —)
- [ ] T7 (test-author DIFERENTE do autor): pytest ≥90% — readiness 503 c/ dep fora, liveness 200 sempre, cache da checagem, shutdown drena (depends on: T2, T3, T6)
- [ ] T8: Review independente (`code-review`): `/healthz` puro (sem dep), `/ready` fail-closed para LB (depends on: T7)

## Ordem sugerida
T1; T2; T3→T4→T5; T6; T7→T8.

## Notas
- Liveness ≠ readiness: liveness puro evita restart-storm quando uma dep cai.
- App é fail-open para requests (06), mas `/ready` é fail-closed para o LB (tira o pod até recuperar).
- Pipeline de verificação (`verification-independence.md`): T1–T6 autor; T7 test-author; T8 code-review.
