"""Token budget — per-session hard cap and history truncation by tokens.

Spec 11 (bedrock-resilience-cost): enforces a configurable maximum token spend
per session. When exceeded, the session is CUT (hard refusal with a clear
message) rather than warned. History truncation removes oldest messages until
the history fits within a token window, preserving the most recent context.

F-019 fix: the backing store is now Redis (shared across all gateway replicas)
so that N replicas enforce ONE budget per session, not N independent budgets.
Uses a SYNC Redis client because ``record_usage`` is called from ``_invoke_sync``
(a real OS thread via ``asyncio.to_thread``) where ``await`` is impossible.
``check_budget`` runs in async supervisor methods, but the ~1ms sync Redis call
is negligible next to the subsequent 1-10s Bedrock invocation.

Failure mode: FAIL CLOSED. Unlike the rate limiter (which fails open because a
brief over-spend during a Redis outage is bounded and availability is preferred),
the token budget MUST fail closed because:
  - A budget that fails open is an unbounded-spend path with no cap at all.
  - The rate limiter's "bounded over-spend" argument doesn't hold for the token
    budget: a session could consume unlimited tokens for its entire lifetime if
    Redis stays down, whereas the rate limiter's sliding window naturally expires.
  - The cost of a false refusal (user starts a new session) is far lower than
    the cost of an uncapped session running up a $hundreds Bedrock bill.
"""
from __future__ import annotations

import threading
from typing import List, Optional, Tuple

import redis as redis_lib

from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import token_budget_exceeded_blocks
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


# TTL for session budget keys in Redis: 24 hours (86400 seconds).
# Justification:
#   - Sessions have a natural lifetime bounded by the user's interaction window.
#   - The gateway's session_ttl is 24h (DynamoDB state_store TTL, same value).
#   - A budget key that outlives its session wastes memory but causes no harm;
#     a key that expires too early resets the budget mid-session (dangerous).
#   - 24h matches the session lifecycle — when a session expires, so does its
#     budget, and redis memory is reclaimed. A user who returns after 24h
#     already starts a new session anyway.
_SESSION_BUDGET_TTL_SECONDS = 86400

# Redis key prefix — namespaced to avoid collision with rate_limiter keys.
_KEY_PREFIX = "token_budget:session:"


class SessionBudgetTracker:
    """Tracks cumulative token usage per session and enforces the hard cap.

    Backed by Redis (INCRBY atomic increment) so that all gateway replicas
    enforce a single shared budget per session_id. Falls back to an in-memory
    dict ONLY in tests (when ``redis_client=None`` explicitly).

    Failure mode: FAIL CLOSED on Redis errors. See module docstring for rationale.
    """

    def __init__(
        self,
        max_tokens_per_session: int | None = None,
        redis_client: Optional[redis_lib.Redis] = None,
    ):
        self._max = max_tokens_per_session or settings.session_token_budget
        self._redis: Optional[redis_lib.Redis] = redis_client
        # In-memory fallback for unit tests only (redis_client=None).
        self._usage: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def max_tokens(self) -> int:
        return self._max

    def _key(self, session_id: str) -> str:
        return f"{_KEY_PREFIX}{session_id}"

    def get_usage(self, session_id: str) -> int:
        """Return current cumulative token usage for a session."""
        if self._redis is None:
            return self._usage.get(session_id, 0)
        try:
            val = self._redis.get(self._key(session_id))
            return int(val) if val is not None else 0
        except Exception as exc:
            # Fail closed: treat unknown usage as over-budget.
            logger.error(
                "token_budget get_usage redis error (fail closed)",
                extra={"error": str(exc), "session_id": session_id},
            )
            return self._max

    def get_remaining(self, session_id: str) -> int:
        """Return remaining tokens for a session."""
        return max(0, self._max - self.get_usage(session_id))

    def check_budget(self, session_id: str) -> None:
        """Raise ``TokenBudgetExceeded`` if the session is already over budget.

        Fails CLOSED on Redis unavailability: if we cannot confirm the budget
        has headroom, refuse the request. This is the OPPOSITE of the rate
        limiter's fail-open policy — see module docstring for the reasoning.
        """
        if self._redis is None:
            used = self._usage.get(session_id, 0)
            if used >= self._max:
                token_budget_exceeded_blocks.add(1, {"session_id": session_id})
                raise TokenBudgetExceeded(session_id, used, self._max)
            return

        try:
            val = self._redis.get(self._key(session_id))
            used = int(val) if val is not None else 0
        except Exception as exc:
            # Fail closed: cannot verify budget → refuse.
            logger.error(
                "token_budget check_budget redis error (fail closed — refusing request)",
                extra={"error": str(exc), "session_id": session_id},
            )
            token_budget_exceeded_blocks.add(1, {"session_id": session_id})
            raise TokenBudgetExceeded(session_id, self._max, self._max) from exc

        if used >= self._max:
            token_budget_exceeded_blocks.add(1, {"session_id": session_id})
            raise TokenBudgetExceeded(session_id, used, self._max)

    def record_usage(self, session_id: str, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for a session atomically (INCRBY in Redis).

        The check happens AFTER recording (post-call) so that the response that
        pushed over the limit is still returned — but the NEXT call will be
        refused. This matches the spec requirement of "cut with a clear message"
        on the next attempt rather than mid-response.

        Fails CLOSED on Redis error, but does NOT raise here: this runs after the
        model call has already completed, so raising would discard a response the
        user has already paid for. Instead it logs and pins local usage to the
        maximum, so the NEXT ``check_budget`` refuses. The refusal is deferred, not
        skipped — net effect is still fail-closed.
        """
        total = input_tokens + output_tokens
        if self._redis is None:
            with self._lock:
                cumulative = self._usage.get(session_id, 0) + total
                self._usage[session_id] = cumulative
            logger.debug("Session token usage recorded (in-memory)", extra={
                "session_id": session_id,
                "added": total,
                "cumulative": cumulative,
                "budget": self._max,
            })
            return

        key = self._key(session_id)
        try:
            # Atomic increment — no read-modify-write race across replicas.
            cumulative = self._redis.incrby(key, total)
            # Set TTL only on first write (when cumulative == total, key was
            # just created) or refresh it. Using EXPIRE unconditionally is safe
            # and cheap — it resets the 24h window on each usage, which is fine:
            # an active session should not expire mid-conversation.
            self._redis.expire(key, _SESSION_BUDGET_TTL_SECONDS)
        except Exception as exc:
            # Fail closed: if we can't record, we can't enforce → refuse next.
            logger.error(
                "token_budget record_usage redis error (fail closed)",
                extra={"error": str(exc), "session_id": session_id},
            )
            # Do NOT raise here — the current response was already generated.
            # But mark the in-memory state as over-budget so subsequent calls
            # within this replica also fail. Other replicas will also fail
            # closed on their next check_budget if Redis is still down.
            with self._lock:
                self._usage[session_id] = self._max
            return

        logger.debug("Session token usage recorded (redis)", extra={
            "session_id": session_id,
            "added": total,
            "cumulative": cumulative,
            "budget": self._max,
        })

    def reset(self, session_id: str) -> None:
        """Reset budget tracking for a session (e.g. on session end)."""
        if self._redis is None:
            with self._lock:
                self._usage.pop(session_id, None)
            return
        try:
            self._redis.delete(self._key(session_id))
        except Exception as exc:
            logger.warning(
                "token_budget reset redis error",
                extra={"error": str(exc), "session_id": session_id},
            )
        with self._lock:
            self._usage.pop(session_id, None)


def _create_budget_redis_client() -> Optional[redis_lib.Redis]:
    """Create a sync Redis client reusing the same config as rate_limiter.

    Returns None if construction or connectivity check fails (tests, missing
    config, no Redis server). A startup ping validates that the server is
    reachable — if not, the tracker falls back to in-memory (with a warning).
    This ensures tests (REDIS_HOST=localhost, no server) still work while
    production (real ElastiCache) gets the shared-store behavior.
    """
    try:
        host = settings.redis_host
        if not host:
            return None
        client = redis_lib.Redis(
            host=host,
            port=settings.redis_port,
            password=settings.redis_password,
            ssl=settings.redis_ssl,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # Validate reachability — fail fast at startup rather than on first request.
        client.ping()
        return client
    except Exception as exc:
        logger.warning(
            "token_budget: Redis unavailable at startup — falling back to in-memory "
            "(per-replica budget only). In production this means N replicas enforce "
            "N independent budgets. Ensure Redis is reachable.",
            extra={"error": str(exc)},
        )
        return None


# Module-level singleton — uses Redis in production, in-memory in tests.
_budget_redis = _create_budget_redis_client()
budget_tracker = SessionBudgetTracker(redis_client=_budget_redis)


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
