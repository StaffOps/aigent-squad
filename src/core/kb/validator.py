"""Validate KbDelta against confidence thresholds. Decide auto-approve vs pending_review."""
from src.core.kb.models import KbDelta, KbItemType, KbStatus

THRESHOLDS = {
    KbItemType.TROUBLESHOOTING.value: 0.85,
    KbItemType.PATTERN.value: 0.90,
    KbItemType.INFRASTRUCTURE.value: 0.80,
    KbItemType.DECISION.value: 1.01,  # never auto-approve
}


def decide_status(delta: KbDelta) -> str:
    """Returns KbStatus value: 'active' (auto-approve) or 'pending_review' or 'rejected'."""
    if delta.confidence < 0.5:
        return KbStatus.REJECTED.value
    threshold = THRESHOLDS.get(delta.type, 0.85)
    if delta.confidence >= threshold:
        return KbStatus.ACTIVE.value
    return KbStatus.PENDING_REVIEW.value
