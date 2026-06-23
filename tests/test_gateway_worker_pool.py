"""Tests for src/gateway/worker_pool.py (spec 31, L2).

Tests the WorkerPool CONTRACT: bounded concurrency, backpressure (PoolFullError),
first-byte and idle-stream timeouts, cancellation via Redis key, job lifecycle
fail-open when Redis is unavailable.

Uses fakeredis for Redis simulation and tiny timeout values to keep tests fast.
"""
import asyncio
from unittest.mock import patch

import fakeredis.aioredis
import pytest

from src.gateway.worker_pool import (
    FirstByteTimeout,
    IdleStreamTimeout,
    PoolFullError,
    WorkerPool,
)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def pool(redis):
    return WorkerPool(
        max_concurrent=2,
        job_timeout=5.0,
        first_byte_timeout=0.05,
        idle_timeout=0.05,
        cancel_poll_interval=0.02,
        redis_client=redis,
    )


@pytest.fixture
def pool_no_redis():
    return WorkerPool(
        max_concurrent=2,
        job_timeout=5.0,
        first_byte_timeout=0.05,
        idle_timeout=0.05,
        cancel_poll_interval=0.02,
        redis_client=None,
    )


# ─── Capacity math ──────────────────────────────────────────────────


class TestCapacity:
    def test_initial_capacity(self, pool):
        assert pool.has_capacity() is True
        assert pool.available == 2
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_available_decreases_while_running(self, pool):
        """While a job is active, available count decreases."""
        hold = asyncio.Event()

        async def slow_gen():
            yield "chunk"
            await hold.wait()

        gen = await pool.submit("j1", slow_gen())
        # Start consuming — the pool slot is taken after first __anext__
        task = asyncio.create_task(self._drain(gen))
        await asyncio.sleep(0.01)
        assert pool.available == 1
        assert pool.active_count == 1
        hold.set()
        await task

    @staticmethod
    async def _drain(gen):
        async for _ in gen:
            pass


# ─── PoolFullError (backpressure) ────────────────────────────────────


class TestPoolFull:
    @pytest.mark.asyncio
    async def test_pool_full_raises_immediately(self, pool):
        """When at capacity, submit raises PoolFullError (no queue)."""
        holds = []
        for i in range(2):
            ev = asyncio.Event()
            holds.append(ev)

            async def gen(e=ev):
                yield "x"
                await e.wait()

            g = await pool.submit(f"j{i}", gen())
            asyncio.create_task(self._drain(g))

        await asyncio.sleep(0.01)

        async def dummy():
            yield "y"

        with pytest.raises(PoolFullError):
            await pool.submit("j_overflow", dummy())

        # has_capacity should be False
        assert pool.has_capacity() is False
        for ev in holds:
            ev.set()
        await asyncio.sleep(0.02)

    @staticmethod
    async def _drain(gen):
        async for _ in gen:
            pass


# ─── Timeouts ────────────────────────────────────────────────────────


class TestTimeouts:
    @pytest.mark.asyncio
    async def test_first_byte_timeout_when_no_chunk(self, pool):
        """If stream never yields a first chunk, FirstByteTimeout fires."""

        async def never_yields():
            await asyncio.sleep(10)
            yield "never"  # pragma: no cover

        gen = await pool.submit("j_fb", never_yields())
        with pytest.raises(FirstByteTimeout):
            async for _ in gen:
                pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_idle_stream_timeout_after_first_chunk(self, pool):
        """If stream stalls AFTER first chunk, IdleStreamTimeout fires."""

        async def stalls_after_first():
            yield "first"
            await asyncio.sleep(10)
            yield "never"  # pragma: no cover

        gen = await pool.submit("j_idle", stalls_after_first())
        chunks = []
        with pytest.raises(IdleStreamTimeout):
            async for c in gen:
                chunks.append(c)

        assert chunks == ["first"]

    @pytest.mark.asyncio
    async def test_normal_stream_completes(self, pool):
        """A healthy stream completing within timeouts works fine."""

        async def healthy():
            yield "a"
            yield "b"

        gen = await pool.submit("j_ok", healthy())
        result = [c async for c in gen]
        assert result == ["a", "b"]


# ─── Cancellation via Redis key ──────────────────────────────────────


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_active_job(self, pool, redis):
        """Setting cancel flag in Redis cancels the running task."""
        hold = asyncio.Event()

        async def slow():
            yield "start"
            await hold.wait()
            yield "end"  # pragma: no cover

        gen = await pool.submit("j_cancel", slow())
        chunks = []

        async def consume():
            async for c in gen:
                chunks.append(c)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        # Request cancellation
        cancelled = await pool.cancel("j_cancel")
        assert cancelled is True
        # Wait for the poll to detect it
        await asyncio.sleep(0.1)
        assert task.done()
        hold.set()

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_returns_false(self, pool):
        result = await pool.cancel("nonexistent")
        assert result is False


# ─── Job lifecycle / Redis status (fail-open) ────────────────────────


class TestJobLifecycle:
    @pytest.mark.asyncio
    async def test_completed_status_written_to_redis(self, pool, redis):
        """On normal completion, job status is set in Redis."""

        async def quick():
            yield "done"

        gen = await pool.submit("j_status", quick())
        async for _ in gen:
            pass

        import json
        raw = await redis.get("job:j_status")
        assert raw is not None
        record = json.loads(raw)
        assert record["status"] == "completed"

    @pytest.mark.asyncio
    async def test_fail_open_when_redis_none(self, pool_no_redis):
        """Pool works fine without Redis — lifecycle degrades to log-only."""

        async def quick():
            yield "ok"

        gen = await pool_no_redis.submit("j_nored", quick())
        result = [c async for c in gen]
        assert result == ["ok"]

    @pytest.mark.asyncio
    async def test_fail_open_when_redis_raises(self, pool):
        """Redis errors during _update_status don't crash the job."""

        async def quick():
            yield "ok"

        with patch.object(pool._redis, "set", side_effect=Exception("redis down")):
            gen = await pool.submit("j_rdown", quick())
            result = [c async for c in gen]
            assert result == ["ok"]

    @pytest.mark.asyncio
    async def test_timeout_status_written(self, pool, redis):
        """On first-byte timeout, status reflects that."""

        async def never():
            await asyncio.sleep(10)
            yield "x"  # pragma: no cover

        gen = await pool.submit("j_fbt", never())
        with pytest.raises(FirstByteTimeout):
            async for _ in gen:
                pass  # pragma: no cover

        import json
        raw = await redis.get("job:j_fbt")
        record = json.loads(raw)
        assert record["status"] == "first_byte_timeout"


# ─── Pool depth (add/remove) ────────────────────────────────────────


class TestPoolDepth:
    @pytest.mark.asyncio
    async def test_pool_depth_returns_to_zero(self, pool):
        """After job completes, pool releases the slot."""

        async def quick():
            yield "x"

        gen = await pool.submit("j_depth", quick())
        async for _ in gen:
            pass

        assert pool.active_count == 0
        assert pool.available == 2
