"""Circuit breaker with optional Redis-backed distributed state (spec 25 T1).

When ``redis_client`` is provided, state is persisted in Redis so all replicas
share the same breaker state. Keys:
  - ``cb:{name}:state``          — "closed" | "open" | "half_open"
  - ``cb:{name}:failures``       — int counter (atomic INCR)
  - ``cb:{name}:last_failure_ts`` — float epoch timestamp

Fail-open: if Redis is unavailable, falls back to in-memory state (the original
behaviour). This means during a Redis outage each replica tracks independently —
acceptable because over-tripping is safer than never-tripping.

TTL on all keys = recovery_timeout * 2 (auto-expire stale state).
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from src.core.logger import logger
from src.core.metrics import circuit_breaker_transitions


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker with optional Redis persistence (fail-open to in-memory)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        redis_client: Optional[Any] = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._redis = redis_client
        self._ttl = int(recovery_timeout * 2)

        # In-memory fallback state (used when Redis unavailable)
        self._mem_state = CircuitState.CLOSED
        self._mem_failure_count = 0
        self._mem_last_failure_time = 0.0

    # ── Redis key helpers ──────────────────────────────────────────────────

    @property
    def _key_state(self) -> str:
        return f"cb:{self.name}:state"

    @property
    def _key_failures(self) -> str:
        return f"cb:{self.name}:failures"

    @property
    def _key_last_failure_ts(self) -> str:
        return f"cb:{self.name}:last_failure_ts"

    # ── Redis state access (sync, fail-open) ──────────────────────────────

    def _redis_get_state(self) -> Optional[CircuitState]:
        """Read state from Redis. Returns None on failure (fall back to mem)."""
        if self._redis is None:
            return None
        try:
            val = self._redis.get(self._key_state)
            if val is None:
                return CircuitState.CLOSED
            return CircuitState(val)
        except Exception as exc:
            logger.warning("circuit_breaker redis read failed (fail-open)", extra={"error": str(exc), "name": self.name})
            return None

    def _redis_get_failures(self) -> Optional[int]:
        if self._redis is None:
            return None
        try:
            val = self._redis.get(self._key_failures)
            return int(val) if val else 0
        except Exception:
            return None

    def _redis_get_last_failure_ts(self) -> Optional[float]:
        if self._redis is None:
            return None
        try:
            val = self._redis.get(self._key_last_failure_ts)
            return float(val) if val else 0.0
        except Exception:
            return None

    def _redis_set_state(self, state: CircuitState) -> None:
        if self._redis is None:
            return
        try:
            self._redis.setex(self._key_state, self._ttl, state.value)
        except Exception as exc:
            logger.warning("circuit_breaker redis write failed (fail-open)", extra={"error": str(exc), "name": self.name})

    def _redis_incr_failures(self) -> Optional[int]:
        """Atomic INCR on failure count. Returns new count or None on error."""
        if self._redis is None:
            return None
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(self._key_failures)
            pipe.expire(self._key_failures, self._ttl)
            results = pipe.execute()
            return int(results[0])
        except Exception as exc:
            logger.warning("circuit_breaker redis incr failed (fail-open)", extra={"error": str(exc), "name": self.name})
            return None

    def _redis_set_last_failure_ts(self, ts: float) -> None:
        if self._redis is None:
            return
        try:
            self._redis.setex(self._key_last_failure_ts, self._ttl, str(ts))
        except Exception:
            pass

    def _redis_reset_failures(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.delete(self._key_failures)
        except Exception:
            pass

    # ── Public API (same interface as before) ─────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current state — reads Redis if available, else in-memory."""
        redis_state = self._redis_get_state()
        if redis_state is not None:
            return redis_state
        return self._mem_state

    @state.setter
    def state(self, value: CircuitState) -> None:
        self._mem_state = value
        self._redis_set_state(value)

    @property
    def failure_count(self) -> int:
        redis_count = self._redis_get_failures()
        if redis_count is not None:
            return redis_count
        return self._mem_failure_count

    @failure_count.setter
    def failure_count(self, value: int) -> None:
        self._mem_failure_count = value

    @property
    def last_failure_time(self) -> float:
        redis_ts = self._redis_get_last_failure_ts()
        if redis_ts is not None:
            return redis_ts
        return self._mem_last_failure_time

    @last_failure_time.setter
    def last_failure_time(self, value: float) -> None:
        self._mem_last_failure_time = value
        self._redis_set_last_failure_ts(value)

    def can_execute(self) -> bool:
        current_state = self.state
        if current_state == CircuitState.CLOSED:
            return True
        if current_state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                circuit_breaker_transitions.add(1, {"name": self.name, "from": "open", "to": "half_open"})
                return True
            return False
        # HALF_OPEN: allow one attempt
        return True

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            circuit_breaker_transitions.add(1, {"name": self.name, "from": "half_open", "to": "closed"})
        self._mem_failure_count = 0
        self._redis_reset_failures()
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        now = time.time()
        self.last_failure_time = now

        # Attempt atomic Redis INCR first
        redis_count = self._redis_incr_failures()
        if redis_count is not None:
            new_count = redis_count
        else:
            self._mem_failure_count += 1
            new_count = self._mem_failure_count

        if new_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            circuit_breaker_transitions.add(1, {"name": self.name, "from": "closed", "to": "open"})

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
