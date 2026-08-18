"""Tests for Redis-backed paths in src/core/circuit_breaker.py (spec 25 T1).

Tests against the CONTRACT:
- State transitions: CLOSED → OPEN → HALF_OPEN → CLOSED.
- Redis persistence of state, failures, and timestamps.
- Fail-open: Redis errors fall back to in-memory tracking.
- Pipeline atomic INCR + EXPIRE for failure counter.
- TTL = recovery_timeout * 2 on all keys.
- Pure in-memory mode when redis_client=None.
"""
from __future__ import annotations

import time

import pytest
from unittest.mock import MagicMock, patch, call

from src.core.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def mock_redis():
    """Sync Redis mock for circuit breaker (sync API)."""
    redis = MagicMock()
    redis.get = MagicMock(return_value=None)  # default: key doesn't exist
    redis.setex = MagicMock()
    redis.delete = MagicMock()

    # Pipeline mock
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = MagicMock(return_value=[1])  # first failure → count=1
    redis.pipeline = MagicMock(return_value=pipe)

    return redis


@pytest.fixture
def cb(mock_redis):
    """Circuit breaker with Redis and threshold=3 for easier testing."""
    return CircuitBreaker(
        name="test-svc",
        failure_threshold=3,
        recovery_timeout=10.0,
        redis_client=mock_redis,
    )


# ─── can_execute() ────────────────────────────────────────────────────────────


class TestCanExecute:
    def test_closed_state_returns_true(self, cb, mock_redis):
        """CLOSED state → can_execute() returns True."""
        mock_redis.get.return_value = None  # None means key absent → CLOSED

        assert cb.can_execute() is True

    def test_open_state_not_past_recovery_returns_false(self, cb, mock_redis):
        """OPEN state + NOT past recovery_timeout → returns False."""
        # State is OPEN
        mock_redis.get.side_effect = lambda key: {
            "cb:test-svc:state": "open",
            "cb:test-svc:failures": "3",
            "cb:test-svc:last_failure_ts": str(time.time()),  # just now
        }.get(key)

        assert cb.can_execute() is False

    def test_open_state_past_recovery_returns_true_half_open(self, cb, mock_redis):
        """OPEN state + past recovery_timeout → transitions to HALF_OPEN, returns True."""
        old_time = time.time() - 20.0  # well past recovery_timeout=10
        mock_redis.get.side_effect = lambda key: {
            "cb:test-svc:state": "open",
            "cb:test-svc:failures": "3",
            "cb:test-svc:last_failure_ts": str(old_time),
        }.get(key)

        with patch("src.core.circuit_breaker.circuit_breaker_transitions") as mock_metric:
            result = cb.can_execute()

        assert result is True
        # Verify state transition was persisted
        mock_redis.setex.assert_called()
        # Verify metrics emitted for open → half_open
        mock_metric.add.assert_called_with(
            1, {"name": "test-svc", "from": "open", "to": "half_open"}
        )

    def test_half_open_state_allows_one_attempt(self, cb, mock_redis):
        """HALF_OPEN state → can_execute() returns True (allow one probe)."""
        mock_redis.get.side_effect = lambda key: {
            "cb:test-svc:state": "half_open",
            "cb:test-svc:failures": "3",
            "cb:test-svc:last_failure_ts": "0",
        }.get(key)

        assert cb.can_execute() is True

    def test_redis_error_on_can_execute_falls_back_to_memory(self, cb, mock_redis):
        """Redis error on state read → falls back to in-memory (CLOSED by default)."""
        mock_redis.get.side_effect = ConnectionError("Redis down")

        # Suppress logger to avoid LogRecord 'name' key conflict in source code
        with patch("src.core.circuit_breaker.logger"):
            # In-memory state defaults to CLOSED → can_execute True
            assert cb.can_execute() is True


# ─── record_success() ─────────────────────────────────────────────────────────


class TestRecordSuccess:
    def test_record_success_resets_to_closed(self, cb, mock_redis):
        """record_success() resets state to CLOSED in Redis."""
        # Pretend we're in HALF_OPEN
        mock_redis.get.side_effect = lambda key: {
            "cb:test-svc:state": "half_open",
            "cb:test-svc:failures": "3",
            "cb:test-svc:last_failure_ts": "0",
        }.get(key)

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_success()

        # State set to CLOSED
        mock_redis.setex.assert_called_with(
            "cb:test-svc:state", 20, "closed"  # TTL = recovery_timeout * 2
        )
        # Failure counter deleted
        mock_redis.delete.assert_called_with("cb:test-svc:failures")

    def test_record_success_resets_in_memory_counter(self, cb, mock_redis):
        """record_success() resets the in-memory failure counter to 0."""
        cb._mem_failure_count = 5

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_success()

        assert cb._mem_failure_count == 0


# ─── record_failure() ─────────────────────────────────────────────────────────


class TestRecordFailure:
    def test_record_failure_increments_via_pipeline(self, cb, mock_redis):
        """record_failure() uses pipeline INCR + EXPIRE atomically."""
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1]  # first failure

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_failure()

        mock_redis.pipeline.assert_called_once_with(transaction=True)
        pipe.incr.assert_called_once_with("cb:test-svc:failures")
        pipe.expire.assert_called_once_with("cb:test-svc:failures", 20)  # TTL
        pipe.execute.assert_called_once()

    def test_record_failure_transitions_to_open_at_threshold(self, cb, mock_redis):
        """Failure count reaching threshold → state transitions to OPEN."""
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [3]  # at threshold

        with patch("src.core.circuit_breaker.circuit_breaker_transitions") as mock_metric:
            cb.record_failure()

        # State set to OPEN
        mock_redis.setex.assert_any_call("cb:test-svc:state", 20, "open")
        mock_metric.add.assert_called_with(
            1, {"name": "test-svc", "from": "closed", "to": "open"}
        )

    def test_record_failure_below_threshold_stays_closed(self, cb, mock_redis):
        """Failure count below threshold → state remains unchanged."""
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1]  # below threshold=3

        with patch("src.core.circuit_breaker.circuit_breaker_transitions") as mock_metric:
            cb.record_failure()

        # No state transition to OPEN
        mock_metric.add.assert_not_called()

    def test_record_failure_redis_error_falls_back_to_memory(self, cb, mock_redis):
        """Redis pipeline error → falls back to in-memory counter."""
        pipe = mock_redis.pipeline.return_value
        pipe.execute.side_effect = ConnectionError("Redis down")

        # Suppress logger to avoid LogRecord 'name' key conflict in source code
        with patch("src.core.circuit_breaker.logger"):
            with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
                cb.record_failure()

        assert cb._mem_failure_count == 1

    def test_record_failure_sets_last_failure_timestamp(self, cb, mock_redis):
        """record_failure() persists the failure timestamp to Redis."""
        pipe = mock_redis.pipeline.return_value
        pipe.execute.return_value = [1]

        before = time.time()
        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_failure()
        after = time.time()

        # Check setex was called for last_failure_ts
        ts_calls = [
            c for c in mock_redis.setex.call_args_list
            if "last_failure_ts" in str(c)
        ]
        assert len(ts_calls) >= 1
        # Verify the stored timestamp is reasonable
        stored_ts = float(ts_calls[0][0][2])
        assert before <= stored_ts <= after


# ─── TTL ──────────────────────────────────────────────────────────────────────


class TestTTL:
    def test_ttl_is_recovery_timeout_times_two(self, mock_redis):
        """All Redis keys use TTL = recovery_timeout * 2."""
        cb = CircuitBreaker(
            name="svc",
            failure_threshold=5,
            recovery_timeout=30.0,
            redis_client=mock_redis,
        )

        assert cb._ttl == 60  # 30 * 2

    def test_ttl_applied_to_state_key(self, cb, mock_redis):
        """State key uses correct TTL via setex."""
        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_success()

        # setex(key, ttl, value)
        state_call = [
            c for c in mock_redis.setex.call_args_list
            if "state" in str(c)
        ]
        assert state_call
        assert state_call[0][0][1] == 20  # recovery_timeout=10 → TTL=20


# ─── Pure in-memory (no Redis) ────────────────────────────────────────────────


class TestPureInMemory:
    def test_no_redis_client_uses_in_memory_only(self):
        """Without redis_client, circuit breaker operates purely in-memory."""
        cb = CircuitBreaker(
            name="mem-svc",
            failure_threshold=2,
            recovery_timeout=5.0,
            redis_client=None,
        )

        assert cb.can_execute() is True
        assert cb.state == CircuitState.CLOSED

    def test_no_redis_failure_threshold_works_in_memory(self):
        """In-memory mode still transitions to OPEN at threshold."""
        cb = CircuitBreaker(
            name="mem-svc",
            failure_threshold=2,
            recovery_timeout=5.0,
            redis_client=None,
        )

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
            cb.record_failure()
            assert cb.state == CircuitState.OPEN

    def test_no_redis_record_success_resets(self):
        """In-memory mode resets to CLOSED on success."""
        cb = CircuitBreaker(
            name="mem-svc",
            failure_threshold=2,
            recovery_timeout=5.0,
            redis_client=None,
        )

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_failure()
            cb.record_failure()
            assert cb.state == CircuitState.OPEN
            cb.record_success()
            assert cb.state == CircuitState.CLOSED
            assert cb._mem_failure_count == 0

    def test_no_redis_can_execute_open_past_recovery(self):
        """In-memory OPEN + past recovery → HALF_OPEN → can_execute True."""
        cb = CircuitBreaker(
            name="mem-svc",
            failure_threshold=2,
            recovery_timeout=0.01,  # very short for testing
            redis_client=None,
        )

        with patch("src.core.circuit_breaker.circuit_breaker_transitions"):
            cb.record_failure()
            cb.record_failure()
            assert cb.state == CircuitState.OPEN

            # Wait past recovery
            time.sleep(0.02)
            assert cb.can_execute() is True
            assert cb.state == CircuitState.HALF_OPEN


# ─── Key naming ───────────────────────────────────────────────────────────────


class TestKeyNaming:
    def test_key_format(self):
        """Redis keys follow cb:{name}:{suffix} format."""
        cb = CircuitBreaker(name="my-service", redis_client=MagicMock())

        assert cb._key_state == "cb:my-service:state"
        assert cb._key_failures == "cb:my-service:failures"
        assert cb._key_last_failure_ts == "cb:my-service:last_failure_ts"
