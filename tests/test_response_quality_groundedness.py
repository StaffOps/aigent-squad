"""Tests for src.core.response_quality's groundedness dimension (spec 35
requirements.md PR-05) — the quality twin of the canary check: canary
catches data that SHOULDN'T leave, groundedness catches claims that never
came IN.

Two different confidence levels, two different behaviors:
- Resource IDs: never legitimately "derived" — hard block, same pattern as
  the other T1 defect classes.
- Bare numeric claims: CAN be a legitimate derived value (sum, average,
  rounding) that won't appear verbatim in infra_data — metric-only, never
  blocking (see response_quality.py module docstring for the reasoning,
  same tradeoff class as F-005's canary redact-and-continue decision).

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import pytest
from unittest.mock import patch

from src.core.response_quality import ResponseQualityGuard
from src.core.guardrail import GuardrailBlockedError


def _make_guard(enabled: bool = True) -> ResponseQualityGuard:
    g = ResponseQualityGuard.__new__(ResponseQualityGuard)
    g.enabled = enabled
    return g


# ---------------------------------------------------------------------------
# Resource-ID groundedness — hard block
# ---------------------------------------------------------------------------


class TestResourceIdGroundedness:
    def test_grounded_instance_id_passes(self):
        guard = _make_guard(enabled=True)
        infra_data = "EC2 instances: i-0abc123def456789 (running), i-0fff999888777666 (stopped)"
        response = "You have one running instance: i-0abc123def456789."
        guard.scan(response, agent_id="aws", infra_data=infra_data)  # no raise

    def test_ungrounded_instance_id_blocked(self):
        guard = _make_guard(enabled=True)
        infra_data = "EC2 instances: i-0abc123def456789 (running)"
        response = "You have a running instance: i-0fab999999999999 that I never saw in the data."
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(response, agent_id="aws", infra_data=infra_data)
        assert "quality:ungrounded_resource_id" in exc_info.value.categories

    def test_ungrounded_arn_blocked(self):
        guard = _make_guard(enabled=True)
        infra_data = "No IAM policies found."
        response = "The role arn:aws:iam::524040971621:role/made-up-role has wildcard access."
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(response, agent_id="security", infra_data=infra_data)
        assert "quality:ungrounded_resource_id" in exc_info.value.categories

    def test_grounded_security_group_id_passes(self):
        guard = _make_guard(enabled=True)
        infra_data = "Security groups: sg-0123456789abcdef0 allows 0.0.0.0/0 on port 22"
        response = "sg-0123456789abcdef0 allows inbound SSH from anywhere — high risk."
        guard.scan(response, agent_id="security", infra_data=infra_data)  # no raise

    def test_case_insensitive_match(self):
        guard = _make_guard(enabled=True)
        infra_data = "instance I-0ABC123DEF456789 is running"
        response = "Instance i-0abc123def456789 is currently running."
        guard.scan(response, agent_id="aws", infra_data=infra_data)  # no raise

    def test_no_infra_data_skips_groundedness_entirely(self):
        """Callers that don't pass infra_data get NO groundedness checking —
        not a false-positive flood against an empty string."""
        guard = _make_guard(enabled=True)
        response = "The instance i-0fab999999999999 is running."
        guard.scan(response, agent_id="aws")  # infra_data defaults to "" — no raise

    def test_clean_response_no_ids_passes(self):
        guard = _make_guard(enabled=True)
        infra_data = "some infra data with no IDs mentioned"
        response = "Everything looks healthy, no action needed."
        guard.scan(response, agent_id="aws", infra_data=infra_data)  # no raise


# ---------------------------------------------------------------------------
# Numeric-claim groundedness — metric-only, never blocking
# ---------------------------------------------------------------------------


class TestNumericClaimGroundedness:
    def test_grounded_dollar_amount_no_metric(self):
        guard = _make_guard(enabled=True)
        infra_data = "Last 30 days cost: $191,225.98"
        response = "Your spend over the last 30 days was $191,225.98."
        with patch("src.core.response_quality.ungrounded_numeric_claims") as mock_metric:
            guard.scan(response, agent_id="finops", infra_data=infra_data)
            mock_metric.add.assert_not_called()

    def test_ungrounded_dollar_amount_never_raises(self):
        """The whole point: an unmatched dollar figure is NEVER a hard block,
        unlike a resource ID — it might be a legitimate derived value."""
        guard = _make_guard(enabled=True)
        infra_data = "Per-instance costs: $50.00, $75.00, $25.00"
        response = "Your average per-instance cost is $50.00... total estimated at $999.99/mo."
        guard.scan(response, agent_id="finops", infra_data=infra_data)  # no raise

    def test_ungrounded_dollar_amount_increments_metric(self):
        guard = _make_guard(enabled=True)
        infra_data = "Per-instance costs: $50.00, $75.00"
        response = "Total projected spend: $999.99 next month."
        with patch("src.core.response_quality.ungrounded_numeric_claims") as mock_metric:
            guard.scan(response, agent_id="finops", infra_data=infra_data)
            mock_metric.add.assert_called_once_with(1, {"agent_id": "finops"})

    def test_no_infra_data_skips_numeric_check_too(self):
        guard = _make_guard(enabled=True)
        response = "Total spend: $999.99."
        with patch("src.core.response_quality.ungrounded_numeric_claims") as mock_metric:
            guard.scan(response, agent_id="finops")  # infra_data defaults to ""
            mock_metric.add.assert_not_called()


# ---------------------------------------------------------------------------
# Combined — resource ID + existing structural patterns
# ---------------------------------------------------------------------------


class TestCombinedWithExistingPatterns:
    def test_ungrounded_id_and_tool_scaffolding_both_reported(self):
        guard = _make_guard(enabled=True)
        infra_data = "instance i-0abc123def456789 is running"
        response = "<use_mcp_tool>describe instance i-0fab999999999999</use_mcp_tool>"
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(response, agent_id="aws", infra_data=infra_data)
        categories = exc_info.value.categories
        assert "quality:tool_scaffolding" in categories
        assert "quality:ungrounded_resource_id" in categories

    def test_disabled_skips_groundedness_too(self):
        guard = _make_guard(enabled=False)
        response = "The instance i-0fab999999999999 is running."
        guard.scan(response, agent_id="aws", infra_data="nothing relevant here")  # no raise
