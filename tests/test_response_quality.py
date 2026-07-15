"""Tests for src.core.response_quality — spec 35 T1 (structural quality gate).

Tests against the BEHAVIOR CONTRACT:
- scan() clean response → no exception
- scan() with each defect pattern → raises GuardrailBlockedError with correct category
- scan() with multiple patterns → raises with ALL detected categories
- scan() when disabled → no exception even with a defect present
- scan() with short response (<10 chars) → no exception
- _audit() called on detection (structured fields, no leaked content in log)
- aigent.quality.violations metric incremented on detection

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import hashlib
import pytest
from unittest.mock import patch

from src.core.response_quality import ResponseQualityGuard, _digest
from src.core.guardrail import GuardrailBlockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guard(enabled: bool = True) -> ResponseQualityGuard:
    """Build a ResponseQualityGuard with controlled enabled state."""
    g = ResponseQualityGuard.__new__(ResponseQualityGuard)
    g.enabled = enabled
    return g


# ---------------------------------------------------------------------------
# _digest
# ---------------------------------------------------------------------------


class TestDigest:
    def test_deterministic(self):
        assert _digest("response text") == _digest("response text")

    def test_length_12(self):
        assert len(_digest("any content")) == 12

    def test_matches_sha256_prefix(self):
        text = "some response with a defect"
        expected = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        assert _digest(text) == expected


# ---------------------------------------------------------------------------
# scan — clean response (no defect)
# ---------------------------------------------------------------------------


class TestScanClean:
    def test_no_exception_on_clean_response(self):
        guard = _make_guard(enabled=True)
        guard.scan(
            "You have 12 EC2 instances running, 3 stopped. All in us-east-1.",
            agent_id="aws", user_id="u", session_id="s",
        )

    def test_no_exception_discussing_access_denied_conceptually(self):
        """A legitimate advisory answer CAN discuss access-denied situations in
        prose without leaking a raw exception — must not false-positive."""
        guard = _make_guard(enabled=True)
        guard.scan(
            "Your policy currently denies access to this action because the "
            "role lacks the ec2:DescribeInstances permission. Consider adding it.",
            agent_id="aws", user_id="u", session_id="s",
        )

    def test_no_exception_on_short_response(self):
        guard = _make_guard(enabled=True)
        guard.scan("err", agent_id="a", user_id="u", session_id="s")


# ---------------------------------------------------------------------------
# scan — tool-scaffolding leak (F-001 class)
# ---------------------------------------------------------------------------


class TestScanToolScaffolding:
    def test_use_mcp_tool_tag_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "<use_mcp_tool><server_name>aws-mcp</server_name></use_mcp_tool>",
                agent_id="aws", user_id="u", session_id="s",
            )
        assert "quality:tool_scaffolding" in exc_info.value.categories
        assert exc_info.value.source == "OUTPUT"
        assert exc_info.value.reason == "blocked"

    def test_tool_call_tag_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan("<tool_call>list_pods</tool_call>", agent_id="k8s", user_id="u", session_id="s")
        assert "quality:tool_scaffolding" in exc_info.value.categories

    def test_invoke_tag_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError):
            guard.scan("<invoke name=\"describe_instances\">", agent_id="aws", user_id="u", session_id="s")


# ---------------------------------------------------------------------------
# scan — raw adapter/infra error leak (F-002/F-003 class)
# ---------------------------------------------------------------------------


class TestScanRawAdapterError:
    def test_our_own_adapter_error_prefix_raises(self):
        """Our own adapters format failures as f"[svc] error: {e}" — if this
        literal prefix reaches the user, the raw error leaked unsummarized."""
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "Here is what I found: [ec2] error: AccessDenied when calling DescribeInstances",
                agent_id="aws", user_id="u", session_id="s",
            )
        assert "quality:raw_adapter_error" in exc_info.value.categories

    def test_mcp_adapter_error_prefix_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "[mcp:k8s-mcp] error: unhandled errors in a TaskGroup (1 sub-exception)",
                agent_id="kubernetes", user_id="u", session_id="s",
            )
        categories = exc_info.value.categories
        assert "quality:raw_adapter_error" in categories
        assert "quality:raw_taskgroup_exception" in categories

    def test_raw_traceback_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "Traceback (most recent call last):\n  File x.py, line 1",
                agent_id="a", user_id="u", session_id="s",
            )
        assert "quality:raw_traceback" in exc_info.value.categories

    def test_raw_botocore_exception_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "Failed: botocore.exceptions.ClientError occurred",
                agent_id="a", user_id="u", session_id="s",
            )
        assert "quality:raw_botocore_exception" in exc_info.value.categories

    def test_raw_boto3_error_string_raises(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "An error occurred (AccessDeniedException) when calling the DescribeInstances operation",
                agent_id="aws", user_id="u", session_id="s",
            )
        assert "quality:raw_boto3_error_string" in exc_info.value.categories


# ---------------------------------------------------------------------------
# scan — multiple patterns present → all categories reported
# ---------------------------------------------------------------------------


class TestScanMultiplePatterns:
    def test_all_detected_categories_included(self):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.scan(
                "<use_mcp_tool>x</use_mcp_tool> and also Traceback (most recent call last):",
                agent_id="a", user_id="u", session_id="s",
            )
        categories = exc_info.value.categories
        assert "quality:tool_scaffolding" in categories
        assert "quality:raw_traceback" in categories


# ---------------------------------------------------------------------------
# scan — disabled (no-op)
# ---------------------------------------------------------------------------


class TestScanDisabled:
    def test_no_exception_even_with_defect_present(self):
        guard = _make_guard(enabled=False)
        guard.scan(
            "<use_mcp_tool>leaked</use_mcp_tool>",
            agent_id="a", user_id="u", session_id="s",
        )


# ---------------------------------------------------------------------------
# _audit — structured logging, no leaked content
# ---------------------------------------------------------------------------


class TestAudit:
    @patch("src.core.response_quality.logger")
    def test_audit_called_on_detection(self, mock_logger):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError):
            guard.scan(
                "<use_mcp_tool>leaked</use_mcp_tool>",
                agent_id="test-agent", user_id="user-42", session_id="sess-99",
            )

        mock_logger.warning.assert_called_once()
        extra = mock_logger.warning.call_args.kwargs.get("extra") or mock_logger.warning.call_args[1].get("extra")
        assert extra["audit"] is True
        assert extra["event"] == "response_quality_block"
        assert extra["agent_id"] == "test-agent"
        assert extra["user_id"] == "user-42"
        assert extra["session_id"] == "sess-99"
        assert "quality:tool_scaffolding" in extra["categories"]
        assert "response_digest" in extra

    @patch("src.core.response_quality.logger")
    def test_leaked_content_never_in_audit_log(self, mock_logger):
        guard = _make_guard(enabled=True)
        secret_looking_text = "<use_mcp_tool>super-secret-payload-xyz</use_mcp_tool>"
        with pytest.raises(GuardrailBlockedError):
            guard.scan(secret_looking_text, agent_id="a", user_id="u", session_id="s")

        call_str = str(mock_logger.warning.call_args)
        assert "super-secret-payload-xyz" not in call_str

    @patch("src.core.response_quality.logger")
    def test_no_audit_on_clean_response(self, mock_logger):
        guard = _make_guard(enabled=True)
        guard.scan("A perfectly normal answer.", agent_id="a", user_id="u", session_id="s")
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# aigent.quality.violations metric
# ---------------------------------------------------------------------------


class TestQualityMetric:
    @patch("src.core.response_quality.quality_violations")
    def test_metric_incremented_on_detection(self, mock_counter):
        guard = _make_guard(enabled=True)
        with pytest.raises(GuardrailBlockedError):
            guard.scan(
                "<use_mcp_tool>leaked</use_mcp_tool>",
                agent_id="aws", user_id="u", session_id="s",
            )
        mock_counter.add.assert_called_once_with(1, {"agent_id": "aws", "category": "tool_scaffolding"})

    @patch("src.core.response_quality.quality_violations")
    def test_metric_not_incremented_on_clean_response(self, mock_counter):
        guard = _make_guard(enabled=True)
        guard.scan("A perfectly normal answer.", agent_id="aws", user_id="u", session_id="s")
        mock_counter.add.assert_not_called()
