"""Independent tests for spec 38 Phase 1 — Model-tier PRE-ROUTING (option A).

Written against the DESIGN CONTRACT (HC1-HC6 + option A), NOT mirroring
the implementation. Tests exercise the public API/behaviour boundaries:

1. Tier-selection matrix (complexity × confidence → tier → model_id)
2. Routing-disabled → always standard
3. Startup validation fails loud on empty/invalid tier IDs
4. Classifier complexity parsing + heuristic fallback
5. resolve_model_for_tier mapping (including deep fallback)
6. _resolve_tier_model dispatch function

These tests stub private deps (otel_helper) and run via:
    docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
      'pip install -q -r .local-stubs/requirements.no-otel.txt && \
       PYTHONPATH=.local-stubs:. pytest tests/test_spec38_tier_routing_independent.py \
       --cov=src.core.model_tier --cov=src.core.classifier --cov=src.supervisor.agent \
       --cov-report=term-missing -v'
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures — isolated settings override (no env leakage between tests)
# ══════════════════════════════════════════════════════════════════════════════

FAST_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
STANDARD_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEEP_MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"
HIGH_CONFIDENCE = 0.85


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Reset tier settings to defaults before each test."""
    from src.core.config import settings
    monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", FAST_MODEL)
    monkeypatch.setattr(settings, "bedrock_tier_standard_model_id", STANDARD_MODEL)
    monkeypatch.setattr(settings, "bedrock_tier_deep_model_id", DEEP_MODEL)
    monkeypatch.setattr(settings, "aigent_tier_routing_enabled", True)
    monkeypatch.setattr(settings, "aigent_tier_deep_enabled", False)  # Phase 1 default
    monkeypatch.setattr(settings, "aigent_tier_confidence_high", HIGH_CONFIDENCE)


# ══════════════════════════════════════════════════════════════════════════════
# Section 1: resolve_model_for_tier — pure tier→model_id mapping
# ══════════════════════════════════════════════════════════════════════════════


class TestResolveModelForTier:
    """HC2: tier → model_id mapping; HC4/option A: deep disabled → standard."""

    def test_fast_returns_haiku(self):
        from src.core.model_tier import resolve_model_for_tier
        assert resolve_model_for_tier("fast") == FAST_MODEL

    def test_standard_returns_sonnet(self):
        from src.core.model_tier import resolve_model_for_tier
        assert resolve_model_for_tier("standard") == STANDARD_MODEL

    def test_deep_with_deep_disabled_returns_standard(self):
        """Option A: deep disabled (Phase 1 default) → falls back to standard."""
        from src.core.model_tier import resolve_model_for_tier
        # deep_enabled is False by default in _reset_settings
        assert resolve_model_for_tier("deep") == STANDARD_MODEL

    def test_deep_with_deep_enabled_returns_opus(self, monkeypatch):
        """When deep IS enabled, it returns the Opus model."""
        from src.core.config import settings
        from src.core.model_tier import resolve_model_for_tier
        monkeypatch.setattr(settings, "aigent_tier_deep_enabled", True)
        assert resolve_model_for_tier("deep") == DEEP_MODEL

    def test_unknown_tier_returns_standard(self):
        """Any unknown tier value defaults to standard (safe: Sonnet)."""
        from src.core.model_tier import resolve_model_for_tier
        assert resolve_model_for_tier("garbage") == STANDARD_MODEL
        assert resolve_model_for_tier("") == STANDARD_MODEL

    def test_respects_custom_model_ids(self, monkeypatch):
        """Tier resolution uses the configured model IDs, not hardcoded values."""
        from src.core.config import settings
        from src.core.model_tier import resolve_model_for_tier
        custom = "us.anthropic.claude-haiku-custom-v1:0"
        monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", custom)
        assert resolve_model_for_tier("fast") == custom


# ══════════════════════════════════════════════════════════════════════════════
# Section 2: validate_tier_models_at_startup — HC5: fail loud
# ══════════════════════════════════════════════════════════════════════════════


class TestStartupValidation:
    """HC5: startup validation must crash on empty or unrecognized model IDs."""

    def test_valid_defaults_pass(self):
        """Default model IDs pass validation without error."""
        from src.core.model_tier import validate_tier_models_at_startup
        validate_tier_models_at_startup()  # should not raise

    def test_empty_fast_id_raises(self, monkeypatch):
        from src.core.config import settings
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "bedrock_tier_fast_model_id", "")
        with pytest.raises(RuntimeError, match="FAST.*empty"):
            validate_tier_models_at_startup()

    def test_empty_standard_id_raises(self, monkeypatch):
        from src.core.config import settings
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "bedrock_tier_standard_model_id", "   ")
        with pytest.raises(RuntimeError, match="STANDARD.*empty"):
            validate_tier_models_at_startup()

    def test_empty_deep_id_raises(self, monkeypatch):
        from src.core.config import settings
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "bedrock_tier_deep_model_id", "")
        with pytest.raises(RuntimeError, match="DEEP.*empty"):
            validate_tier_models_at_startup()

    def test_unrecognized_family_raises(self, monkeypatch):
        """A model ID that doesn't contain haiku/sonnet/opus is rejected."""
        from src.core.config import settings
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(
            settings, "bedrock_tier_fast_model_id",
            "us.anthropic.claude-mystery-v1:0"
        )
        with pytest.raises(RuntimeError, match="unrecognized model family"):
            validate_tier_models_at_startup()

    def test_whitespace_only_is_empty(self, monkeypatch):
        """Whitespace-only strings count as empty."""
        from src.core.config import settings
        from src.core.model_tier import validate_tier_models_at_startup
        monkeypatch.setattr(settings, "bedrock_tier_deep_model_id", "  \t  ")
        with pytest.raises(RuntimeError, match="DEEP.*empty"):
            validate_tier_models_at_startup()


# ══════════════════════════════════════════════════════════════════════════════
# Section 3: _resolve_tier_model — dispatch (complexity, confidence) → tier
# ══════════════════════════════════════════════════════════════════════════════


def _make_classification(complexity: str = "standard", confidence: float = 0.9):
    """Build a ClassifierResult with the given complexity/confidence."""
    from src.core.classifier import ClassifierResult, AgentMatch
    return ClassifierResult(
        agents=[AgentMatch(agent="observability", confidence=confidence)],
        complexity=complexity,
    )


class TestResolveTierModel:
    """HC2 dispatch matrix + HC1 (no escalation) + option A (deep disabled)."""

    def test_simple_high_confidence_returns_fast(self):
        """simple + confidence >= HIGH → fast (Haiku)."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("simple", 0.95)
        model_id = _resolve_tier_model(cr)
        assert model_id == FAST_MODEL

    def test_simple_exactly_at_threshold_returns_fast(self):
        """simple + confidence == HIGH (boundary) → fast."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("simple", HIGH_CONFIDENCE)
        model_id = _resolve_tier_model(cr)
        assert model_id == FAST_MODEL

    def test_simple_below_threshold_returns_deep_fallback(self):
        """simple + confidence < HIGH → deep tier (but deep disabled → standard)."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("simple", 0.6)
        model_id = _resolve_tier_model(cr)
        # deep disabled → falls back to standard
        assert model_id == STANDARD_MODEL

    def test_complex_returns_deep_fallback_to_standard(self):
        """complex → deep tier, but deep disabled (Phase 1) → standard."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("complex", 0.95)
        model_id = _resolve_tier_model(cr)
        assert model_id == STANDARD_MODEL  # deep fallback

    def test_complex_with_deep_enabled_returns_opus(self, monkeypatch):
        """complex + deep enabled → Opus."""
        from src.core.config import settings
        from src.supervisor.agent import _resolve_tier_model
        monkeypatch.setattr(settings, "aigent_tier_deep_enabled", True)
        cr = _make_classification("complex", 0.95)
        model_id = _resolve_tier_model(cr)
        assert model_id == DEEP_MODEL

    def test_standard_complexity_high_confidence_returns_standard(self):
        """standard complexity + high confidence → standard (Sonnet)."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("standard", 0.92)
        model_id = _resolve_tier_model(cr)
        assert model_id == STANDARD_MODEL

    def test_standard_complexity_low_confidence_returns_deep_fallback(self):
        """standard complexity + low confidence → deep (falls back to standard)."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("standard", 0.5)
        model_id = _resolve_tier_model(cr)
        assert model_id == STANDARD_MODEL  # deep fallback

    def test_low_confidence_with_deep_enabled_returns_opus(self, monkeypatch):
        """Low confidence + deep enabled → Opus (the harder model)."""
        from src.core.config import settings
        from src.supervisor.agent import _resolve_tier_model
        monkeypatch.setattr(settings, "aigent_tier_deep_enabled", True)
        cr = _make_classification("standard", 0.5)
        model_id = _resolve_tier_model(cr)
        assert model_id == DEEP_MODEL

    def test_routing_disabled_returns_none(self, monkeypatch):
        """When aigent_tier_routing_enabled=false, always returns None
        (meaning: use default role-based resolution, i.e. standard Sonnet)."""
        from src.core.config import settings
        from src.supervisor.agent import _resolve_tier_model
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", False)
        cr = _make_classification("simple", 0.99)
        assert _resolve_tier_model(cr) is None

    def test_routing_disabled_complex_also_none(self, monkeypatch):
        """Routing disabled → None even for complex queries."""
        from src.core.config import settings
        from src.supervisor.agent import _resolve_tier_model
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", False)
        cr = _make_classification("complex", 0.99)
        assert _resolve_tier_model(cr) is None

    def test_no_agents_in_classification_uses_zero_confidence(self):
        """When classification has no agents, confidence=0 → goes to deep path."""
        from src.core.classifier import ClassifierResult
        from src.supervisor.agent import _resolve_tier_model
        cr = ClassifierResult(agents=[], complexity="standard")
        # confidence property returns 0.0 (< HIGH) → deep → standard (deep disabled)
        model_id = _resolve_tier_model(cr)
        assert model_id == STANDARD_MODEL


# ══════════════════════════════════════════════════════════════════════════════
# Section 4: Classifier complexity — parsing + heuristic fallback
# ══════════════════════════════════════════════════════════════════════════════


class TestClassifierComplexity:
    """Spec 38 classifier complexity parsing and heuristic fallback."""

    def test_heuristic_fanout_2_agents_is_complex(self):
        """fan-out ≥2 agents → complex."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [
            AgentMatch(agent="observability", confidence=0.9),
            AgentMatch(agent="aws", confidence=0.8),
        ]
        assert Classifier._heuristic_complexity(agents, "something") == "complex"

    def test_heuristic_fanout_3_agents_is_complex(self):
        from src.core.classifier import Classifier, AgentMatch
        agents = [
            AgentMatch(agent="observability", confidence=0.9),
            AgentMatch(agent="aws", confidence=0.8),
            AgentMatch(agent="kubernetes", confidence=0.7),
        ]
        assert Classifier._heuristic_complexity(agents, "x") == "complex"

    def test_heuristic_single_agent_short_input_is_simple(self):
        """Single-agent + short input (≤60 chars) → simple."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [AgentMatch(agent="observability", confidence=0.9)]
        assert Classifier._heuristic_complexity(agents, "list pods") == "simple"

    def test_heuristic_single_agent_long_input_is_standard(self):
        """Single-agent + long input (>60 chars) → standard."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [AgentMatch(agent="observability", confidence=0.9)]
        long_input = "x" * 61
        assert Classifier._heuristic_complexity(agents, long_input) == "standard"

    def test_heuristic_no_agents_is_standard(self):
        """No agents matched → standard (safe default)."""
        from src.core.classifier import Classifier
        assert Classifier._heuristic_complexity([], "something") == "standard"

    def test_heuristic_single_agent_exactly_60_chars_is_simple(self):
        """Boundary: exactly 60 chars → simple."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [AgentMatch(agent="observability", confidence=0.9)]
        assert Classifier._heuristic_complexity(agents, "x" * 60) == "simple"

    def test_classifier_result_complexity_field_defaults_to_standard(self):
        """ClassifierResult.complexity defaults to 'standard'."""
        from src.core.classifier import ClassifierResult
        cr = ClassifierResult()
        assert cr.complexity == "standard"

    def test_classifier_result_preserves_complexity(self):
        """ClassifierResult stores the complexity value."""
        from src.core.classifier import ClassifierResult
        cr = ClassifierResult(complexity="complex")
        assert cr.complexity == "complex"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5: HC3 — RCA/investigation NEVER downgrades to simple
# ══════════════════════════════════════════════════════════════════════════════


class TestRCANeverSimple:
    """HC3: 'why is X slow?', RCA-type queries must stay >= standard.

    The CONTRACT says the classifier prompt explicitly rules out
    RCA/multi-signal from being 'simple'. We test that the heuristic
    also prevents it: if the classifier returns 'simple' for a >60-char
    investigative query, the heuristic would give standard (not simple).
    """

    def test_heuristic_long_rca_query_is_standard(self):
        """A long investigative query (even single-agent) is standard."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [AgentMatch(agent="observability", confidence=0.9)]
        query = "why is the payment-api response time increasing over the last 2 hours?"
        # len > 60 → standard
        assert Classifier._heuristic_complexity(agents, query) == "standard"

    def test_short_but_complex_fanout_is_complex(self):
        """Short text but multi-agent = complex, not simple."""
        from src.core.classifier import Classifier, AgentMatch
        agents = [
            AgentMatch(agent="observability", confidence=0.8),
            AgentMatch(agent="kubernetes", confidence=0.7),
        ]
        assert Classifier._heuristic_complexity(agents, "why is X slow?") == "complex"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6: Full tier-selection matrix (parametrized, contract-level)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("complexity,confidence,deep_enabled,expected_model", [
    # HC2: simple + high-conf → fast
    ("simple", 0.95, False, FAST_MODEL),
    ("simple", 0.85, False, FAST_MODEL),  # boundary
    ("simple", 0.99, True, FAST_MODEL),
    # HC2: complex → deep (or standard if deep disabled)
    ("complex", 0.95, False, STANDARD_MODEL),  # deep disabled → fallback
    ("complex", 0.95, True, DEEP_MODEL),       # deep enabled → Opus
    ("complex", 0.50, False, STANDARD_MODEL),
    ("complex", 0.50, True, DEEP_MODEL),
    # Low confidence → deep (regardless of complexity)
    ("simple", 0.60, False, STANDARD_MODEL),   # low conf → deep → fallback
    ("simple", 0.60, True, DEEP_MODEL),        # low conf → deep → Opus
    ("standard", 0.50, False, STANDARD_MODEL),
    ("standard", 0.50, True, DEEP_MODEL),
    # Default (standard complexity + high confidence) → standard
    ("standard", 0.90, False, STANDARD_MODEL),
    ("standard", 0.90, True, STANDARD_MODEL),
    ("standard", 0.85, False, STANDARD_MODEL),  # boundary: >= HIGH → standard
    ("standard", 0.85, True, STANDARD_MODEL),
])
def test_tier_matrix(complexity, confidence, deep_enabled, expected_model, monkeypatch):
    """Parametrized tier-selection matrix covering HC2 dispatch rules."""
    from src.core.config import settings
    from src.supervisor.agent import _resolve_tier_model
    monkeypatch.setattr(settings, "aigent_tier_deep_enabled", deep_enabled)
    cr = _make_classification(complexity, confidence)
    assert _resolve_tier_model(cr) == expected_model


# ══════════════════════════════════════════════════════════════════════════════
# Section 7: HC1 — NO runtime escalation (verify no escalation code exists)
# ══════════════════════════════════════════════════════════════════════════════


class TestNoRuntimeEscalation:
    """HC1: there must be NO tier bumping/escalation in the dispatch.

    _resolve_tier_model returns a single model_id. It does NOT accept
    a 'previous_tier' parameter or return escalation metadata.
    """

    def test_resolve_tier_model_signature_has_no_escalation_param(self):
        """The function takes only a ClassifierResult, not escalation state."""
        import inspect
        from src.supervisor.agent import _resolve_tier_model
        sig = inspect.signature(_resolve_tier_model)
        params = list(sig.parameters.keys())
        assert params == ["classification"], (
            f"Expected only 'classification' param, got {params} — "
            "escalation params would violate HC1"
        )

    def test_resolve_tier_model_returns_str_or_none(self):
        """Return type is str|None — no escalation metadata object."""
        from src.supervisor.agent import _resolve_tier_model
        cr = _make_classification("standard", 0.9)
        result = _resolve_tier_model(cr)
        assert isinstance(result, str) or result is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 8: _model_family helper (used by validation)
# ══════════════════════════════════════════════════════════════════════════════


class TestModelFamily:
    """Verify _model_family extracts correct family from various ID formats."""

    def test_haiku_family(self):
        from src.core.model_tier import _model_family
        assert _model_family("us.anthropic.claude-haiku-4-5-20251001-v1:0") == "haiku"

    def test_sonnet_family(self):
        from src.core.model_tier import _model_family
        assert _model_family("us.anthropic.claude-sonnet-4-5-20250929-v1:0") == "sonnet"

    def test_opus_family(self):
        from src.core.model_tier import _model_family
        assert _model_family("us.anthropic.claude-opus-4-20250514-v1:0") == "opus"

    def test_unknown_family(self):
        from src.core.model_tier import _model_family
        assert _model_family("us.anthropic.claude-mystery-v1:0") == "unknown"

    def test_empty_string(self):
        from src.core.model_tier import _model_family
        assert _model_family("") == "unknown"

    def test_case_insensitive(self):
        from src.core.model_tier import _model_family
        assert _model_family("US.ANTHROPIC.CLAUDE-HAIKU-V1") == "haiku"


class TestTierRoutingObservability:
    """The pertinent tier-routing counter is emitted with the resolved tier (agentic28)."""

    def test_counter_emitted_with_resolved_tier(self, monkeypatch):
        import src.supervisor.agent as agent_mod
        from src.core.config import settings
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", True)
        counter = MagicMock()
        monkeypatch.setattr(agent_mod, "tier_routing_decisions", counter)
        # complex → deep (regardless of deep enabled/disabled: the DECISION is what's counted)
        agent_mod._resolve_tier_model(_make_classification("complex", 0.95))
        counter.add.assert_called_once_with(1, {"tier": "deep"})

    def test_counter_labels_fast_and_standard(self, monkeypatch):
        import src.supervisor.agent as agent_mod
        from src.core.config import settings
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", True)
        counter = MagicMock()
        monkeypatch.setattr(agent_mod, "tier_routing_decisions", counter)
        agent_mod._resolve_tier_model(_make_classification("simple", 0.95))
        counter.add.assert_called_once_with(1, {"tier": "fast"})
        counter.reset_mock()
        agent_mod._resolve_tier_model(_make_classification("standard", 0.95))
        counter.add.assert_called_once_with(1, {"tier": "standard"})

    def test_counter_not_emitted_when_routing_disabled(self, monkeypatch):
        import src.supervisor.agent as agent_mod
        from src.core.config import settings
        monkeypatch.setattr(settings, "aigent_tier_routing_enabled", False)
        counter = MagicMock()
        monkeypatch.setattr(agent_mod, "tier_routing_decisions", counter)
        assert agent_mod._resolve_tier_model(_make_classification("complex", 0.95)) is None
        counter.add.assert_not_called()
