"""Tests for src/core/rate_limiter.py (spec 31 L3 — global admission guards).

Tests the CONTRACT: estimate_cost pricing, AdmissionGuard.check_rate (sliding
window), and AdmissionGuard.check_budget (daily cap). All fail-open on Redis
errors. Uses fakeredis for the happy path; AsyncMock for error simulation.

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import fakeredis.aioredis

from src.core.rate_limiter import estimate_cost, AdmissionGuard


# ─── estimate_cost ───────────────────────────────────────────────────


class TestEstimateCost:
    """Pessimistic pricing: (input_tokens * in_rate + max_output * out_rate) / 1M."""

    def test_sonnet_pricing(self):
        # sonnet: in=$3/M, out=$15/M
        cost = estimate_cost(input_tokens=1000, max_output_tokens=4096, model="sonnet")
        expected = (1000 * 3.0 + 4096 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_haiku_pricing(self):
        # haiku: in=$0.25/M, out=$1.25/M
        cost = estimate_cost(input_tokens=2000, max_output_tokens=2000, model="haiku")
        expected = (2000 * 0.25 + 2000 * 1.25) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_unknown_model_defaults_to_sonnet(self):
        cost_unknown = estimate_cost(input_tokens=500, max_output_tokens=1000, model="gpt-4o")
        cost_sonnet = estimate_cost(input_tokens=500, max_output_tokens=1000, model="sonnet")
        assert cost_unknown == cost_sonnet

    def test_zero_tokens(self):
        assert estimate_cost(0, 0, "sonnet") == 0.0


# ─── AdmissionGuard.check_rate ───────────────────────────────────────


class TestCheckRate:
    @pytest.mark.asyncio
    async def test_redis_none_fail_open(self):
        guard = AdmissionGuard(redis_client=None, rate_per_minute=60)
        allowed, remaining = await guard.check_rate("user1")
        assert allowed is True
        assert remaining == 60

    @pytest.mark.asyncio
    async def test_allows_under_limit(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, rate_per_minute=10)
        allowed, remaining = await guard.check_rate("user1")
        assert allowed is True
        assert remaining == 9  # 10 - 0 - 1

    @pytest.mark.asyncio
    async def test_decrements_remaining(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, rate_per_minute=5)
        for i in range(4):
            allowed, remaining = await guard.check_rate("user1")
            assert allowed is True
            assert remaining == 5 - i - 1

    @pytest.mark.asyncio
    async def test_blocks_at_limit(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, rate_per_minute=3)
        # Fill up the window
        for _ in range(3):
            await guard.check_rate("user1")
        # 4th request should be blocked
        allowed, remaining = await guard.check_rate("user1")
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_blocks_records_metric(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, rate_per_minute=1)
        await guard.check_rate("user1")  # fills the 1 allowed
        with patch("src.core.rate_limiter.rate_limit_blocks") as mock_metric:
            await guard.check_rate("user1")
            mock_metric.add.assert_called_once_with(1, {"reason": "user"})

    @pytest.mark.asyncio
    async def test_redis_error_fail_open(self):
        mock_redis = AsyncMock()
        # pipeline() returns an async context manager whose execute raises
        mock_pipe = AsyncMock()
        mock_pipe.zremrangebyscore = MagicMock()
        mock_pipe.zcard = MagicMock()
        mock_pipe.zadd = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=ConnectionError("redis down"))
        mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipe.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        guard = AdmissionGuard(redis_client=mock_redis, rate_per_minute=60)
        allowed, remaining = await guard.check_rate("user1")
        assert allowed is True
        assert remaining == 60


# ─── AdmissionGuard.check_budget ─────────────────────────────────────


class TestCheckBudget:
    @pytest.mark.asyncio
    async def test_redis_none_fail_open(self):
        guard = AdmissionGuard(redis_client=None, daily_budget_usd=50.0)
        allowed, remaining = await guard.check_budget(1.0)
        assert allowed is True
        assert remaining == 50.0

    @pytest.mark.asyncio
    async def test_under_budget_allows(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, daily_budget_usd=10.0)
        allowed, remaining = await guard.check_budget(3.0)
        assert allowed is True
        assert remaining == pytest.approx(7.0)

    @pytest.mark.asyncio
    async def test_increments_daily_counter(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, daily_budget_usd=10.0)
        await guard.check_budget(2.0)
        await guard.check_budget(3.0)
        # Total spent should be 5.0
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"budget:global:{day}"
        val = float(await redis_client.get(key))
        assert val == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_over_budget_denies(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, daily_budget_usd=5.0)
        await guard.check_budget(4.0)  # 4 spent, 1 remaining
        allowed, remaining = await guard.check_budget(2.0)  # 4+2=6 > 5
        assert allowed is False
        assert remaining == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_over_budget_records_metric(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, daily_budget_usd=1.0)
        with patch("src.core.rate_limiter.rate_limit_blocks") as mock_metric:
            await guard.check_budget(2.0)
            mock_metric.add.assert_called_once_with(1, {"reason": "global"})

    @pytest.mark.asyncio
    async def test_daily_key_format(self):
        redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        guard = AdmissionGuard(redis_client=redis_client, daily_budget_usd=100.0)
        await guard.check_budget(1.0)
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"budget:global:{day}"
        assert await redis_client.exists(key)

    @pytest.mark.asyncio
    async def test_redis_error_fail_open(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=ConnectionError("redis down"))

        guard = AdmissionGuard(redis_client=mock_redis, daily_budget_usd=50.0)
        allowed, remaining = await guard.check_budget(5.0)
        assert allowed is True
        assert remaining == 50.0
