from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class KbItemType(str, Enum):
    TROUBLESHOOTING = "troubleshooting"
    DECISION = "decision"
    PATTERN = "pattern"
    INFRASTRUCTURE = "infrastructure"


class KbStatus(str, Enum):
    ACTIVE = "active"
    PENDING_REVIEW = "pending_review"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class KbAction(str, Enum):
    CREATE = "create"
    SUPERSEDE = "supersede"
    NOOP = "noop"


@dataclass
class KbItem:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = KbItemType.TROUBLESHOOTING.value
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    service_name: Optional[str] = None
    embedding: Optional[list[float]] = None
    metadata: dict = field(default_factory=dict)
    confidence_score: float = 0.0
    status: str = KbStatus.ACTIVE.value
    superseded_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class KbDelta:
    """Output of distillation pipeline. Decision to create/supersede/noop a KB item."""
    action: str  # KbAction value
    type: str    # KbItemType value
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    service_name: Optional[str] = None
    confidence: float = 0.0
    supersedes_id: Optional[str] = None
    reasoning: str = ""
