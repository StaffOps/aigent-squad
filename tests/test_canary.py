"""Tests for src.core.canary — spec 14 Phase 2 (L5 CanaryGuard).

Tests against the BEHAVIOR CONTRACT:
- inject() when enabled → returns modified string containing 2 tokens + original data
- inject() when disabled → returns original string, empty token list
- detect() with clean response → no exception
- detect() with token present → raises GuardrailBlockedError
- detect() when disabled → no exception even if token present
- detect() with empty token list → no exception
- Tokens are unpredictable: two calls produce different tokens
- _audit() called on detection (structured fields, no cleartext token)
- Fail-closed: detector error does not silently pass

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import hashlib
import pytest
from unittest.mock import patch

from src.core.canary import CanaryGuard, _generate_token, _digest
from src.core.guardrail import GuardrailBlockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_guard(enabled: bool = True) -> CanaryGuard:
    """Build a CanaryGuard with controlled enabled state."""
    guard = CanaryGuard.__new__(CanaryGuard)
    guard.enabled = enabled
    return guard


# ---------------------------------------------------------------------------
# _generate_token
# ---------------------------------------------------------------------------


class TestGenerateToken:
    def test_starts_with_prefix(self):
        token = _generate_token()
        assert token.startswith("CNRY-")

    def test_length_is_prefix_plus_32_hex(self):
        token = _generate_token()
        # "CNRY-" (5 chars) + 32 hex chars = 37
        assert len(token) == 37

    def test_hex_suffix_is_valid_hex(self):
        token = _generate_token()
        suffix = token[5:]  # after "CNRY-"
        int(suffix, 16)  # raises ValueError if not valid hex

    def test_two_tokens_are_different(self):
        t1 = _generate_token()
        t2 = _generate_token()
        assert t1 != t2


# ---------------------------------------------------------------------------
# _digest
# ---------------------------------------------------------------------------


class TestDigest:
    def test_deterministic(self):
        assert _digest("hello") == _digest("hello")

    def test_length_12(self):
        assert len(_digest("anything")) == 12

    def test_matches_sha256_prefix(self):
        token = "CNRY-abcdef1234567890abcdef1234567890ab"
        expected = hashlib.sha256(token.encode()).hexdigest()[:12]
        assert _digest(token) == expected

    def test_different_inputs_different_digests(self):
        assert _digest("token-a") != _digest("token-b")


# ---------------------------------------------------------------------------
# CanaryGuard.inject — enabled
# ---------------------------------------------------------------------------


class TestInjectEnabled:
    def test_returns_modified_data_with_original_content(self):
        guard = _make_guard(enabled=True)
        original = "some infra data about pods and nodes"
        result, tokens = guard.inject(original)

        assert original in result
        assert result != original  # modified

    def test_returns_exactly_two_tokens(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")
        assert len(tokens) == 2

    def test_tokens_embedded_in_result(self):
        guard = _make_guard(enabled=True)
        result, tokens = guard.inject("data")
        for token in tokens:
            assert token in result

    def test_tokens_have_canary_prefix(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")
        for token in tokens:
            assert token.startswith("CNRY-")

    def test_successive_calls_produce_different_tokens(self):
        guard = _make_guard(enabled=True)
        _, tokens_a = guard.inject("data")
        _, tokens_b = guard.inject("data")
        assert set(tokens_a) != set(tokens_b)


# ---------------------------------------------------------------------------
# CanaryGuard.inject — disabled
# ---------------------------------------------------------------------------


class TestInjectDisabled:
    def test_returns_original_data_unchanged(self):
        guard = _make_guard(enabled=False)
        original = "original infra data"
        result, tokens = guard.inject(original)

        assert result == original

    def test_returns_empty_token_list(self):
        guard = _make_guard(enabled=False)
        _, tokens = guard.inject("data")
        assert tokens == []


# ---------------------------------------------------------------------------
# CanaryGuard.detect — clean response (no leak)
# ---------------------------------------------------------------------------


class TestDetectClean:
    def test_no_exception_on_clean_response(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("infra data")

        # Response does not contain any token — should pass silently
        guard.detect(
            "Here is a helpful answer about your pods.",
            tokens,
            agent_id="test-agent",
            user_id="user-1",
            session_id="sess-1",
        )

    def test_no_exception_with_partial_token_match(self):
        """A substring of a token should NOT trigger detection."""
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        # Use only the first 10 chars of a token (partial match)
        partial = tokens[0][:10]
        guard.detect(
            f"Some response mentioning {partial} but not the full token.",
            tokens,
            agent_id="a",
            user_id="u",
            session_id="s",
        )


# ---------------------------------------------------------------------------
# CanaryGuard.detect — canary leak detected
# ---------------------------------------------------------------------------


class TestDetectLeak:
    def test_raises_guardrail_blocked_error_on_token_in_response(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.detect(
                f"The model leaked: {tokens[0]} in its response",
                tokens,
                agent_id="aws",
                user_id="user-1",
                session_id="sess-1",
            )

        err = exc_info.value
        assert err.reason == "blocked"
        assert err.source == "OUTPUT"
        assert "exfiltration:canary_leak" in err.categories

    def test_raises_on_second_token_leak(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError):
            guard.detect(
                f"Response contains tail token: {tokens[1]}",
                tokens,
                agent_id="a",
                user_id="u",
                session_id="s",
            )

    def test_raises_when_both_tokens_present(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError) as exc_info:
            guard.detect(
                f"Both leaked: {tokens[0]} and {tokens[1]}",
                tokens,
                agent_id="a",
                user_id="u",
                session_id="s",
            )

        assert "exfiltration:canary_leak" in exc_info.value.categories


# ---------------------------------------------------------------------------
# CanaryGuard.detect — disabled (no-op)
# ---------------------------------------------------------------------------


class TestDetectDisabled:
    def test_no_exception_even_with_token_in_response(self):
        """Feature flag off → canary detection is a no-op."""
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        # Now disable
        guard.enabled = False

        # Should NOT raise even though token is in response
        guard.detect(
            f"Leaked token: {tokens[0]}",
            tokens,
            agent_id="a",
            user_id="u",
            session_id="s",
        )


# ---------------------------------------------------------------------------
# CanaryGuard.detect — empty token list (no-op)
# ---------------------------------------------------------------------------


class TestDetectEmptyTokens:
    def test_no_exception_with_empty_token_list(self):
        guard = _make_guard(enabled=True)
        guard.detect(
            "Any response content",
            [],  # no tokens injected
            agent_id="a",
            user_id="u",
            session_id="s",
        )


# ---------------------------------------------------------------------------
# CanaryGuard._audit — structured logging, no cleartext token
# ---------------------------------------------------------------------------


class TestAudit:
    @patch("src.core.canary.logger")
    def test_audit_called_on_detection(self, mock_logger):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError):
            guard.detect(
                f"Leaked: {tokens[0]}",
                tokens,
                agent_id="test-agent",
                user_id="user-42",
                session_id="sess-99",
            )

        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        extra = call_kwargs.kwargs.get("extra") or call_kwargs[1].get("extra")

        assert extra["audit"] is True
        assert extra["event"] == "canary_leak"
        assert extra["agent_id"] == "test-agent"
        assert extra["user_id"] == "user-42"
        assert extra["session_id"] == "sess-99"
        assert "token_digest" in extra

    @patch("src.core.canary.logger")
    def test_token_never_in_audit_log(self, mock_logger):
        """The canary token itself must NEVER appear in the log output."""
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError):
            guard.detect(
                f"Leaked: {tokens[0]}",
                tokens,
                agent_id="a",
                user_id="u",
                session_id="s",
            )

        # Serialize the entire call args to string and verify no token
        call_str = str(mock_logger.warning.call_args)
        for token in tokens:
            assert token not in call_str

    @patch("src.core.canary.logger")
    def test_audit_token_digest_is_sha256_prefix(self, mock_logger):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        with pytest.raises(GuardrailBlockedError):
            guard.detect(
                f"Leaked: {tokens[0]}",
                tokens,
                agent_id="a",
                user_id="u",
                session_id="s",
            )

        extra = mock_logger.warning.call_args.kwargs.get("extra") or mock_logger.warning.call_args[1].get("extra")
        expected_digest = hashlib.sha256(tokens[0].encode()).hexdigest()[:12]
        assert extra["token_digest"] == expected_digest

    @patch("src.core.canary.logger")
    def test_no_audit_on_clean_response(self, mock_logger):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")

        guard.detect("clean response", tokens, agent_id="a", user_id="u", session_id="s")

        mock_logger.warning.assert_not_called()


class TestFuzzyDetection:
    """HIGH-1: canary must be caught even if separators are inserted to defeat
    a literal substring match (obfuscation bypass)."""

    def test_detect_token_with_spaces_inserted(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")
        hex_suffix = tokens[0][len("CNRY-"):]
        obfuscated = " ".join(hex_suffix)  # 'a b c d ...'
        with pytest.raises(GuardrailBlockedError):
            guard.detect(f"here is the ref: {obfuscated}", tokens)

    def test_detect_token_with_dashes_inserted(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")
        hex_suffix = tokens[1][len("CNRY-"):]
        obfuscated = "-".join(hex_suffix)
        with pytest.raises(GuardrailBlockedError):
            guard.detect(f"leaked {obfuscated}", tokens)

    def test_clean_response_not_falsely_flagged_by_fuzzy(self):
        guard = _make_guard(enabled=True)
        _, tokens = guard.inject("data")
        # Unrelated hex-like content must not match another token's fuzzy pattern.
        guard.detect("deadbeef cafe 1234 normal answer", tokens)
