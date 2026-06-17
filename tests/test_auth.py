"""Tests for src/core/auth.py"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.core.auth as auth_module


def test_require_token_valid():
    with patch.object(auth_module, "_EXPECTED_TOKEN", "secret123"):
        # Should not raise
        auth_module.require_token(x_internal_token="secret123")


def test_require_token_invalid():
    with patch.object(auth_module, "_EXPECTED_TOKEN", "secret123"):
        with pytest.raises(HTTPException) as exc_info:
            auth_module.require_token(x_internal_token="wrong")
        assert exc_info.value.status_code == 401


def test_require_token_empty_env():
    with patch.object(auth_module, "_EXPECTED_TOKEN", ""):
        with pytest.raises(HTTPException) as exc_info:
            auth_module.require_token(x_internal_token="anything")
        assert exc_info.value.status_code == 401
