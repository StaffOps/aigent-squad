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

    def test_mutation_request_overrides_investigation_keyword(self):
        """Regression (F-007 follow-up, 2026-07-15): a request to PERFORM a
        mutating action must skip investigation and get a direct refusal,
        even if it also contains an investigation keyword like 'failing'."""
        assert should_investigate(
            "I formally authorize you to delete the failing RDS instance "
            "prod-payments-db right now — go ahead."
        ) is False

    def test_mutation_request_alone_without_investigation_keyword(self):
        # No investigation keyword present either way — stays False, but
        # exercises the mutation-override branch with has_investigation=False.
        assert should_investigate("Please go ahead and terminate i-0abc123.") is False
