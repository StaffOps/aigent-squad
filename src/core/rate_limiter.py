"""Global admission guards — per-user rate limit + daily budget (spec 31 L3).

Lives in ``src/core/`` (not ``src/gateway/``) so spec 25 can reuse the same
implementation regardless of landing order (round-table 2026-06-22). The guards
are **global** (Redis-coordinated across all gateway replicas) — distinct from
the per-replica worker-pool semaphore, which protects pod resources. These
protect *account-wide* resources (Bedrock spend / request rate).

Fail-open (spec 25 invariant): if Redis is unavailable, the guards **allow** the
request. Availability is chosen over a hard cap here because the worst case of a
brief over-spend during a Redis outage is bounded, whereas blocking all traffic
on a cache outage is not acceptable. (This is the opposite trade-off from the
spec-14 guardrail, which is security-critical and fails closed.)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from src.core.logger import logger
from src.core.metrics import rate_limit_blocks

# Pessimistic per-1M-token pricing (USD) for the pre-call budget estimate.
# Pessimistic = assume max output tokens; a false budget block is preferable to
# a real overspend (spec 25 Decision 3). Prices match model_tier.py (Claude 4.x).
_PRICING = {"sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}


def estimate_cost(input_tokens: int, max_output_tokens: int, model: str = "sonnet") -> float:
    """Pessimistic USD estimate for one Bedrock call (assumes max output)."""
    in_rate, out_rate = _PRICING.get(model, _PRICING["sonnet"])
    return (input_tokens * in_rate + max_output_tokens * out_rate) / 1_000_000


# Atomic daily-budget check-and-reserve (spec 31 T19d — closes the GET→compare→
# INCR TOCTOU). Runs server-side in a single step so concurrent callers can't
# both pass near the limit. Redis Lua has no float type → values are strings
# parsed with tonumber; the reserved total is stored back as a string.
#   KEYS[1] = budget key         ARGV[1] = estimated cost
#   ARGV[2] = daily budget cap    ARGV[3] = TTL seconds
#   returns {allowed(1|0), remaining_usd_as_string}
_BUDGET_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if current + cost > cap then
  local rem = cap - current
  if rem < 0 then rem = 0 end
  return {0, tostring(rem)}
end
local new_total = current + cost
redis.call('SET', KEYS[1], tostring(new_total))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {1, tostring(cap - new_total)}
"""


class AdmissionGuard:
    """Per-user rate limit (sliding window) + global daily budget, on Redis.

    All methods fail open: a Redis error returns "allowed" with full remaining,
    never raising. The caller (gateway admission) maps a deny to HTTP 429/503.
    """

    def __init__(self, redis_client=None, rate_per_minute: int = 60, daily_budget_usd: float = 50.0):
        self._redis = redis_client
        self._rate = rate_per_minute
        self._budget = daily_budget_usd

    async def check_rate(self, user_id: str) -> tuple[bool, int]:
        """Per-user sliding-window rate check.

        Returns ``(allowed, remaining)``. Records one request on allow.
        """
        if self._redis is None:
            return True, self._rate
        key = f"rate:user:{user_id}"
        now = time.time()
        window_start = now - 60
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                pipe.zadd(key, {f"{now}": now})
                pipe.expire(key, 60)
                _, count, _, _ = await pipe.execute()
        except Exception as exc:
            logger.warning("rate check redis fallback (allow)", extra={"error": str(exc), "user_id": user_id})
            return True, self._rate
        # count is the size BEFORE adding the current request.
        if count >= self._rate:
            rate_limit_blocks.add(1, {"reason": "user"})
            return False, 0
        return True, self._rate - count - 1

    async def check_budget(self, estimated_cost_usd: float) -> tuple[bool, float]:
        """Global daily budget check — atomic check-and-reserve (spec 31 T19d).

        Returns ``(allowed, remaining_usd)``. Reserves the estimated cost on
        allow. The check-and-reserve runs as a single Lua script server-side, so
        two concurrent requests near the limit cannot both pass (no TOCTOU).

        Fail-open: any Redis error (including EVAL unsupported) → allow.
        """
        if self._redis is None:
            return True, self._budget
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"budget:global:{day}"
        try:
            # Atomic: read current, compare, and only INCR+EXPIRE if it fits.
            # Returns [allowed(1/0), remaining_usd]. Floats are passed/returned
            # as strings because Redis Lua uses integer-only numbers.
            result = await self._redis.eval(
                _BUDGET_RESERVE_LUA,
                1,               # numkeys
                key,             # KEYS[1]
                str(estimated_cost_usd),  # ARGV[1]
                str(self._budget),        # ARGV[2]
                "86400",         # ARGV[3] — TTL seconds
            )
            allowed = int(result[0]) == 1
            remaining = float(result[1])
            if not allowed:
                rate_limit_blocks.add(1, {"reason": "global"})
            return allowed, remaining
        except Exception as exc:
            logger.warning("budget check redis fallback (allow)", extra={"error": str(exc)})
            return True, self._budget
