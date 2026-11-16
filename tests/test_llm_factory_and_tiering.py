"""Spec 28 — provider selection, model resolution, startup validation, L1 auto-off."""
from __future__ import annotations

import pytest

import src.core.llm as llm_pkg
import src.core.llm.factory as factory
from src.core.config import settings
from src.core.guardrail import active_for_provider
from src.core.model_tier import (
    compute_cost,
    get_pricing,
    resolve_model,
    resolve_model_for_tier,
    validate_tier_models_at_startup,
)


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_model_id", "big")
    monkeypatch.setattr(settings, "llm_classifier_model_id", None)
    monkeypatch.setattr(settings, "llm_synthesis_model_id", None)
    monkeypatch.setattr(settings, "openai_base_url", "https://llm.test/v1")
    monkeypatch.setattr(settings, "openai_api_key", "k")
    monkeypatch.setattr(settings, "openai_api_key_file", None)
    monkeypatch.setattr(settings, "llm_price_input_per_1m", None)
    monkeypatch.setattr(settings, "llm_price_output_per_1m", None)
    monkeypatch.setattr(factory, "_l1_metric_published", False)


# ── Selection ───────────────────────────────────────────────────────────────

def test_default_provider_is_bedrock_singleton():
    from src.core.bedrock import bedrock
    assert settings.llm_provider == "bedrock"
    assert factory.build_llm_service() is bedrock


def test_openai_selected(openai_env):
    from src.core.llm.openai_provider import OpenAICompatClient
    assert isinstance(factory.build_llm_service(), OpenAICompatClient)


def test_package_lazy_llm(monkeypatch):
    monkeypatch.setattr(llm_pkg, "_llm", None)
    first = llm_pkg.llm
    assert llm_pkg.get_llm() is first
    with pytest.raises(AttributeError):
        llm_pkg.nope  # noqa: B018


def test_invalid_provider_rejected():
    from pydantic import ValidationError
    from src.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(redis_host="x", llm_provider="gemini")


# ── Model resolution ───────────────────────────────────────────────────────

def test_bedrock_neutral_vars_override_with_fallback(monkeypatch):
    assert resolve_model("classifier") == settings.bedrock_classifier_model_id
    monkeypatch.setattr(settings, "llm_model_id", "us.anthropic.claude-sonnet-x")
    monkeypatch.setattr(settings, "llm_classifier_model_id", "us.anthropic.claude-haiku-x")
    assert resolve_model("agent") == "us.anthropic.claude-sonnet-x"
    assert resolve_model("classifier") == "us.anthropic.claude-haiku-x"
    assert resolve_model("synthesis") == settings.bedrock_synthesis_model_id
    assert resolve_model("weird") == "us.anthropic.claude-sonnet-x"


def test_openai_role_and_tier_models(openai_env, monkeypatch):
    assert resolve_model("classifier") == "big"
    assert resolve_model("synthesis") == "big"
    monkeypatch.setattr(settings, "llm_classifier_model_id", "small")
    monkeypatch.setattr(settings, "llm_synthesis_model_id", "huge")
    assert resolve_model("classifier") == "small"
    assert resolve_model("synthesis") == "huge"
    assert resolve_model("unknown-role") == "big"
    assert resolve_model_for_tier("fast") == "small"
    assert resolve_model_for_tier("standard") == "big"
    assert resolve_model_for_tier("deep") == "big"


# ── Pricing ─────────────────────────────────────────────────────────────────

def test_openai_pricing_configured_or_zero(openai_env, monkeypatch):
    assert get_pricing("anything").input_per_1m == 0.0
    assert compute_cost("big", 1000, 1000) == 0.0
    monkeypatch.setattr(settings, "llm_price_input_per_1m", 1.0)
    assert compute_cost("big", 1000, 1000) == 0.0  # only one half set → still zero
    monkeypatch.setattr(settings, "llm_price_output_per_1m", 3.0)
    assert compute_cost("big", 1_000_000, 1_000_000) == pytest.approx(4.0)


def test_bedrock_pricing_unchanged():
    assert get_pricing("us.anthropic.claude-haiku-4-5").input_per_1m == 1.00


# ── Startup validation ─────────────────────────────────────────────────────

def test_bedrock_validation_unchanged_and_l1_active(monkeypatch):
    from src.core.guardrail import guardrail
    monkeypatch.setattr(guardrail, "enabled", True)  # conftest disables it by default
    validate_tier_models_at_startup()  # defaults are valid
    assert factory.guardrail_l1_active() is True
    status = factory.provider_status()
    assert status["provider"] == "bedrock" and status["guardrail_l1"] == "active" and status["ok"] is True


def test_openai_valid_config_warns_l1_inactive_and_pricing(openai_env, monkeypatch):
    from unittest.mock import MagicMock
    metric, log = MagicMock(), MagicMock()
    monkeypatch.setattr(factory, "llm_guardrail_l1_active", metric)
    monkeypatch.setattr(factory, "logger", log)
    monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", "")  # bedrock-only check skipped
    validate_tier_models_at_startup()
    validate_tier_models_at_startup()  # idempotent metric publication
    text = " ".join(str(c.args[0]) for c in log.warning.call_args_list)
    assert "llm_guardrail_l1_inactive" in text
    assert "llm_pricing_unset" in text
    metric.add.assert_called_once_with(0, {"provider": "openai"})
    status = factory.provider_status()
    assert status["guardrail_l1"] == "inactive" and status["ok"] is True


@pytest.mark.parametrize("field,value,needle", [
    ("openai_base_url", None, "OPENAI_BASE_URL"),
    ("openai_base_url", "ftp://x", "OPENAI_BASE_URL"),
    ("openai_api_key", "", "OPENAI_API_KEY"),
    ("llm_model_id", None, "LLM_MODEL_ID"),
])
def test_openai_misconfig_fails_loud(openai_env, monkeypatch, field, value, needle):
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(RuntimeError, match=needle):
        validate_tier_models_at_startup()


def test_openai_unreadable_key_file_fails_loud(openai_env, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "openai_api_key_file", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="not readable"):
        validate_tier_models_at_startup()


def test_openai_pricing_set_no_pricing_warning(openai_env, monkeypatch):
    from unittest.mock import MagicMock
    log = MagicMock()
    monkeypatch.setattr(factory, "logger", log)
    monkeypatch.setattr(settings, "llm_price_input_per_1m", 1.0)
    monkeypatch.setattr(settings, "llm_price_output_per_1m", 2.0)
    validate_tier_models_at_startup()
    assert all("llm_pricing_unset" not in str(c.args[0]) for c in log.warning.call_args_list)


# ── L1 rule ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("enabled,provider,expected", [
    (True, "bedrock", True), (False, "bedrock", False), (True, "openai", False), (False, "openai", False),
])
def test_active_for_provider(enabled, provider, expected):
    assert active_for_provider(enabled, provider) is expected


def test_l1_status_reports_enforced_flag_not_settings(monkeypatch):
    """Reviewer fix 3: the reported state is the live guardrail.enabled flag."""
    from src.core.guardrail import guardrail
    monkeypatch.setattr(settings, "guardrail_enabled", True)
    monkeypatch.setattr(guardrail, "enabled", False)
    assert factory.guardrail_l1_active() is False
    assert factory.provider_status()["guardrail_l1"] == "inactive"


@pytest.mark.parametrize("url,ok", [
    ("http://llama.llm.svc:8080/v1", True),
    ("http://llm.ns.svc.cluster.local/v1", True),
    ("http://localhost:11434/v1", True),
    ("http://127.0.0.1:8000/v1", True),
    ("http://[::1]:8000/v1", True),
    ("http://ollama:11434/v1", True),
    ("https://api.openai.com/v1", True),
    ("http://api.openai.com/v1", False),
    ("http://10.0.0.5:8000/v1", False),
    ("http://llm.svc.evil.com/v1", False),
])
def test_plain_http_only_to_local_hosts(openai_env, monkeypatch, url, ok):
    monkeypatch.setattr(settings, "openai_base_url", url)
    if ok:
        validate_tier_models_at_startup()
    else:
        with pytest.raises(RuntimeError, match="plain http"):
            validate_tier_models_at_startup()
