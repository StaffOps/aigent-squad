"""Tests for src.core.kb.validator — confidence threshold logic."""
from src.core.kb.models import KbDelta
from src.core.kb.validator import decide_status


def test_validator_auto_approve_high_confidence_troubleshooting():
    delta = KbDelta(action="create", type="troubleshooting", confidence=0.9)
    assert decide_status(delta) == "active"


def test_validator_pending_review_below_threshold():
    delta = KbDelta(action="create", type="troubleshooting", confidence=0.7)
    assert decide_status(delta) == "pending_review"


def test_validator_rejected_very_low_confidence():
    delta = KbDelta(action="create", type="troubleshooting", confidence=0.3)
    assert decide_status(delta) == "rejected"


def test_validator_decision_never_auto_approve():
    delta = KbDelta(action="create", type="decision", confidence=0.99)
    assert decide_status(delta) == "pending_review"


def test_validator_pattern_higher_threshold():
    delta = KbDelta(action="create", type="pattern", confidence=0.85)
    assert decide_status(delta) == "pending_review"
