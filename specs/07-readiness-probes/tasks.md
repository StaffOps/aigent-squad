# Tasks: Health Probes + Graceful Shutdown

> Code prerequisite for `05-helm-chart`. Shares the `lifespan` with `06`.

- [x] T1: `src/core/health.py` — `DependencyChecker` with timeout (2s) + cache (~5s) per dependency (2026-06-17)
- [x] T2: `/healthz` (liveness, 200 as long as the process responds) in supervisor and mcp-server (2026-06-17)
- [x] T3: `/ready` per role — supervisor (Redis+DynamoDB+≥1 in-memory agent), mcp (supervisor /healthz) (2026-06-17)
- [x] T4: Keep `/health` legacy (alias for /healthz) in supervisor and mcp-server (2026-06-17)
- [x] T5: docker-compose healthchecks → `/ready` (supervisor + mcp-server) (2026-06-17)
- [x] T6: Graceful shutdown via `lifespan` — already implemented in spec 06; not duplicated (2026-06-17)
- [x] T7: pytest 237/237 passing, coverage 92.46% (gate 90%) — /ready 503 with dep down, /healthz 200 always, cache doesn't re-ping (2026-06-17)
- [ ] T8: Independent review (`code-review`): `/healthz` pure (no dep), `/ready` fail-closed for LB (depends on: T7) — deferred

## Suggested order
T1; T2; T3→T4→T5; T6; T7→T8.

## Notes
- Liveness ≠ readiness: pure liveness avoids restart-storm when a dep goes down.
- App is fail-open for requests (06), but `/ready` is fail-closed for the LB (removes the pod until recovery).
- Verification pipeline (`verification-independence.md`): T1–T6 author; T7 test-author; T8 code-review.
