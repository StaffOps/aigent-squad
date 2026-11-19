"""Provider selection + startup validation (spec 28, Decisions 7/8/10).

``LLM_PROVIDER`` is global: every LLM call in the process goes through the one
service ``build_llm_service()`` returns. With the default (``bedrock``) that is
the existing ``src.core.bedrock.bedrock`` singleton, so behavior is unchanged.
"""
from __future__ import annotations

from typing import Any, Dict

from src.core.config import settings
import src.core.guardrail as _guardrail_mod
from src.core.llm.service import LLMService
from src.core.logger import logger
from src.core.metrics import llm_guardrail_l1_active
from src.core.model_tier import configured_llm_pricing

_l1_metric_published = False


def build_llm_service() -> LLMService:
    """Return the service for the configured ``LLM_PROVIDER``."""
    if settings.llm_provider == "openai":
        from src.core.llm.openai_provider import OpenAICompatClient

        return OpenAICompatClient()
    from src.core.bedrock import bedrock

    return bedrock


def guardrail_l1_active() -> bool:
    """True when the Bedrock Guardrail (spec 14 L1) is evaluating requests.

    Reports what is actually ENFORCED — the live ``guardrail.enabled`` flag of
    client the LLM service actually calls — not a re-derivation from settings.
    Resolved at call time via the module attribute (not a name bound at
    import), so the signal can never drift from the enforcing instance.
    """
    return bool(_guardrail_mod.guardrail.enabled)


def provider_status() -> Dict[str, Any]:
    """Informational /ready detail: which provider, and whether L1 is active."""
    l1 = guardrail_l1_active()
    return {
        "ok": True,  # informational — never fails readiness
        "provider": settings.llm_provider,
        "guardrail_l1": "active" if l1 else "inactive",
        "detail": f"llm provider={settings.llm_provider}, guardrail L1 {'active' if l1 else 'inactive'}",
    }


def _openai_config_errors() -> list[str]:
    from src.core.llm.openai_provider import load_api_key, plaintext_host_allowed

    errors: list[str] = []
    base_url = (settings.openai_base_url or "").strip()
    if not base_url.startswith(("http://", "https://")):
        errors.append("OPENAI_BASE_URL must be set to an http(s) URL")
    elif not plaintext_host_allowed(base_url):
        # The API key travels in a header: never in cleartext off-cluster.
        errors.append(
            "OPENAI_BASE_URL uses plain http:// to a non-local host — use https:// "
            "(plain http is allowed only for localhost/127.0.0.1/::1, *.svc, "
            "*.svc.cluster.local and single-label hosts)"
        )
    try:
        if not load_api_key():
            errors.append("OPENAI_API_KEY or OPENAI_API_KEY_FILE must be set "
                          "(any non-empty value for keyless local servers)")
    except RuntimeError as e:
        errors.append(str(e))
    if not (settings.llm_model_id or "").strip():
        errors.append("LLM_MODEL_ID must be set (the model name the endpoint serves)")
    return errors


def validate_llm_provider() -> None:
    """Fail loudly on provider misconfiguration; announce L1 state. Idempotent.

    Called from ``validate_tier_models_at_startup`` (supervisor lifespan).
    Under ``openai``: refuses to boot without base URL, key and model id;
    WARNs when pricing is unset (cost metrics read 0) and — always — that the
    Bedrock Guardrail (L1) is inactive (spec 28 Decision 10, ADR-0009).
    """
    global _l1_metric_published

    if settings.llm_provider == "openai":
        errors = _openai_config_errors()
        if errors:
            raise RuntimeError("LLM_PROVIDER=openai misconfigured — cannot start: " + "; ".join(errors))
        if configured_llm_pricing() is None:
            logger.warning(
                "llm_pricing_unset — LLM_PRICE_INPUT_PER_1M/LLM_PRICE_OUTPUT_PER_1M not both set; "
                "aigent.cost.estimated will read 0 for this provider",
                extra={"provider": settings.llm_provider},
            )

    l1 = guardrail_l1_active()
    if not l1 and settings.llm_provider != "bedrock":
        logger.warning(
            "llm_guardrail_l1_inactive — Bedrock Guardrail (spec 14 L1) is OFF because "
            "LLM_PROVIDER is not bedrock. Deliberate exception to fail-closed security "
            "(AGENTS.md invariant 6, ADR-0009); L2-L6 remain active.",
            extra={"provider": settings.llm_provider, "audit": True},
        )

    if not _l1_metric_published:
        llm_guardrail_l1_active.add(1 if l1 else 0, {"provider": settings.llm_provider})
        _l1_metric_published = True
