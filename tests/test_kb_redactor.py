"""Tests for src.core.kb.redactor — PII/secret stripping."""
from src.core.kb.redactor import redact


def test_redact_email():
    result = redact("contact me at user@example.com")
    assert "<redacted:email>" in result
    assert "user@example.com" not in result


def test_redact_aws_key():
    result = redact("key is AKIAIOSFODNN7EXAMPLE")
    assert "<redacted:aws-key>" in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_redact_github_pat():
    result = redact("token: ghp_1234567890abcdefghij1234567890abcdef")
    assert "<redacted:github-pat>" in result
    assert "ghp_" not in result


def test_redact_bearer_token():
    result = redact("Authorization: Bearer abc.def.ghi")
    assert "Bearer <redacted:token>" in result
    assert "abc.def.ghi" not in result


def test_redact_empty_input():
    assert redact("") == ""
    assert redact(None) is None
