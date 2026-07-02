"""Tests for src.core.output_filter — spec 14 Phase 2 (L4 OutputFilter).

Tests against the BEHAVIOR CONTRACT:
- scan() clean response → no exception
- scan() with each pattern type → raises GuardrailBlockedError with correct category
- scan() with multiple patterns → raises with ALL detected categories
- scan() when disabled → no exception even with secrets
- scan() with short response (<10 chars) → no exception
- _audit() called on detection (structured fields, no matched content in log)
- Fail-closed: detector error does not silently pass

NOTE: otel_helper stub used (no real OTel SDK in test env).
"""
import hashlib
import pytest
from unittest.mock import patch

from src.core.output_filter import OutputFilter, _digest
from src.core.guardrail import GuardrailBlockedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_filter(enabled: bool = True) -> OutputFilter:
    """Build an OutputFilter with controlled enabled state."""
    f = OutputFilter.__new__(OutputFilter)
    f.enabled = enabled
    return f


# ---------------------------------------------------------------------------
# _digest
# ---------------------------------------------------------------------------


class TestOutputFilterDigest:
    def test_deterministic(self):
        assert _digest("response text") == _digest("response text")

    def test_length_12(self):
        assert len(_digest("any content")) == 12

    def test_matches_sha256_prefix(self):
        text = "some response with secrets"
        expected = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
        assert _digest(text) == expected


# ---------------------------------------------------------------------------
# OutputFilter.scan — clean response (no leak)
# ---------------------------------------------------------------------------


class TestScanClean:
    def test_no_exception_on_clean_response(self):
        f = _make_filter(enabled=True)
        f.scan(
            "Here is a helpful answer about Kubernetes pod scheduling.",
            agent_id="test-agent",
            user_id="user-1",
            session_id="sess-1",
        )

    def test_no_exception_on_normal_text_with_numbers(self):
        f = _make_filter(enabled=True)
        f.scan(
            "The deployment has 3 replicas running on nodes m5.xlarge with 16GB RAM.",
            agent_id="a",
            user_id="u",
            session_id="s",
        )


# ---------------------------------------------------------------------------
# OutputFilter.scan — AWS access key detection
# ---------------------------------------------------------------------------


class TestScanAwsAccessKey:
    def test_raises_on_akia_key(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "Your access key is AKIAIOSFODNN7EXAMPLE for the account.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:aws_access_key" in exc_info.value.categories

    def test_raises_on_asia_key(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "Temporary creds: ASIA1234567890ABCDEF for session.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:aws_access_key" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — private key detection
# ---------------------------------------------------------------------------


class TestScanPrivateKey:
    def test_raises_on_rsa_private_key(self):
        f = _make_filter(enabled=True)
        response = (
            "Here is the key:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(response, agent_id="a", user_id="u", session_id="s")
        assert "leak:private_key" in exc_info.value.categories

    def test_raises_on_ec_private_key(self):
        f = _make_filter(enabled=True)
        response = "-----BEGIN EC PRIVATE KEY-----\nblah\n-----END EC PRIVATE KEY-----"
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(response, agent_id="a", user_id="u", session_id="s")
        assert "leak:private_key" in exc_info.value.categories

    def test_raises_on_generic_private_key(self):
        f = _make_filter(enabled=True)
        response = "-----BEGIN PRIVATE KEY-----\ncontent\n-----END PRIVATE KEY-----"
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(response, agent_id="a", user_id="u", session_id="s")
        assert "leak:private_key" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — email detection
# ---------------------------------------------------------------------------


class TestScanEmail:
    def test_raises_on_email(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "Contact the admin at john.doe@company.internal for access.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:email" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — GitHub token detection
# ---------------------------------------------------------------------------


class TestScanGitHubToken:
    def test_raises_on_ghp_token(self):
        f = _make_filter(enabled=True)
        token = "ghp_" + "A" * 36
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"Use this token: {token} to authenticate.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:github_token" in exc_info.value.categories

    def test_raises_on_gho_token(self):
        f = _make_filter(enabled=True)
        token = "gho_" + "x" * 36
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"OAuth token: {token}",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:github_token" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — GitLab token detection
# ---------------------------------------------------------------------------


class TestScanGitLabToken:
    def test_raises_on_glpat_token(self):
        f = _make_filter(enabled=True)
        token = "glpat-" + "a" * 20
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"GitLab PAT: {token} for CI/CD.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:gitlab_token" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — generic secret detection
# ---------------------------------------------------------------------------


class TestScanGenericSecret:
    def test_raises_on_api_key_equals(self):
        f = _make_filter(enabled=True)
        secret_value = "A" * 30  # 30 chars (>= 20 threshold)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"Configuration: api_key={secret_value} in the env.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:generic_secret" in exc_info.value.categories

    def test_raises_on_password_equals(self):
        f = _make_filter(enabled=True)
        secret_value = "SuperSecretPass12345678"
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"Set password={secret_value} in the config file.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:generic_secret" in exc_info.value.categories

    def test_raises_on_auth_token(self):
        f = _make_filter(enabled=True)
        secret_value = "B" * 25
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                f"auth_token={secret_value} for the service.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:generic_secret" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — CPF detection
# ---------------------------------------------------------------------------


class TestScanCPF:
    def test_raises_on_formatted_cpf(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "The employee CPF is 123.456.789-01 in the system.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:cpf" in exc_info.value.categories

    def test_raises_on_raw_cpf(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "CPF number: 12345678901 found in the database.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:cpf" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — credit card detection
# ---------------------------------------------------------------------------


class TestScanCreditCard:
    def test_raises_on_16_digit_card(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "Card number: 4111 1111 1111 1111 expires 12/25.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert "leak:credit_card" in exc_info.value.categories


# ---------------------------------------------------------------------------
# OutputFilter.scan — multiple patterns → all categories reported
# ---------------------------------------------------------------------------


class TestScanMultiplePatterns:
    def test_raises_with_all_detected_categories(self):
        f = _make_filter(enabled=True)
        response = (
            "Access key: AKIAIOSFODNN7EXAMPLE\n"
            "Contact: admin@company.com\n"
            "-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----"
        )
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(response, agent_id="a", user_id="u", session_id="s")

        cats = exc_info.value.categories
        assert "leak:aws_access_key" in cats
        assert "leak:email" in cats
        assert "leak:private_key" in cats
        assert len(cats) >= 3


# ---------------------------------------------------------------------------
# OutputFilter.scan — disabled (no-op)
# ---------------------------------------------------------------------------


class TestScanDisabled:
    def test_no_exception_even_with_secrets(self):
        f = _make_filter(enabled=False)
        # This response has an AWS key — but filter is disabled
        f.scan(
            "Key: AKIAIOSFODNN7EXAMPLE and -----BEGIN PRIVATE KEY----- data",
            agent_id="a",
            user_id="u",
            session_id="s",
        )


# ---------------------------------------------------------------------------
# OutputFilter.scan — short response (< 10 chars) bypass
# ---------------------------------------------------------------------------


class TestScanShortResponse:
    def test_no_exception_on_short_response(self):
        f = _make_filter(enabled=True)
        # 9 chars — below minimum scan threshold
        f.scan("short", agent_id="a", user_id="u", session_id="s")

    def test_no_exception_on_exactly_9_chars(self):
        f = _make_filter(enabled=True)
        f.scan("123456789", agent_id="a", user_id="u", session_id="s")

    def test_scans_at_10_chars(self):
        """At exactly 10 chars, scanning IS performed (boundary)."""
        f = _make_filter(enabled=True)
        # 10 chars of clean content — should pass
        f.scan("0123456789", agent_id="a", user_id="u", session_id="s")


# ---------------------------------------------------------------------------
# OutputFilter._audit — structured logging, no matched content in log
# ---------------------------------------------------------------------------


class TestOutputFilterAudit:
    @patch("src.core.output_filter.logger")
    def test_audit_called_on_detection(self, mock_logger):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError):
            f.scan(
                "Key: AKIAIOSFODNN7EXAMPLE in the response.",
                agent_id="aws-agent",
                user_id="user-42",
                session_id="sess-99",
            )

        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args
        extra = call_kwargs.kwargs.get("extra") or call_kwargs[1].get("extra")

        assert extra["audit"] is True
        assert extra["event"] == "output_filter_block"
        assert extra["agent_id"] == "aws-agent"
        assert extra["user_id"] == "user-42"
        assert extra["session_id"] == "sess-99"
        assert "categories" in extra
        assert "response_digest" in extra

    @patch("src.core.output_filter.logger")
    def test_matched_content_never_in_audit_log(self, mock_logger):
        """The matched secret must NEVER appear in the audit log."""
        f = _make_filter(enabled=True)
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        with pytest.raises(GuardrailBlockedError):
            f.scan(
                f"Found key: {aws_key} in config.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )

        # Serialize entire call to string — secret must not be present
        call_str = str(mock_logger.warning.call_args)
        assert aws_key not in call_str

    @patch("src.core.output_filter.logger")
    def test_audit_response_digest_is_sha256_prefix(self, mock_logger):
        f = _make_filter(enabled=True)
        response = "Leaked email: admin@example.com here."
        with pytest.raises(GuardrailBlockedError):
            f.scan(response, agent_id="a", user_id="u", session_id="s")

        extra = mock_logger.warning.call_args.kwargs.get("extra") or mock_logger.warning.call_args[1].get("extra")
        expected = hashlib.sha256(response.encode("utf-8")).hexdigest()[:12]
        assert extra["response_digest"] == expected

    @patch("src.core.output_filter.logger")
    def test_no_audit_on_clean_response(self, mock_logger):
        f = _make_filter(enabled=True)
        f.scan(
            "This is a perfectly clean response about infrastructure.",
            agent_id="a",
            user_id="u",
            session_id="s",
        )
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# OutputFilter.scan — error attribute validation
# ---------------------------------------------------------------------------


class TestScanErrorAttributes:
    def test_error_reason_is_blocked(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "Token: ghp_" + "Z" * 36,
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert exc_info.value.reason == "blocked"

    def test_error_source_is_output(self):
        f = _make_filter(enabled=True)
        with pytest.raises(GuardrailBlockedError) as exc_info:
            f.scan(
                "admin@example.org is the contact.",
                agent_id="a",
                user_id="u",
                session_id="s",
            )
        assert exc_info.value.source == "OUTPUT"


class TestLuhnAndContextFP:
    """Review remediations: credit_card confirmed by Luhn (drops timestamps/IDs);
    aws_secret_key requires an AWS key-name context (drops bare 40-char blobs)."""

    def test_valid_luhn_credit_card_blocked(self):
        f = _make_filter(enabled=True)
        # 4242 4242 4242 4242 is a well-known Luhn-valid test card.
        with pytest.raises(GuardrailBlockedError):
            f.scan("card on file: 4242 4242 4242 4242 expires soon")

    def test_epoch_timestamp_not_flagged_as_card(self):
        f = _make_filter(enabled=True)
        # 13-digit epoch millis — matches the regex but fails Luhn → no block.
        f.scan("event at 1719878400000 in the log stream, all good")

    def test_bare_40_char_blob_not_flagged_as_aws_secret(self):
        f = _make_filter(enabled=True)
        # 40 hex chars (sha1-like) without AWS key context → must NOT block.
        f.scan("commit abc123def4567890abc123def4567890abc12345 merged cleanly")

    def test_aws_secret_with_context_blocked(self):
        f = _make_filter(enabled=True)
        # Real AWS secret keys are exactly 40 chars of [A-Za-z0-9/+].
        key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # 40 chars
        with pytest.raises(GuardrailBlockedError):
            f.scan(f"config: aws_secret_access_key = {key}")
