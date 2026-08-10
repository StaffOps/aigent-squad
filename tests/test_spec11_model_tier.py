"""Tests for spec 11: Model tiering, prompt caching, token budget, cost metrics.

Written against the BEHAVIOR CONTRACT, not implementation details.
Bedrock is fully mocked — no AWS calls.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# T1: Model Resolver (src/core/model_tier.py)
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveModel:
    """resolve_model maps logical roles to config-driven model IDs."""

    def test_classifier_resolves_to_haiku_by_default(self):
        from src.core.model_tier import resolve_model
        result = resolve_model("classifier")
        assert "haiku" in result.lower()

    def test_agent_resolves_to_sonnet_by_default(self):
        from src.core.model_tier import resolve_model
        result = resolve_model("agent")
        assert "sonnet" in result.lower()

    def test_synthesis_resolves_to_sonnet_by_default(self):
        from src.core.model_tier import resolve_model
        result = resolve_model("synthesis")
        assert "sonnet" in result.lower()

    def test_unknown_role_falls_back_to_agent_model(self):
        from src.core.model_tier import resolve_model
        result = resolve_model("nonexistent_role")
        agent_model = resolve_model("agent")
        assert result == agent_model

    def test_role_comes_from_config_not_hardcoded(self, monkeypatch):
        """Changing the setting changes the resolved model."""
        custom_model = "us.anthropic.claude-opus-4-20250514-v1:0"
        monkeypatch.setattr("src.core.model_tier.settings.bedrock_classifier_model_id", custom_model)
        from src.core.model_tier import resolve_model
        assert resolve_model("classifier") == custom_model

    def test_agent_model_configurable(self, monkeypatch):
        custom_model = "custom-agent-model-id"
        monkeypatch.setattr("src.core.model_tier.settings.bedrock_agent_model_id", custom_model)
        from src.core.model_tier import resolve_model
        assert resolve_model("agent") == custom_model

    def test_synthesis_model_configurable(self, monkeypatch):
        custom_model = "custom-synthesis-model-id"
        monkeypatch.setattr("src.core.model_tier.settings.bedrock_synthesis_model_id", custom_model)
        from src.core.model_tier import resolve_model
        assert resolve_model("synthesis") == custom_model


class TestGetPricing:
    """get_pricing extracts family from model ID and returns correct rates."""

    def test_haiku_pricing(self):
        from src.core.model_tier import get_pricing
        p = get_pricing("us.anthropic.claude-haiku-4-5-20251001-v1:0")
        assert p.input_per_1m == 1.00
        assert p.output_per_1m == 5.00
        assert p.cache_read_per_1m == 0.10

    def test_sonnet_pricing(self):
        from src.core.model_tier import get_pricing
        p = get_pricing("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        assert p.input_per_1m == 3.00
        assert p.output_per_1m == 15.00
        assert p.cache_read_per_1m == 0.30

    def test_opus_pricing(self):
        from src.core.model_tier import get_pricing
        p = get_pricing("us.anthropic.claude-opus-4-5-20251101-v1:0")
        assert p.input_per_1m == 5.00
        assert p.output_per_1m == 25.00
        assert p.cache_read_per_1m == 0.50

    def test_unknown_family_defaults_to_sonnet(self):
        from src.core.model_tier import get_pricing
        p = get_pricing("some-unknown-model-xyz")
        sonnet_p = get_pricing("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        assert p == sonnet_p

    def test_case_insensitive_family_detection(self):
        from src.core.model_tier import get_pricing
        p = get_pricing("US.ANTHROPIC.CLAUDE-HAIKU-4-v1:0")
        assert p.input_per_1m == 1.00


class TestComputeCost:
    """compute_cost calculates USD cost accounting for cached tokens."""

    def test_basic_cost_no_cache(self):
        from src.core.model_tier import compute_cost
        # 1000 input * 3.00/1M + 500 output * 15.00/1M
        cost = compute_cost("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 1000, 500, 0)
        expected = (1000 * 3.00 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_cost_with_cache_read_tokens(self):
        from src.core.model_tier import compute_cost
        # 1000 input, 200 cached → 800 full-rate + 200 cache-rate
        cost = compute_cost("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 1000, 500, 200)
        expected = (800 * 3.00 + 200 * 0.30 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_cost_with_cache_write_tokens(self):
        from src.core.model_tier import compute_cost
        # 1000 input, 300 cache-write → 700 full-rate + 300 at 1.25x base premium
        cost = compute_cost(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0", 1000, 500,
            cache_read_tokens=0, cache_write_tokens=300,
        )
        expected = (700 * 3.00 + 300 * 3.00 * 1.25 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_cost_all_cached(self):
        from src.core.model_tier import compute_cost
        # All input tokens cached
        cost = compute_cost("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 1000, 500, 1000)
        expected = (0 * 3.00 + 1000 * 0.30 + 500 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_cost_cache_exceeds_input_clamps_to_zero(self):
        from src.core.model_tier import compute_cost
        # cache_read > input → non-cached is 0 (not negative)
        cost = compute_cost("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 100, 50, 200)
        expected = (0 * 3.00 + 200 * 0.30 + 50 * 15.00) / 1_000_000
        assert abs(cost - expected) < 1e-10


class TestEstimateTokens:
    """estimate_tokens: len(text)//4, min 1, empty→0."""

    def test_empty_string_returns_zero(self):
        from src.core.model_tier import estimate_tokens
        assert estimate_tokens("") == 0

    def test_short_text_returns_minimum_one(self):
        from src.core.model_tier import estimate_tokens
        assert estimate_tokens("hi") == 1  # 2 chars // 4 = 0, min 1

    def test_normal_text(self):
        from src.core.model_tier import estimate_tokens
        text = "a" * 100
        assert estimate_tokens(text) == 25  # 100 // 4

    def test_single_char_returns_one(self):
        from src.core.model_tier import estimate_tokens
        assert estimate_tokens("x") == 1
