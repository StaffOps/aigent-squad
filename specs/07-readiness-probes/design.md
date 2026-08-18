# Design: Health Probes + Graceful Shutdown

## Architecture

Two endpoints with distinct semantics + cached dependency checks.

```
/healthz (liveness)  → 200 if the process responds. K8s restarts only on deadlock.
/ready   (readiness) → checks deps (timeout 2s, cache 5s). 503 if critical dep is down →
                        K8s removes pod from Service until recovery.
```

## Components

| Component | Responsibility | Where |
|-----------|----------------|-------|
| `/healthz`, `/ready` | endpoints per service | each `server.py` |
| `DependencyChecker` | ping deps with timeout + cache TTL | `src/core/health.py` (new) |
| Lifespan | graceful shutdown (shared with 06) | each `server.py` |

Checks per role:

| Service | `/ready` checks |
|---------|-----------------|
| supervisor | Redis ping · DynamoDB describe-table · ≥1 agent `/healthz` |
| agents | Redis ping · valid Bedrock credentials (sts:GetCallerIdentity) |
| mcp-server | supervisor reachable |

## Decisions and trade-offs

### Decision 1: Separate liveness from readiness (not a single `/health`)
**Choice**: `/healthz` never checks dependencies; `/ready` does.
**Justification**: mixing causes restart loops — if Redis goes down and `/health` (used as liveness) fails, K8s **restarts** the pod, which comes up and fails again (Redis still down). Liveness should only reflect "process alive"; readiness reflects "can serve". Separating avoids restart-storm and still correctly removes the pod from LB.
**Trade-off**: two endpoints instead of one — trivial.

### Decision 2: Cache the check result (~5s)
**Choice**: `/ready` doesn't ping deps on every probe request; caches ~5s.
**Justification**: probes run every few seconds × N replicas → without cache, unnecessary hammering of Redis/DynamoDB/STS. Short cache keeps the information fresh without cost.
**Trade-off**: up to ~5s of lag in detecting a failure — acceptable for probe intervals.

## Invariants
- `/healthz` **never** depends on an external service.
- `/ready` 503 when a **critical** dependency is down (fail-closed for traffic — the pod leaves the LB), but the app itself stays fail-open for requests (spec 06).
- Check with timeout (2s) — never hangs the probe.

## External dependencies
| Service | Usage in check |
|---------|----------------|
| Redis | `PING` |
| DynamoDB | `describe-table` (supervisor) |
| STS/Bedrock | `GetCallerIdentity` (agents) |

## Verification
```bash
docker run --rm -v "$PWD:/app" -w /app python:3.11-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt && pytest tests/ -v --cov=src --cov-fail-under=90"
```
Tests: `/ready` 503 with mocked Redis down; `/healthz` 200 even with dep down; second call uses cache (doesn't re-ping).

## Risks
- `/ready` slow (deps summed) → timeout per dep (2s) + parallelize checks + cache.
- Liveness accidentally checking dep → review ensures `/healthz` is pure.
