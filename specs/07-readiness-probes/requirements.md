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
**Severity**: 🔴 High (code prerequisite that `05-helm-chart` already assumes)
**Origin**: `../ANALYSIS.md` CONV-4, sre R5/R6, dev F4
**Depends on**: `02-unify-agent-architecture` (complements `06` on graceful shutdown)

Today every service has only `/health` that returns `{"status":"healthy"}` unconditionally — it lies. K8s never detects a broken pod. This spec separates **liveness** (process alive) from **readiness** (can serve — dependencies OK).

## User Stories

WHEN K8s checks liveness (`/healthz`) THEN SHALL return 200 if the process responds (without checking dependencies) — restart only on deadlock.

WHEN K8s checks readiness (`/ready`) THEN SHALL check dependencies (Redis ping, Bedrock/credentials, DynamoDB for the supervisor) and return 503 with detail if any critical one is down.

WHEN a dependency check is performed THEN SHALL have a short timeout (2s) and the result SHALL be cached (~5s) to avoid hammering the dependency at every probe.

WHEN the pod receives SIGTERM THEN SHALL drain in-flight requests, flush OTel and close connections (aligns with `06`).

## Acceptance Criteria

- [ ] `/healthz` (liveness): 200 if the process responds; no dependency check.
- [ ] `/ready` (readiness): supervisor checks Redis+DynamoDB(+≥1 agent); agents check Redis+Bedrock credentials; 503+JSON detailing the failing dependency.
- [ ] 2s timeout per check; result cached ~5s.
- [ ] `/health` legacy maintained (or redirected) for compat.
- [ ] docker-compose healthchecks migrated to `/ready`.
- [ ] Graceful shutdown via `lifespan` (shared with `06`).
- [ ] Tests (test-author ≠ author, ≥90%): readiness 503 when dep is down (mock), liveness 200 always, check caching, shutdown drains.

## Out of scope
- Fail-open logic itself → spec 06.
- Manifests/probes Helm → spec 05 (this delivers the endpoints it consumes).
