"""Conversation message type + history constants shared by every history backend.

Kept apart from ``state_store`` (which builds the process-wide store at import)
so backend modules can import these without a circular import (spec 46).
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.core.response_quality import QualityAssessment


@dataclass
class ConversationMessage:
    """Standard message format"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    agent_id: Optional[str] = None
    # Spec 41: optional structured quality assessment (not persisted to DynamoDB)
    quality_assessment: Optional["QualityAssessment"] = field(default=None, repr=False)


HISTORY_TTL_SECONDS = 24 * 3600  # history retention, every backend (BACKLOG B-07)


def record_history_error(backend: str, op: str) -> None:
    try:
        from src.core.metrics import history_backend_errors
        history_backend_errors.add(1, {"backend": backend, "op": op})
    except Exception:  # metrics must never break the fail-open path
        pass
