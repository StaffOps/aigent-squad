"""Model tiering — config-driven role→model resolution and per-model pricing.

Spec 11 (bedrock-resilience-cost): each logical role (classifier, agent,
synthesis) maps to a model ID from settings. Pricing is per-model so cost
metrics are accurate (vs the previous hardcoded Sonnet price).

The model resolver is pure (no side effects) and must never fallback silently to
a different tier — misconfiguration must fail loudly at startup, not mask cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from src.core.config import settings


@dataclass(frozen=True)
class ModelPricing:
    """Per-1M-token pricing in USD (input / output / cache_read).

    cache_read is the discounted input rate for prompt-cache hits (~90% off).
    """
    input_per_1m: float
    output_per_1m: float
    cache_read_per_1m: float


# Known pricing table.  Keyed by the short model family name extracted from the
# inference profile ID (see ``_model_family``). Bounded cardinality: only models
# we actually use are listed. Prices are per-1M-tokens (Bedrock, us-east-1,
# Claude 4.x family — verify on the AWS pricing page when adding models).
MODEL_PRICING: Dict[str, ModelPricing] = {
    "haiku": ModelPricing(input_per_1m=1.00, output_per_1m=5.00, cache_read_per_1m=0.10),
    "sonnet": ModelPricing(input_per_1m=3.00, output_per_1m=15.00, cache_read_per_1m=0.30),
    "opus": ModelPricing(input_per_1m=15.00, output_per_1m=75.00, cache_read_per_1m=1.50),
}

# Prompt-cache WRITE tokens cost more than base input (Anthropic: 1.25x for the
# 5-minute ephemeral cache). Cache READ hits cost ~0.1x (the cache_read rate).
_CACHE_WRITE_MULTIPLIER = 1.25

# Default (fallback) — Sonnet pricing.  Applied when the model family cannot be
# determined from the profile ID. Never silently cheaper (safe: overestimates).
_DEFAULT_PRICING = MODEL_PRICING["sonnet"]


def _model_family(model_id: str) -> str:
    """Extract the short family name from an inference-profile ID.

    Examples:
        "us.anthropic.claude-haiku-4-5-20251001-v1:0" → "haiku"
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0" → "sonnet"
        "us.anthropic.claude-opus-4-20250514-v1:0" → "opus"
    """
    lower = model_id.lower()
    for family in ("haiku", "sonnet", "opus"):
        if family in lower:
            return family
    return "unknown"


def resolve_model(role: str) -> str:
    """Resolve a logical role to a concrete Bedrock model ID from settings.

    Roles:
        - "classifier" → settings.bedrock_classifier_model_id
        - "agent"      → settings.bedrock_agent_model_id
        - "synthesis"  → settings.bedrock_synthesis_model_id

    Unknown roles default to the agent model (safe: Sonnet, the mid-tier).
    """
    role_map = {
        "classifier": settings.bedrock_classifier_model_id,
        "agent": settings.bedrock_agent_model_id,
        "synthesis": settings.bedrock_synthesis_model_id,
    }
    return role_map.get(role, settings.bedrock_agent_model_id)


# ── Model-tier PRE-ROUTING (spec 38 Phase 1) ────────────────────────────────


def resolve_model_for_tier(tier: str) -> str:
    """Resolve a complexity tier to a concrete Bedrock model ID.

    Tiers:
        - "fast"     → bedrock_tier_fast_model_id (Haiku)
        - "standard" → bedrock_tier_standard_model_id (Sonnet)
        - "deep"     → bedrock_tier_deep_model_id (Opus), BUT falls back to
                       standard when AIGENT_TIER_DEEP_ENABLED is false.

    Unknown tiers resolve to standard (safe: Sonnet, the mid-tier).
    """
    if tier == "fast":
        return settings.bedrock_tier_fast_model_id
    if tier == "deep":
        # Phase 1 rollout: deep disabled by default → falls back to standard.
        if not settings.aigent_tier_deep_enabled:
            return settings.bedrock_tier_standard_model_id
        return settings.bedrock_tier_deep_model_id
    # "standard" or anything unknown → standard
    return settings.bedrock_tier_standard_model_id


def validate_tier_models_at_startup() -> None:
    """Fail loud at startup if any tier model ID is empty or unrecognizable.

    HC5: startup validation — no silent fallback. A misconfigured tier must
    crash the process before serving traffic.
    """
    tier_ids = {
        "fast": settings.bedrock_tier_fast_model_id,
        "standard": settings.bedrock_tier_standard_model_id,
        "deep": settings.bedrock_tier_deep_model_id,
    }
    for tier_name, model_id in tier_ids.items():
        if not model_id or not model_id.strip():
            raise RuntimeError(
                f"BEDROCK_TIER_{tier_name.upper()}_MODEL_ID is empty — "
                f"cannot start. Set a valid inference-profile ID."
            )
        family = _model_family(model_id)
        if family == "unknown":
            raise RuntimeError(
                f"BEDROCK_TIER_{tier_name.upper()}_MODEL_ID='{model_id}' — "
                f"unrecognized model family (expected haiku/sonnet/opus in the ID). "
                f"Fix the env var or update MODEL_PRICING for the new family."
            )


def get_pricing(model_id: str) -> ModelPricing:
    """Return pricing for a model ID (by family extraction)."""
    family = _model_family(model_id)
    return MODEL_PRICING.get(family, _DEFAULT_PRICING)


def compute_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Compute estimated cost in USD for a single Bedrock call.

    Token classes are priced separately:
      - cache READ hits  → discounted ``cache_read_per_1m``
      - cache WRITE      → base input × 1.25 (ephemeral-cache write premium)
      - remaining input  → base input rate
      - output           → output rate
    """
    pricing = get_pricing(model_id)
    non_cached_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    cost = (
        non_cached_input * pricing.input_per_1m
        + cache_read_tokens * pricing.cache_read_per_1m
        + cache_write_tokens * pricing.input_per_1m * _CACHE_WRITE_MULTIPLIER
        + output_tokens * pricing.output_per_1m
    ) / 1_000_000
    return cost


# ── Token estimation (for budget + history truncation) ───────────────────────

# Anthropic tokenizer approximation: ~4 chars per token (conservative; real is
# closer to 3.5 for English). Using 4 ensures we over-estimate rather than
# under-estimate token counts when the real tokenizer is unavailable.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length (conservative approximation).

    The Anthropic tokenizer is not publicly available as a standalone lib.
    Claude's actual BPE averages ~3.5 chars/token for English; we use 4 to
    slightly overestimate (safer for budget enforcement).
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)
