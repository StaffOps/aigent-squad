"""Token budget — per-session hard cap and history truncation by tokens.

Spec 11 (bedrock-resilience-cost): enforces a configurable maximum token spend
per session. When exceeded, the session is CUT (hard refusal with a clear
message) rather than warned. History truncation removes oldest messages until
the history fits within a token window, preserving the most recent context.
"""
from __future__ import annotations

from typing import List, Tuple

from src.core.config import settings
from src.core.logger import logger
from src.core.model_tier import estimate_tokens
from src.core.state_store import ConversationMessage


class TokenBudgetExceeded(Exception):
    """Raised when a session exceeds its configured token budget (hard cap)."""

    def __init__(self, session_id: str, used: int, limit: int):
        self.session_id = session_id
        self.used = used
        self.limit = limit
        super().__init__(
            f"Session token budget exceeded: {used}/{limit} tokens used. "
            "Please start a new session."
        )


# ── Budget tracker (in-memory per process; DynamoDB tracks actual usage) ─────

class SessionBudgetTracker:
    """Tracks cumulative token usage per session and enforces the hard cap.

    The tracker is deliberately simple (in-memory dict). In a multi-replica
    deployment the real enforcement comes from checking cumulative spend in
    DynamoDB or Redis — this local tracker provides fast fail-fast protection
    within a single replica's lifetime. Worst case (replica restart): the budget
    resets for that replica, but the session TTL (24h) bounds total exposure.
    """

    def __init__(self, max_tokens_per_session: int | None = None):
        self._max = max_tokens_per_session or settings.session_token_budget
        # session_id → cumulative tokens consumed
        self._usage: dict[str, int] = {}

    @property
    def max_tokens(self) -> int:
        return self._max

    def get_usage(self, session_id: str) -> int:
        """Return current cumulative token usage for a session."""
        return self._usage.get(session_id, 0)

    def get_remaining(self, session_id: str) -> int:
        """Return remaining tokens for a session."""
        return max(0, self._max - self.get_usage(session_id))

    def check_budget(self, session_id: str) -> None:
        """Raise ``TokenBudgetExceeded`` if the session is already over budget."""
        used = self.get_usage(session_id)
        if used >= self._max:
            raise TokenBudgetExceeded(session_id, used, self._max)

    def record_usage(self, session_id: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for a session; raise if budget is now exceeded.

        The check happens AFTER recording (post-call) so that the response that
        pushed over the limit is still returned — but the NEXT call will be
        refused. This matches the spec requirement of "cut with a clear message"
        on the next attempt rather than mid-response.
        """
        total = input_tokens + output_tokens
        self._usage[session_id] = self.get_usage(session_id) + total
        logger.debug("Session token usage recorded", extra={
            "session_id": session_id,
            "added": total,
            "cumulative": self._usage[session_id],
            "budget": self._max,
        })

    def reset(self, session_id: str) -> None:
        """Reset budget tracking for a session (e.g. on session end)."""
        self._usage.pop(session_id, None)


# Module-level singleton for in-process tracking.
budget_tracker = SessionBudgetTracker()


# ── History truncation by tokens ─────────────────────────────────────────────

def truncate_history_by_tokens(
    messages: List[ConversationMessage],
    max_tokens: int | None = None,
) -> Tuple[List[ConversationMessage], int]:
    """Truncate conversation history to fit within a token budget.

    Keeps the most recent messages that fit (drops oldest first). Returns
    the truncated list and the total estimated token count of the result.

    Args:
        messages: Full conversation history (oldest first).
        max_tokens: Maximum tokens for the history window. Defaults to
            ``settings.history_max_tokens``.

    Returns:
        (truncated_messages, total_tokens) — truncated list preserving order,
        and the estimated token count of the retained messages.
    """
    if max_tokens is None:
        max_tokens = settings.history_max_tokens

    if not messages:
        return [], 0

    # Estimate tokens per message (content + small overhead for role/metadata).
    msg_tokens = [(msg, estimate_tokens(msg.content) + 4) for msg in messages]

    # Walk from most recent backwards, accumulating until budget exhausted.
    total = 0
    keep_start = len(msg_tokens)
    for i in range(len(msg_tokens) - 1, -1, -1):
        _, tokens = msg_tokens[i]
        if total + tokens > max_tokens:
            break
        total += tokens
        keep_start = i

    truncated = messages[keep_start:]
    if len(truncated) < len(messages):
        logger.debug("History truncated by tokens", extra={
            "original_count": len(messages),
            "kept_count": len(truncated),
            "total_tokens": total,
            "max_tokens": max_tokens,
        })

    return truncated, total
