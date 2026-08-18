"""Distributed session lock using Redis SETNX + TTL + Lua release (spec 25 T2).

Prevents duplicate concurrent processing of the same session_id across replicas.
Uses async Redis (``redis.asyncio``). Fail-open: if Redis is unavailable, the
lock is considered acquired (availability over strict dedup).

Usage::

    async with SessionLock(session_id, redis_client, ttl=60):
        # Only one replica processes this session concurrently
        await process(session_id)

The Lua release script ensures only the holder releases the lock (compare value
before DEL), preventing a slow holder from accidentally releasing a lock that
another replica acquired after TTL expiry.
"""
from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any, Optional, Type

from src.core.logger import logger

# Lua script: atomically release the lock ONLY if we still hold it.
# Avoids the race where: holder A's lock expires → B acquires → A finishes
# and blindly DELetes B's lock.
#   KEYS[1] = lock key    ARGV[1] = expected value (our unique token)
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class SessionLockConflict(Exception):
    """Raised when the lock is already held by another worker."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session lock conflict: {session_id}")


class SessionLock:
    """Async context manager for distributed session locking.

    Args:
        session_id: Unique session identifier to lock on.
        redis_client: An ``redis.asyncio.Redis`` instance (or None for fail-open).
        ttl: Lock TTL in seconds (auto-release safety net).
        raise_on_conflict: If True, raises SessionLockConflict instead of
            returning silently (caller decides whether to 409 the request).
    """

    def __init__(
        self,
        session_id: str,
        redis_client: Optional[Any] = None,
        ttl: int = 60,
        raise_on_conflict: bool = True,
    ) -> None:
        self._session_id = session_id
        self._redis = redis_client
        self._ttl = ttl
        self._raise = raise_on_conflict
        self._token: str = uuid.uuid4().hex
        self._acquired: bool = False

    @property
    def _key(self) -> str:
        return f"session_lock:{self._session_id}"

    async def acquire(self) -> bool:
        """Attempt to acquire the lock. Returns True if acquired.

        Fail-open: Redis errors → returns True (lock considered acquired).
        """
        if self._redis is None:
            self._acquired = True
            return True
        try:
            # SET key value NX EX ttl — atomic acquire
            result = await self._redis.set(self._key, self._token, nx=True, ex=self._ttl)
            self._acquired = result is not None and bool(result)
            return self._acquired
        except Exception as exc:
            logger.warning(
                "session_lock acquire failed (fail-open)",
                extra={"error": str(exc), "session_id": self._session_id},
            )
            self._acquired = True  # fail-open
            return True

    async def release(self) -> None:
        """Release the lock if we hold it. Fail-open on error."""
        if self._redis is None or not self._acquired:
            return
        try:
            await self._redis.eval(_RELEASE_LUA, 1, self._key, self._token)
        except Exception as exc:
            logger.warning(
                "session_lock release failed (TTL will auto-expire)",
                extra={"error": str(exc), "session_id": self._session_id},
            )

    async def __aenter__(self) -> "SessionLock":
        acquired = await self.acquire()
        if not acquired and self._raise:
            raise SessionLockConflict(self._session_id)
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.release()
