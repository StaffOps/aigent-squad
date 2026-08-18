"""Tests for src/core/session_lock.py — distributed session lock (spec 25 T2).

Tests against the CONTRACT:
- Acquire succeeds or fails based on Redis SET NX result.
- Release uses Lua atomic compare-and-delete.
- Fail-open: Redis errors result in lock considered acquired (availability).
- Context manager lifecycle.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.core.session_lock import SessionLock, SessionLockConflict


@pytest.fixture
def mock_redis():
    """Async Redis mock with default happy-path behavior."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.eval = AsyncMock(return_value=1)
    return redis


# ─── Acquire ──────────────────────────────────────────────────────────────────


class TestAcquire:
    @pytest.mark.asyncio
    async def test_acquire_success(self, mock_redis):
        """SET NX returns True → lock acquired."""
        lock = SessionLock("sess-1", redis_client=mock_redis, ttl=30)

        result = await lock.acquire()

        assert result is True
        assert lock._acquired is True
        mock_redis.set.assert_awaited_once_with(
            "session_lock:sess-1", lock._token, nx=True, ex=30
        )

    @pytest.mark.asyncio
    async def test_acquire_conflict_set_returns_none(self, mock_redis):
        """SET NX returns None → lock NOT acquired (already held)."""
        mock_redis.set = AsyncMock(return_value=None)
        lock = SessionLock("sess-1", redis_client=mock_redis)

        result = await lock.acquire()

        assert result is False
        assert lock._acquired is False

    @pytest.mark.asyncio
    async def test_acquire_redis_error_fail_open(self, mock_redis):
        """Redis exception on acquire → fail-open (acquired=True)."""
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        lock = SessionLock("sess-1", redis_client=mock_redis)

        result = await lock.acquire()

        assert result is True
        assert lock._acquired is True

    @pytest.mark.asyncio
    async def test_acquire_no_redis_client_always_acquired(self):
        """No Redis client → lock always acquired (fail-open by design)."""
        lock = SessionLock("sess-1", redis_client=None)

        result = await lock.acquire()

        assert result is True
        assert lock._acquired is True

    @pytest.mark.asyncio
    async def test_ttl_passed_to_redis_set(self, mock_redis):
        """TTL is forwarded to Redis SET as 'ex' parameter."""
        lock = SessionLock("sess-1", redis_client=mock_redis, ttl=120)

        await lock.acquire()

        _, kwargs = mock_redis.set.call_args
        assert kwargs["ex"] == 120


# ─── Release ──────────────────────────────────────────────────────────────────


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_calls_lua_with_correct_args(self, mock_redis):
        """Release invokes eval() with Lua script, key, and token."""
        lock = SessionLock("sess-1", redis_client=mock_redis)
        await lock.acquire()

        await lock.release()

        mock_redis.eval.assert_awaited_once()
        call_args = mock_redis.eval.call_args[0]
        # args: lua_script, num_keys, key, token
        assert "GET" in call_args[0]  # Lua script contains GET
        assert "DEL" in call_args[0]  # Lua script contains DEL
        assert call_args[1] == 1  # num_keys
        assert call_args[2] == "session_lock:sess-1"  # KEYS[1]
        assert call_args[3] == lock._token  # ARGV[1]

    @pytest.mark.asyncio
    async def test_release_token_mismatch_no_error(self, mock_redis):
        """Lua returns 0 (token mismatch) → no exception raised."""
        mock_redis.eval = AsyncMock(return_value=0)
        lock = SessionLock("sess-1", redis_client=mock_redis)
        await lock.acquire()

        # Should not raise
        await lock.release()

    @pytest.mark.asyncio
    async def test_release_redis_error_fail_open(self, mock_redis):
        """Redis exception on release → no exception (TTL auto-expire)."""
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))
        lock = SessionLock("sess-1", redis_client=mock_redis)
        await lock.acquire()

        # Should not raise
        await lock.release()

    @pytest.mark.asyncio
    async def test_release_skipped_when_not_acquired(self, mock_redis):
        """Release does nothing if lock was never acquired."""
        lock = SessionLock("sess-1", redis_client=mock_redis)
        # Do NOT acquire — _acquired remains False

        await lock.release()

        mock_redis.eval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_skipped_when_no_redis(self):
        """Release does nothing if redis_client is None."""
        lock = SessionLock("sess-1", redis_client=None)
        await lock.acquire()

        await lock.release()  # no-op, no exception


# ─── Context Manager ──────────────────────────────────────────────────────────


class TestContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_happy_path(self, mock_redis):
        """__aenter__ acquires, __aexit__ releases."""
        lock = SessionLock("sess-1", redis_client=mock_redis)

        async with lock as ctx:
            assert ctx is lock
            assert lock._acquired is True

        mock_redis.eval.assert_awaited_once()  # release called

    @pytest.mark.asyncio
    async def test_context_manager_conflict_raises(self, mock_redis):
        """Lock conflict raises SessionLockConflict when raise_on_conflict=True."""
        mock_redis.set = AsyncMock(return_value=None)
        lock = SessionLock("sess-1", redis_client=mock_redis, raise_on_conflict=True)

        with pytest.raises(SessionLockConflict) as exc_info:
            async with lock:
                pass  # pragma: no cover

        assert exc_info.value.session_id == "sess-1"
        assert "sess-1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_context_manager_conflict_no_raise(self, mock_redis):
        """Lock conflict enters context silently when raise_on_conflict=False."""
        mock_redis.set = AsyncMock(return_value=None)
        lock = SessionLock("sess-1", redis_client=mock_redis, raise_on_conflict=False)

        async with lock as ctx:
            assert ctx is lock
            assert lock._acquired is False

    @pytest.mark.asyncio
    async def test_context_manager_releases_on_exception(self, mock_redis):
        """Lock is released even when body raises an exception."""
        lock = SessionLock("sess-1", redis_client=mock_redis)

        with pytest.raises(ValueError):
            async with lock:
                raise ValueError("oops")

        mock_redis.eval.assert_awaited_once()  # release still called

    @pytest.mark.asyncio
    async def test_unique_token_per_instance(self, mock_redis):
        """Each SessionLock instance has a unique token (UUID-based)."""
        lock_a = SessionLock("sess-1", redis_client=mock_redis)
        lock_b = SessionLock("sess-1", redis_client=mock_redis)

        assert lock_a._token != lock_b._token
