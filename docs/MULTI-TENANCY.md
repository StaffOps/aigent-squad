# Multi-Tenancy & Concurrency

How aigent-squad isolates users, controls costs, and scales under concurrent load.

## Session Isolation

Each agent conversation is isolated via DynamoDB-backed session history:

- **Key**: `user_id + session_id` (composite partition key)
- **TTL**: Sessions expire after `SESSION_TTL_HOURS` (default: 24h)
- **History**: Each session stores its own message history — no cross-session bleed
- **Agents**: Different agents within the same session maintain independent histories

This ensures that User A's investigation context never leaks into User B's queries, even when both hit the same agent simultaneously.

## Rate Limiting

Sliding-window rate limiting protects against abuse and ensures fair resource distribution:

| Scope | Default | Env Var | Behavior on limit |
|-------|---------|---------|-------------------|
| Per-user | 30 req/min | `RATE_LIMIT_PER_USER` | HTTP 429 + `Retry-After` header |
| Global | 200 req/min | `RATE_LIMIT_GLOBAL` | HTTP 429 (all users) |

**Implementation**: Redis-backed sliding window (`ZRANGEBYSCORE` on timestamp-scored sets). Falls back to in-memory counter if Redis is unavailable (fail-open — availability over strictness).

## Budget Control

Daily USD cap prevents runaway LLM costs:

| Parameter | Default | Env Var |
|-----------|---------|---------|
| Daily budget | $50.00 | `DAILY_BUDGET_USD` |
| Cost estimator | Per-model token pricing | `MODEL_COST_PER_1K_INPUT` / `MODEL_COST_PER_1K_OUTPUT` |

**Flow**:
1. Before each Bedrock call, the cost estimator projects the request cost (input tokens × rate)
2. Running daily total is checked against cap
3. If projected total > cap → reject with HTTP 402 and informative message
4. After response, actual cost is recorded (output tokens counted)

**Reset**: Daily counter resets at midnight UTC.

## Session Lock

Prevents concurrent writes to the same session (race condition on DynamoDB):

- **Mechanism**: Redis `SETNX` with TTL = request timeout + buffer
- **Key**: `lock:{user_id}:{session_id}`
- **Conflict**: If lock exists → HTTP 409 Conflict ("session is being processed")
- **Fail-open**: If Redis is unavailable, requests proceed without locking (correctness degrades gracefully)
- **Auto-release**: TTL ensures locks don't persist after crashes

## Circuit Breaker

Protects against cascading failures when Bedrock or backing services degrade:

| Parameter | Default | Env Var |
|-----------|---------|---------|
| Failure threshold | 5 consecutive failures | `CIRCUIT_BREAKER_THRESHOLD` |
| Recovery timeout | 30s | `CIRCUIT_BREAKER_TIMEOUT` |
| Half-open probes | 1 request | — |

**States**: Closed → Open (after N failures) → Half-Open (after timeout) → Closed (on success)

**Storage**: Redis-backed (`circuit:{service_name}` key). Fail-open if Redis unavailable.

## Bedrock Semaphore

Limits concurrent Bedrock API calls to avoid throttling:

| Parameter | Default | Env Var |
|-----------|---------|---------|
| Max concurrent calls | 10 | `BEDROCK_MAX_CONCURRENT` |

**Implementation**: `asyncio.Semaphore` (in-process). Each pod has its own semaphore — total cluster concurrency = `BEDROCK_MAX_CONCURRENT × pod_count`.

**Metrics emitted**:
- `aigent_bedrock_queue_depth` — current waiters in semaphore queue
- `aigent_bedrock_queue_wait_seconds` — time spent waiting for semaphore

## Scaling

| Mechanism | Trigger | Target |
|-----------|---------|--------|
| HPA | CPU > 70% | 2–10 pods |
| KEDA | `aigent_bedrock_queue_depth` > 5 | 2–20 pods |

**Recommended**: Use KEDA `ScaledObject` with the Prometheus trigger reading `aigent_bedrock_queue_depth` from VictoriaMetrics. This scales on actual demand (queue pressure) rather than CPU, which is more meaningful for I/O-bound LLM workloads.

```yaml
triggers:
  - type: prometheus
    metadata:
      serverAddress: http://vmselect:8481/select/0/prometheus
      query: avg(aigent_bedrock_queue_depth)
      threshold: "5"
```

## Environment Variables Summary

| Variable | Default | Purpose |
|----------|---------|---------|
| `SESSION_TTL_HOURS` | 24 | DynamoDB session expiry |
| `RATE_LIMIT_PER_USER` | 30 | Requests/min per user |
| `RATE_LIMIT_GLOBAL` | 200 | Requests/min cluster-wide |
| `DAILY_BUDGET_USD` | 50.0 | Daily cost cap |
| `BEDROCK_MAX_CONCURRENT` | 10 | Per-pod Bedrock concurrency |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | Failures before opening circuit |
| `CIRCUIT_BREAKER_TIMEOUT` | 30 | Seconds before half-open probe |
| `REDIS_URL` | `redis://localhost:6379` | Redis for locks, rate limits, circuit state |
