# Tasks: Health Probes + Graceful Shutdown

> Pré-requisito de código da `05-helm-chart`. Compartilha o `lifespan` com a `06`.

- [x] T1: `src/core/health.py` — `DependencyChecker` com timeout (2s) + cache (~5s) por dependência (2026-06-17)
- [x] T2: `/healthz` (liveness, 200 sempre que o processo responde) em supervisor e mcp-server (2026-06-17)
- [x] T3: `/ready` por papel — supervisor (Redis+DynamoDB+≥1 agente em memória), mcp (supervisor /healthz) (2026-06-17)
- [x] T4: Manter `/health` legado (alias para /healthz) em supervisor e mcp-server (2026-06-17)
- [x] T5: docker-compose healthchecks → `/ready` (supervisor + mcp-server) (2026-06-17)
- [x] T6: Graceful shutdown via `lifespan` — já implementado na spec 06; não duplicado (2026-06-17)
- [x] T7: pytest 237/237 passando, coverage 92.46% (gate 90%) — /ready 503 c/ dep fora, /healthz 200 sempre, cache não repinga (2026-06-17)
- [ ] T8: Review independente (`code-review`): `/healthz` puro (sem dep), `/ready` fail-closed para LB (depends on: T7) — deferred

## Ordem sugerida
T1; T2; T3→T4→T5; T6; T7→T8.

## Notas
- Liveness ≠ readiness: liveness puro evita restart-storm quando uma dep cai.
- App é fail-open para requests (06), mas `/ready` é fail-closed para o LB (tira o pod até recuperar).
- Pipeline de verificação (`verification-independence.md`): T1–T6 autor; T7 test-author; T8 code-review.
