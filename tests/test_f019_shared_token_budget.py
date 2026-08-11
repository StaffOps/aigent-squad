"""F-019: Shared token budget — independent verification.

Tests the CONTRACT of the fix, not its implementation. The fix claims:
  1. Two replicas sharing one Redis enforce ONE budget per session_id.
  2. Atomic INCRBY prevents lost updates under concurrency.
  3. Fail-closed on Redis unavailability (opposite of rate_limiter's fail-open).
  4. TTL is set on the Redis key (24h).
  5. A refusal metric (aigent.token_budget.exceeded) is emitted on block.
  6. Existing behaviour preserved: TokenBudgetExceeded fields, 4 call sites work.

Uses fakeredis (sync) — already a test dependency. All tests are independent of
ordering (pytest-randomly safe) and do not touch module-level singletons.
"""
import threading
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from src.core.token_budget import (
    SessionBudgetTracker,
    TokenBudgetExceeded,
    _SESSION_BUDGET_TTL_SECONDS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def shared_redis():
    """A single fakeredis server instance shared across multiple clients.

    This simulates the multi-replica scenario: multiple SessionBudgetTracker
    instances connecting to the SAME Redis server (same data), each acting as
    a separate gateway replica.
    """
    server = fakeredis.FakeServer()
    return server


@pytest.fixture
def make_tracker(shared_redis):
    """Factory: create a tracker backed by the shared fakeredis server.

    Each call simulates a NEW gateway replica connecting to the same Redis.
    """
    def _make(max_tokens: int = 10000) -> SessionBudgetTracker:
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        return SessionBudgetTracker(max_tokens_per_session=max_tokens, redis_client=client)
    return _make


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Multi-replica: two trackers, one budget
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiReplicaSharedBudget:
    """Two independent tracker instances (simulating two replicas) sharing one
    Redis server must enforce a SINGLE budget per session_id."""

    def test_second_replica_sees_first_replicas_spend(self, make_tracker):
        """THE acceptance criterion for F-019: replica B must see replica A's
        usage. With an in-memory-only implementation, this test FAILS because
        each instance has its own private dict."""
        tracker_a = make_tracker(max_tokens=1000)
        tracker_b = make_tracker(max_tokens=1000)

        # Replica A records 600 tokens for session "shared-sess"
        tracker_a.record_usage("shared-sess", input_tokens=300, output_tokens=300)

        # Replica B must see those 600 tokens
        assert tracker_b.get_usage("shared-sess") == 600
        assert tracker_b.get_remaining("shared-sess") == 400

    def test_combined_spend_triggers_budget_exceeded(self, make_tracker):
        """If A spends 600 and B spends 500, total is 1100 > 1000 budget.
        The NEXT check on either replica must refuse."""
        tracker_a = make_tracker(max_tokens=1000)
        tracker_b = make_tracker(max_tokens=1000)

        tracker_a.record_usage("sess-x", input_tokens=300, output_tokens=300)  # 600
        tracker_b.record_usage("sess-x", input_tokens=250, output_tokens=250)  # 500

        # Either replica's check_budget must raise
        with pytest.raises(TokenBudgetExceeded) as exc_info:
            tracker_a.check_budget("sess-x")
        assert exc_info.value.used == 1100
        assert exc_info.value.limit == 1000

        with pytest.raises(TokenBudgetExceeded):
            tracker_b.check_budget("sess-x")

    def test_in_memory_only_would_fail_this(self):
        """Proves the pre-fix (in-memory-only) implementation is broken for
        multi-replica: two trackers WITHOUT shared Redis each have their own
        independent budget. This is the REVERT proof."""
        # No shared Redis — each tracker is purely in-memory (redis_client=None)
        tracker_a = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=None)
        tracker_b = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=None)

        tracker_a.record_usage("sess-broken", input_tokens=400, output_tokens=400)  # 800

        # With in-memory, tracker_b sees ZERO — it has its own private dict.
        # This is the bug F-019 fixes.
        assert tracker_b.get_usage("sess-broken") == 0  # BUG: should be 800
        # Tracker B still has full remaining — independent budget!
        assert tracker_b.get_remaining("sess-broken") == 1000  # BUG: should be 200

        # Tracker B allows another 800 without raising — 2x the intended budget!
        tracker_b.record_usage("sess-broken", input_tokens=400, output_tokens=400)
        tracker_b.check_budget("sess-broken")  # Does NOT raise — bug demonstrated

    def test_reset_on_one_replica_is_visible_on_other(self, make_tracker):
        """Reset propagates through the shared store."""
        tracker_a = make_tracker(max_tokens=5000)
        tracker_b = make_tracker(max_tokens=5000)

        tracker_a.record_usage("sess-r", input_tokens=1000, output_tokens=1000)
        assert tracker_b.get_usage("sess-r") == 2000

        tracker_b.reset("sess-r")
        assert tracker_a.get_usage("sess-r") == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Atomicity under concurrent access
# ═══════════════════════════════════════════════════════════════════════════════


class TestAtomicIncrements:
    """Concurrent INCRBY from multiple threads/instances must not lose updates."""

    def test_concurrent_increments_no_lost_updates(self, make_tracker):
        """50 threads × 100 tokens each = 5000. No lost updates allowed."""
        tracker = make_tracker(max_tokens=10**9)
        threads = [
            threading.Thread(
                target=tracker.record_usage,
                args=("sess-atomic", 50, 50),
            )
            for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert tracker.get_usage("sess-atomic") == 5000

    def test_concurrent_increments_from_two_replicas(self, make_tracker):
        """Two separate tracker instances (replicas) incrementing concurrently.
        25 threads on each tracker × 100 tokens = 5000 total."""
        tracker_a = make_tracker(max_tokens=10**9)
        tracker_b = make_tracker(max_tokens=10**9)

        threads_a = [
            threading.Thread(
                target=tracker_a.record_usage,
                args=("sess-dual", 50, 50),
            )
            for _ in range(25)
        ]
        threads_b = [
            threading.Thread(
                target=tracker_b.record_usage,
                args=("sess-dual", 50, 50),
            )
            for _ in range(25)
        ]
        all_threads = threads_a + threads_b
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join()

        # Both replicas agree on the total
        assert tracker_a.get_usage("sess-dual") == 5000
        assert tracker_b.get_usage("sess-dual") == 5000


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Fail-closed on Redis unavailability (divergence from rate_limiter)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosed:
    """Token budget MUST refuse when Redis is unavailable (fail closed).
    This is the OPPOSITE of the rate limiter which allows (fail open)."""

    def test_check_budget_refuses_on_redis_error(self):
        """Redis GET raises → check_budget must raise TokenBudgetExceeded."""
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=ConnectionError("redis down"))

        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=mock_redis)

        with pytest.raises(TokenBudgetExceeded) as exc_info:
            tracker.check_budget("sess-fail")

        # The exception must indicate failure (used=max, meaning "can't verify headroom")
        assert exc_info.value.used == 10000
        assert exc_info.value.limit == 10000

    def test_get_usage_returns_max_on_redis_error(self):
        """get_usage fails closed: returns max_tokens (treated as over-budget)."""
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=ConnectionError("redis down"))

        tracker = SessionBudgetTracker(max_tokens_per_session=5000, redis_client=mock_redis)
        assert tracker.get_usage("sess-fail") == 5000

    def test_get_remaining_returns_zero_on_redis_error(self):
        """Remaining must be 0 when Redis is down (fail closed)."""
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=ConnectionError("redis down"))

        tracker = SessionBudgetTracker(max_tokens_per_session=5000, redis_client=mock_redis)
        assert tracker.get_remaining("sess-fail") == 0

    def test_divergence_from_rate_limiter_fail_open(self):
        """Pin the DIVERGENCE between token_budget (fail-closed) and
        rate_limiter (fail-open) via a single test.

        Rate limiter: Redis error → allow request (returns True).
        Token budget: Redis error → refuse request (raises TokenBudgetExceeded).

        This test proves the two modules make OPPOSITE choices on Redis failure,
        ensuring the divergence is never accidentally unified."""
        from src.core.rate_limiter import AdmissionGuard

        # Rate limiter: mock Redis that fails → should ALLOW (fail open)
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock()
        mock_pipe.zcard = MagicMock()
        mock_pipe.zadd = MagicMock()
        mock_pipe.expire = MagicMock()
        # The rate limiter uses async Redis; we can test fail-open by passing None
        rate_guard = AdmissionGuard(redis_client=None, rate_per_minute=60)
        # With redis_client=None, rate limiter returns (True, rate) — fail open
        import asyncio
        allowed, _ = asyncio.get_event_loop().run_until_complete(
            rate_guard.check_rate("user-x")
        )
        assert allowed is True, "Rate limiter must ALLOW when Redis unavailable (fail open)"

        # Token budget: mock Redis that fails → should REFUSE (fail closed)
        mock_redis_sync = MagicMock()
        mock_redis_sync.get = MagicMock(side_effect=ConnectionError("down"))
        budget_tracker_local = SessionBudgetTracker(
            max_tokens_per_session=10000, redis_client=mock_redis_sync
        )
        with pytest.raises(TokenBudgetExceeded):
            budget_tracker_local.check_budget("sess-x")

    def test_record_usage_marks_over_budget_on_redis_error(self):
        """record_usage doesn't raise (response already sent), but marks
        the session as over-budget locally so subsequent checks also fail."""
        mock_redis = MagicMock()
        mock_redis.incrby = MagicMock(side_effect=ConnectionError("redis down"))

        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=mock_redis)
        # record_usage should not raise even on Redis error
        tracker.record_usage("sess-fail-record", input_tokens=100, output_tokens=100)

        # But the in-memory state should be set to max (fail closed for next check)
        # The implementation sets _usage[session_id] = max on Redis error
        assert tracker._usage.get("sess-fail-record") == 10000


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TTL is set on the Redis key
# ═══════════════════════════════════════════════════════════════════════════════


class TestTTL:
    """The budget key must have a TTL set (24 hours = 86400s)."""

    def test_ttl_set_on_first_record(self, shared_redis):
        """After the first record_usage, the Redis key has a TTL > 0."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)

        tracker.record_usage("sess-ttl", input_tokens=100, output_tokens=100)

        key = "token_budget:session:sess-ttl"
        ttl = client.ttl(key)
        # TTL should be approximately 86400 (might be 86399 due to timing)
        assert ttl > 0
        assert ttl <= _SESSION_BUDGET_TTL_SECONDS

    def test_ttl_value_is_24_hours(self, shared_redis):
        """The TTL constant is exactly 86400 seconds (24 hours)."""
        assert _SESSION_BUDGET_TTL_SECONDS == 86400

    def test_ttl_refreshed_on_subsequent_usage(self, shared_redis):
        """Each record_usage call refreshes the TTL (active session stays alive)."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)

        tracker.record_usage("sess-ttl2", input_tokens=100, output_tokens=100)
        key = "token_budget:session:sess-ttl2"

        # Manually shorten TTL to simulate time passing
        client.expire(key, 100)
        assert client.ttl(key) <= 100

        # Record more usage — TTL should be refreshed back to 86400
        tracker.record_usage("sess-ttl2", input_tokens=50, output_tokens=50)
        new_ttl = client.ttl(key)
        assert new_ttl > 100  # Must have been refreshed
        assert new_ttl <= _SESSION_BUDGET_TTL_SECONDS


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Refusal metric is emitted
# ═══════════════════════════════════════════════════════════════════════════════


class TestRefusalMetric:
    """aigent.token_budget.exceeded counter must be incremented on refusal."""

    def test_metric_emitted_on_budget_exceeded(self, shared_redis):
        """When check_budget raises, the metric counter is incremented."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=100, redis_client=client)
        tracker.record_usage("sess-metric", input_tokens=60, output_tokens=60)  # 120 > 100

        with patch("src.core.token_budget.token_budget_exceeded_blocks") as mock_metric:
            with pytest.raises(TokenBudgetExceeded):
                tracker.check_budget("sess-metric")
            mock_metric.add.assert_called_once_with(1, {"session_id": "sess-metric"})

    def test_metric_emitted_on_redis_failure(self):
        """Fail-closed refusal ALSO emits the metric (observability of the cap)."""
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(side_effect=ConnectionError("redis down"))

        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=mock_redis)

        with patch("src.core.token_budget.token_budget_exceeded_blocks") as mock_metric:
            with pytest.raises(TokenBudgetExceeded):
                tracker.check_budget("sess-redis-fail")
            mock_metric.add.assert_called_once_with(1, {"session_id": "sess-redis-fail"})

    def test_metric_not_emitted_when_under_budget(self, shared_redis):
        """No false metric emission when the budget has headroom."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        tracker.record_usage("sess-ok", input_tokens=100, output_tokens=100)

        with patch("src.core.token_budget.token_budget_exceeded_blocks") as mock_metric:
            tracker.check_budget("sess-ok")  # Should not raise
            mock_metric.add.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Existing behaviour preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestExistingBehaviourPreserved:
    """TokenBudgetExceeded fields and the 4 call-site patterns still work."""

    def test_exception_has_session_id_used_limit(self):
        """TokenBudgetExceeded has .session_id, .used, .limit attributes."""
        exc = TokenBudgetExceeded("sess-123", used=5000, limit=4000)
        assert exc.session_id == "sess-123"
        assert exc.used == 5000
        assert exc.limit == 4000

    def test_exception_message_format(self):
        """Message contains usage, limit, and suggests new session."""
        exc = TokenBudgetExceeded("sess-x", used=9000, limit=8000)
        msg = str(exc)
        assert "9000" in msg
        assert "8000" in msg
        assert "new session" in msg.lower()

    def test_check_budget_callable_sync(self, shared_redis):
        """check_budget is sync (called from async supervisor without await)."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        # Must be callable without await — sync method
        tracker.check_budget("sess-sync-test")  # No exception

    def test_record_usage_callable_sync(self, shared_redis):
        """record_usage is sync (called from _invoke_sync on a real OS thread)."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        # Must be callable without await — sync method
        tracker.record_usage("sess-sync-test", input_tokens=100, output_tokens=100)

    def test_get_usage_and_get_remaining_sync(self, shared_redis):
        """Accessor methods are sync."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        assert isinstance(tracker.get_usage("sess-x"), int)
        assert isinstance(tracker.get_remaining("sess-x"), int)

    def test_reset_sync(self, shared_redis):
        """reset is sync and clears the budget."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        tracker.record_usage("sess-reset", input_tokens=500, output_tokens=500)
        tracker.reset("sess-reset")
        assert tracker.get_usage("sess-reset") == 0

    def test_record_does_not_raise_when_over_budget(self, shared_redis):
        """record_usage never raises — the response was already sent.
        Only the NEXT check_budget raises."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=100, redis_client=client)
        # This pushes over budget but must NOT raise
        tracker.record_usage("sess-over", input_tokens=200, output_tokens=200)
        # Only check_budget raises
        with pytest.raises(TokenBudgetExceeded):
            tracker.check_budget("sess-over")

    def test_sessions_isolated(self, shared_redis):
        """Different session_ids have independent budgets."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=client)
        tracker.record_usage("sess-A", input_tokens=500, output_tokens=0)
        tracker.record_usage("sess-B", input_tokens=100, output_tokens=0)
        assert tracker.get_usage("sess-A") == 500
        assert tracker.get_usage("sess-B") == 100

    def test_max_tokens_configurable(self, shared_redis):
        """max_tokens_per_session parameter is respected."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=42, redis_client=client)
        assert tracker.max_tokens == 42

    def test_call_site_pattern_supervisor_check(self, shared_redis):
        """Simulates src/supervisor/agent.py pattern:
        budget_tracker.check_budget(session_id) before Bedrock call."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        # Under budget — should not raise
        tracker.check_budget("supervisor-sess")

    def test_call_site_pattern_bedrock_record(self, shared_redis):
        """Simulates src/core/bedrock.py pattern:
        budget_tracker.record_usage(session_id, input_tokens, output_tokens)."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=10000, redis_client=client)
        tracker.record_usage("bedrock-sess", input_tokens=1500, output_tokens=2000)
        assert tracker.get_usage("bedrock-sess") == 3500


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for robustness."""

    def test_zero_usage_record(self, shared_redis):
        """Recording 0 tokens is valid (e.g. cached response)."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=client)
        tracker.record_usage("sess-zero", input_tokens=0, output_tokens=0)
        assert tracker.get_usage("sess-zero") == 0

    def test_new_session_starts_at_zero(self, shared_redis):
        """A session_id never seen before has 0 usage."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=client)
        assert tracker.get_usage("brand-new-sess") == 0
        assert tracker.get_remaining("brand-new-sess") == 1000

    def test_key_namespacing_no_collision_with_rate_limiter(self, shared_redis):
        """Budget keys use 'token_budget:session:' prefix — no collision with
        rate limiter keys ('rate:user:', 'budget:global:')."""
        client = fakeredis.FakeRedis(server=shared_redis, decode_responses=True)
        tracker = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=client)
        tracker.record_usage("sess-ns", input_tokens=100, output_tokens=100)

        # Verify the key format
        key = "token_budget:session:sess-ns"
        assert client.exists(key)
        # Rate limiter keys would be "rate:user:..." or "budget:global:..."
        # No collision by construction
