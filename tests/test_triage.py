"""Tests for src.core.triage — should_investigate heuristic."""

from src.core.triage import should_investigate


class TestShouldInvestigate:
    def test_should_investigate_with_investigation_keyword(self):
        assert should_investigate("why is latency high?") is True

    def test_should_investigate_with_trivial_keyword(self):
        assert should_investigate("how do I configure SSL?") is False

    def test_should_investigate_force_bypasses_heuristic(self):
        assert should_investigate("list ec2", force=True) is True

    def test_should_investigate_neutral_query(self):
        assert should_investigate("show me cost for last week") is False

    def test_should_investigate_mixed_keywords_trivial_wins(self):
        # 'how do i' (trivial) + 'investigate' (investigation) → trivial wins
        assert should_investigate("how do I investigate latency") is False
