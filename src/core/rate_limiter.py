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
# a real overspend (spec 25 Decision 3).
_PRICING = {"sonnet": (3.0, 15.0), "haiku": (0.25, 1.25)}


def estimate_cost(input_tokens: int, max_output_tokens: int, model: str = "sonnet") -> float:
    """Pessimistic USD estimate for one Bedrock call (assumes max output)."""
    in_rate, out_rate = _PRICING.get(model, _PRICING["sonnet"])
    return (input_tokens * in_rate + max_output_tokens * out_rate) / 1_000_000


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
        """Global daily budget check.

        Returns ``(allowed, remaining_usd)``. Reserves the estimated cost on
        allow (incrementing the daily counter).

        NOTE (tech debt, tracked for L4/L5 hardening): the GET→compare→INCR is
        not atomic, so two concurrent requests near the limit can both pass and
        slightly overspend. Accepted for now (spec 31 explicitly tolerates brief
        over-spend; the pessimistic estimate buffers it; budget is a soft cap).
        Close with an EVAL/Lua atomic check-and-reserve when hardening.
        """
        if self._redis is None:
            return True, self._budget
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"budget:global:{day}"
        try:
            current = float(await self._redis.get(key) or 0.0)
            if current + estimated_cost_usd > self._budget:
                rate_limit_blocks.add(1, {"reason": "global"})
                return False, max(0.0, self._budget - current)
            await self._redis.incrbyfloat(key, estimated_cost_usd)
            await self._redis.expire(key, 86400)
            return True, self._budget - current - estimated_cost_usd
        except Exception as exc:
            logger.warning("budget check redis fallback (allow)", extra={"error": str(exc)})
            return True, self._budget
