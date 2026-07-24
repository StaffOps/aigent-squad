# Design: Multi-Tenant Concurrency

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      N Supervisor Pods                       │
│                    (auto-scaled via HPA)                     │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  RateLimiter (per-user + global) ◄──── Redis        │   │
│  │  SessionLock (per session_id)    ◄──── Redis SETNX   │   │
│  │  CircuitBreaker (Bedrock)        ◄──── Redis state   │   │
│  │  Bedrock Semaphore (concurrency)        in-memory    │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
                ┌────────────────────────┐
                │  Redis (shared state)  │
                │  - Circuit breaker     │
                │  - Session locks       │
                │  - Rate counters       │
                │  - Budget counter      │
                └────────────────────────┘
```

## Components

### 1. Distributed Circuit Breaker (`src/core/circuit_breaker.py`)

Moves state to Redis. Maintains in-memory fallback if Redis is down.

```python
class CircuitBreaker:
    def __init__(self, name, failure_threshold=5, recovery_timeout=30.0, redis_client=None):
        self.name = name
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.redis = redis_client  # optional; without it = in-memory
        self._local_state = CircuitState.CLOSED  # fallback

    async def can_execute(self) -> bool:
        if not self.redis:
            return self._local_can_execute()
        try:
            state = await self._redis_get_state()
            return state != CircuitState.OPEN or self._past_recovery()
        except Exception:
            return self._local_can_execute()  # fail-open
```

**Redis keys**:
- `cb:bedrock:state` (CLOSED/OPEN/HALF_OPEN)
- `cb:bedrock:failures` (counter)
- `cb:bedrock:last_failure_ts` (timestamp)
- TTL: `recovery_timeout * 2` (auto-cleanup)

**Race condition**: `record_failure` uses atomic `INCR`. Transition to OPEN uses `SETNX` to avoid log duplication across replicas.

### 2. Session Lock (`src/core/session_lock.py`)

```python
class SessionLock:
    def __init__(self, redis_client, ttl_seconds=30, wait_timeout=5):
        self.redis = redis_client
        self.ttl = ttl_seconds
        self.wait_timeout = wait_timeout

    @asynccontextmanager
    async def acquire(self, session_id: str):
        if not self.redis:
            yield  # no-op if Redis unavailable
            return

        key = f"lock:session:{session_id}"
        token = secrets.token_urlsafe(16)  # unique per acquisition
        deadline = time.time() + self.wait_timeout

        while time.time() < deadline:
            try:
                if await self.redis.set(key, token, nx=True, ex=self.ttl):
                    break
            except Exception:
                yield  # Redis down → degrade
                return
            await asyncio.sleep(0.05)
        else:
            raise SessionLockTimeout(f"Could not acquire lock for {session_id}")

        try:
            yield
        finally:
            # Atomic Lua script: delete only if token matches (avoids deleting another's lock)
            await self.redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1, key, token
            )
```

Usage in the supervisor:
```python
async with session_lock.acquire(session_id):
    history = await storage.fetch_chat(...)
    response = await self._process(...)
    await storage.save_chat_message(...)
```

### 3. Rate Limiter / Budget Guard (`src/core/rate_limiter.py`)

```python
class RateLimiter:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def check_user_rate(self, user_id: str, max_per_minute: int = 60) -> tuple[bool, int]:
        """Sliding window via sorted set."""
        key = f"rate:user:{user_id}"
        now = time.time()
        window_start = now - 60

        # Remove old entries + count + add new (atomic via pipeline)
        async with self.redis.pipeline() as p:
            p.zremrangebyscore(key, 0, window_start)
            p.zcard(key)
            p.zadd(key, {str(now): now})
            p.expire(key, 60)
            _, count, _, _ = await p.execute()

        return count < max_per_minute, max_per_minute - count

    async def check_budget(self, estimated_cost_usd: float, daily_budget: float = 50.0) -> tuple[bool, float]:
        key = f"budget:global:{datetime.now(timezone.utc).date()}"
        current = float(await self.redis.get(key) or 0)

        if current + estimated_cost_usd > daily_budget:
            return False, daily_budget - current

        await self.redis.incrbyfloat(key, estimated_cost_usd)
        await self.redis.expire(key, 86400)
        return True, daily_budget - current - estimated_cost_usd
```

Cost estimation **before** the call:
```python
def estimate_cost(input_tokens: int, max_output_tokens: int, model: str = "sonnet") -> float:
    # Pessimistic: assumes max_output_tokens
    pricing = {"sonnet": (3, 15), "haiku": (0.25, 1.25)}  # per 1M tokens
    in_rate, out_rate = pricing.get(model, (3, 15))
    return (input_tokens * in_rate + max_output_tokens * out_rate) / 1_000_000
```

### 4. Bedrock Semaphore (`src/core/bedrock.py`)

```python
class BedrockClient:
    def __init__(self):
        ...
        self._semaphore = asyncio.Semaphore(int(os.getenv("BEDROCK_MAX_CONCURRENT", "10")))

    async def invoke(self, ...):
        queue_start = time.time()
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=30.0)
        except asyncio.TimeoutError:
            raise Exception("Bedrock semaphore acquisition timeout")

        wait_ms = (time.time() - queue_start) * 1000
        bedrock_queue_wait.record(wait_ms)
        bedrock_queue_depth.add(-1)

        try:
            return await asyncio.to_thread(self._invoke_sync, ...)
        finally:
            self._semaphore.release()
```

### 5. Load Testing (k6)

`tests/load/scenario_basic.js`:
```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '2m', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(99)<10000'],
    http_req_failed: ['rate<0.05'],
  },
};

const queries = [
  "list ec2 instances",
  "show pod status",
  "what is my AWS cost?",
  "why did cost go up after the last deploy?",  // cross-domain
];

export default function () {
  const userId = `user-${__VU}`;
  const sessionId = `session-${__VU}-${__ITER}`;
  const query = queries[Math.floor(Math.random() * queries.length)];

  const res = http.post(
    `${__ENV.BASE_URL}/query`,
    JSON.stringify({ user_input: query, user_id: userId, session_id: sessionId }),
    { headers: { 'Content-Type': 'application/json', 'X-Internal-Token': __ENV.TOKEN } }
  );

  check(res, {
    'status 200': (r) => r.status === 200,
    'has response': (r) => r.json('response')?.length > 0,
  });
  sleep(1);
}
```

## Order of integration

1. CircuitBreaker → Redis backend
2. SessionLock created and wrapped in `supervisor.process_request`
3. RateLimiter check before classify
4. BudgetGuard check before invoke (cost estimate)
5. Bedrock semaphore in `BedrockClient.invoke`
6. k6 scripts + workflow

## Rationale

### Decision 1: Redis as source of truth for distributed state

**Trade-off accepted**: +5ms latency per operation. Negligible compared to 5s from Bedrock. Gain: multi-replica safe without sticky sessions.

### Decision 2: Fail-open on all layers (Redis unavailable ≠ system down)

CircuitBreaker, SessionLock, RateLimiter — all degrade to in-memory or no-op if Redis is down. **A Redis outage never brings down the supervisor.**

### Decision 3: Pessimistic cost estimate (max_tokens) BEFORE the call

Blocks the query before burning tokens, but may block queries that would cost less. Acceptable: a false positive on the budget is better than a real overshoot.

### Decision 4: Simple semaphore vs token bucket

Bedrock already has adaptive retry. The semaphore only prevents bursts >> TPS. A token bucket would be over-engineering.

## Invariants

- Redis down NEVER brings down the system (fail-open everywhere)
- Messages from the same session are NEVER processed concurrently (lock)
- No rate limit on `/health` (probes)
- `agents_consulted` in fan-out NEVER includes agents that failed the rate limit (they are skipped, not errors)

## New metrics (update `docs/METRICS.md`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `aigent.rate_limit.blocks` | Counter | `reason` (user/global) | Blocks by rate limit |
| `aigent.bedrock.queue_depth` | Gauge | — | Bedrock calls waiting for semaphore |
| `aigent.bedrock.queue_wait` | Histogram | — | Time waiting for semaphore (ms) |
| `aigent.session_lock.wait` | Histogram | — | Time waiting for session lock (ms) |
| `aigent.session_lock.timeout` | Counter | — | Timeouts acquiring lock |
| `aigent.circuit_breaker.transitions` | Counter | `from`, `to` | State transitions |
