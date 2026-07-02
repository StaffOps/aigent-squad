"""Tests for spec 11: Token budget (T4) and history truncation (T5).

Written against the BEHAVIOR CONTRACT. No Bedrock calls.
"""
import pytest
from src.core.state_store import ConversationMessage


# ═══════════════════════════════════════════════════════════════════════════════
# T4: Token Budget (src/core/token_budget.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionBudgetTracker:
    """SessionBudgetTracker enforces per-session hard cap."""

    def test_new_session_has_zero_usage(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        assert tracker.get_usage("sess-1") == 0

    def test_new_session_has_full_remaining(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        assert tracker.get_remaining("sess-1") == 1000

    def test_record_usage_accumulates(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=10000)
        tracker.record_usage("sess-1", input_tokens=100, output_tokens=50)
        assert tracker.get_usage("sess-1") == 150
        tracker.record_usage("sess-1", input_tokens=200, output_tokens=100)
        assert tracker.get_usage("sess-1") == 450

    def test_remaining_decreases_after_usage(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-1", input_tokens=300, output_tokens=200)
        assert tracker.get_remaining("sess-1") == 500

    def test_check_budget_passes_when_under(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-1", input_tokens=100, output_tokens=100)
        # Should not raise
        tracker.check_budget("sess-1")

    def test_check_budget_raises_when_at_limit(self):
        from src.core.token_budget import SessionBudgetTracker, TokenBudgetExceeded
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-1", input_tokens=500, output_tokens=500)
        with pytest.raises(TokenBudgetExceeded):
            tracker.check_budget("sess-1")

    def test_check_budget_raises_when_over_limit(self):
        from src.core.token_budget import SessionBudgetTracker, TokenBudgetExceeded
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-1", input_tokens=600, output_tokens=500)
        with pytest.raises(TokenBudgetExceeded):
            tracker.check_budget("sess-1")

    def test_record_does_not_raise_immediately(self):
        """record_usage does NOT raise; next check_budget will."""
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=100)
        # This pushes over budget but should NOT raise
        tracker.record_usage("sess-1", input_tokens=200, output_tokens=200)
        # Only check_budget raises
        from src.core.token_budget import TokenBudgetExceeded
        with pytest.raises(TokenBudgetExceeded):
            tracker.check_budget("sess-1")

    def test_reset_clears_usage(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-1", input_tokens=999, output_tokens=0)
        tracker.reset("sess-1")
        assert tracker.get_usage("sess-1") == 0
        assert tracker.get_remaining("sess-1") == 1000

    def test_sessions_are_independent(self):
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker(max_tokens_per_session=1000)
        tracker.record_usage("sess-A", input_tokens=500, output_tokens=0)
        tracker.record_usage("sess-B", input_tokens=100, output_tokens=0)
        assert tracker.get_usage("sess-A") == 500
        assert tracker.get_usage("sess-B") == 100

    def test_defaults_to_settings_value(self, monkeypatch):
        monkeypatch.setattr("src.core.token_budget.settings.session_token_budget", 99999)
        from src.core.token_budget import SessionBudgetTracker
        tracker = SessionBudgetTracker()
        assert tracker.max_tokens == 99999


class TestTokenBudgetExceeded:
    """TokenBudgetExceeded exception has correct attributes and message."""

    def test_exception_attributes(self):
        from src.core.token_budget import TokenBudgetExceeded
        exc = TokenBudgetExceeded("sess-x", used=5000, limit=4000)
        assert exc.session_id == "sess-x"
        assert exc.used == 5000
        assert exc.limit == 4000

    def test_exception_message_contains_usage_and_limit(self):
        from src.core.token_budget import TokenBudgetExceeded
        exc = TokenBudgetExceeded("sess-x", used=5000, limit=4000)
        msg = str(exc)
        assert "5000" in msg
        assert "4000" in msg
        assert "new session" in msg.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# T5: History Truncation by Tokens
# ═══════════════════════════════════════════════════════════════════════════════


def _make_msg(content: str, role: str = "user") -> ConversationMessage:
    return ConversationMessage(role=role, content=content, timestamp="2026-01-01T00:00:00Z")


class TestTruncateHistoryByTokens:
    """truncate_history_by_tokens drops oldest, keeps most recent."""

    def test_empty_history_returns_empty(self):
        from src.core.token_budget import truncate_history_by_tokens
        result, total = truncate_history_by_tokens([], max_tokens=1000)
        assert result == []
        assert total == 0

    def test_all_fit_within_budget(self):
        from src.core.token_budget import truncate_history_by_tokens
        msgs = [_make_msg("hi"), _make_msg("hello")]
        result, total = truncate_history_by_tokens(msgs, max_tokens=10000)
        assert len(result) == 2
        assert total > 0

    def test_oldest_dropped_when_over_budget(self):
        from src.core.token_budget import truncate_history_by_tokens
        # Each msg ~25 tokens content + 4 overhead = ~29 tokens
        msgs = [_make_msg("a" * 100) for _ in range(10)]
        # Budget = 60 tokens → should keep only ~2 most recent
        result, total = truncate_history_by_tokens(msgs, max_tokens=60)
        assert len(result) < 10
        assert len(result) >= 1
        # Most recent messages are preserved
        assert result[-1] is msgs[-1]
        assert total <= 60

    def test_preserves_order(self):
        from src.core.token_budget import truncate_history_by_tokens
        msgs = [_make_msg(f"msg-{i}") for i in range(5)]
        result, _ = truncate_history_by_tokens(msgs, max_tokens=10000)
        for i in range(len(result) - 1):
            # Original order is preserved
            orig_idx_a = msgs.index(result[i])
            orig_idx_b = msgs.index(result[i + 1])
            assert orig_idx_a < orig_idx_b

    def test_single_message_exceeding_budget_returns_empty(self):
        from src.core.token_budget import truncate_history_by_tokens
        # One message with huge content
        msgs = [_make_msg("x" * 10000)]
        # Budget is tiny
        result, total = truncate_history_by_tokens(msgs, max_tokens=10)
        # Can't fit even one message
        assert len(result) == 0
        assert total == 0

    def test_uses_settings_default_when_no_max(self, monkeypatch):
        from src.core.token_budget import truncate_history_by_tokens
        monkeypatch.setattr("src.core.token_budget.settings.history_max_tokens", 50)
        msgs = [_make_msg("a" * 100) for _ in range(10)]
        result, total = truncate_history_by_tokens(msgs)
        assert total <= 50

    def test_token_cost_includes_overhead(self):
        """Each message cost = estimate_tokens(content) + 4 overhead."""
        from src.core.token_budget import truncate_history_by_tokens
        from src.core.model_tier import estimate_tokens
        msg = _make_msg("a" * 40)  # 40 chars → 10 tokens content + 4 overhead = 14
        result, total = truncate_history_by_tokens([msg], max_tokens=100)
        expected = estimate_tokens("a" * 40) + 4
        assert total == expected
