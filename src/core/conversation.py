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


HISTORY_TRUNCATION_MARKER = "\n[... truncated by aigent history cap]"


def cap_history_content(content: str) -> str:
    """Cut ``content`` to ``HISTORY_MAX_MESSAGE_CHARS`` before it is stored.

    Applied by every history backend on write (BACKLOG F-025 c): a single huge
    message must not grow Redis memory (shared with rate/budget counters) or a
    Postgres/DynamoDB row without bound. The result, marker included, never
    exceeds the cap; a cap of 0 disables it.
    """
    from src.core.config import settings

    limit = settings.history_max_message_chars
    if limit <= 0 or len(content) <= limit:
        return content
    if limit <= len(HISTORY_TRUNCATION_MARKER):
        return content[:limit]
    return content[: limit - len(HISTORY_TRUNCATION_MARKER)] + HISTORY_TRUNCATION_MARKER
