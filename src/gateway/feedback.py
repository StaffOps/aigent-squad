"""User feedback on answers (spec 47, BACKLOG B-03).

The gateway mints a response id for every answer and stores a small ownership
record in Redis (``feedback:resp:<id>``: consumer, scoped user, session, agent —
no answer text). ``POST /feedback`` rates that answer once
(``feedback:score:<id>``, first write wins) and counts it in
``aigent.feedback.score{agent_id, score}``.

Failure policy (AGENTS.md invariant 6):
  - recording the response id is fail-open: a Redis error never fails a query;
  - the feedback write is fail-closed: without the ownership record the gateway
    cannot check the caller's consumer, so it answers 503 instead of guessing.

A rating for a response another consumer (API key) received is answered 404,
the same as an unknown or expired id, so one key cannot probe another's ids.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional, Union

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from src.core.config import settings
from src.core.metrics import feedback_errors, feedback_score
from src.gateway.auth import AuthResult, require_edge_auth

logger = logging.getLogger(__name__)

RESPONSE_ID_HEADER = "X-Aigent-Response-Id"
RESPONSE_ID_PATTERN = r"^[A-Za-z0-9_-]{8,80}$"
_RESP_PREFIX = "feedback:resp:"
_SCORE_PREFIX = "feedback:score:"
_AGENT_LABEL_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def new_query_response_id() -> str:
    """Response id for the native ``/query`` answer."""
    return f"resp-{uuid.uuid4().hex}"


def new_completion_id() -> str:
    """Response id for an OpenAI-compatible answer (keeps the ``chatcmpl-`` form)."""
    return f"chatcmpl-{uuid.uuid4().hex}"


def agent_label(agent_id: Any) -> str:
    """Bound the ``agent_id`` metric label: a registry-style name or ``other``."""
    if isinstance(agent_id, str) and _AGENT_LABEL_RE.match(agent_id):
        return agent_id
    return "other"


def agent_of(result: dict[str, Any]) -> Any:
    """The answering agent in a supervisor result (``agent``; ``agent_id`` as fallback)."""
    return result.get("agent") or result.get("agent_id")


def score_bucket(score: Union[str, int]) -> str:
    """Normalise both scales to one bounded bucket (design Decision 4)."""
    if score == "up" or (isinstance(score, int) and score >= 4):
        return "positive"
    if score == 3:
        return "neutral"
    return "negative"


class FeedbackRequest(BaseModel):  # type: ignore[misc]
    """Body of ``POST /feedback``. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    response_id: str = Field(pattern=RESPONSE_ID_PATTERN)
    score: Union[Literal["up", "down"], StrictInt]
    comment: Optional[str] = None

    @field_validator("score")  # type: ignore[untyped-decorator]
    @classmethod
    def _score_range(cls, value: Union[str, int]) -> Union[str, int]:
        if isinstance(value, int) and not 1 <= value <= 5:
            raise ValueError("score must be 'up', 'down' or an integer from 1 to 5")
        return value

    @field_validator("comment")  # type: ignore[untyped-decorator]
    @classmethod
    def _comment_length(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value) > settings.feedback_comment_max_chars:
            raise ValueError(
                f"comment longer than {settings.feedback_comment_max_chars} characters"
            )
        return value


class FeedbackUnavailableError(Exception):
    """The store cannot be reached, so ownership cannot be verified."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeedbackStore:
    """Redis-backed response-ownership records and ratings."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def record_response(
        self,
        response_id: str,
        auth: Optional[AuthResult],
        user_id: str,
        session_id: str,
        agent_id: Any,
    ) -> None:
        """Store who received ``response_id``. Fail-open: never raises."""
        if not settings.feedback_enabled or self._redis is None:
            return
        record = {
            "consumer_id": auth.consumer_id if auth is not None else "internal",
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_label(agent_id),
            "created_at": _now_iso(),
        }
        try:
            await self._redis.set(
                _RESP_PREFIX + response_id,
                json.dumps(record),
                ex=settings.feedback_response_ttl_seconds,
            )
        except Exception as exc:  # fail-open: the answer is already on its way
            feedback_errors.add(1, {"reason": "record_failed"})
            logger.warning("feedback: could not record response id (%s)", type(exc).__name__)

    async def submit(
        self, request: FeedbackRequest, auth: Optional[AuthResult]
    ) -> Literal["recorded", "unknown_response", "already_rated"]:
        """Store one rating. Raises FeedbackUnavailableError when Redis fails."""
        if self._redis is None:
            raise FeedbackUnavailableError("no redis client")
        consumer_id = auth.consumer_id if auth is not None else "internal"
        try:
            raw = await self._redis.get(_RESP_PREFIX + request.response_id)
            if raw is None:
                return "unknown_response"
            record = json.loads(raw)
            if record.get("consumer_id") != consumer_id:
                # Same answer as an unknown id: no oracle across consumers.
                return "unknown_response"
            agent = agent_label(record.get("agent_id"))
            bucket = score_bucket(request.score)
            rating = {
                "score": request.score,
                "bucket": bucket,
                "comment": request.comment,
                "agent_id": agent,
                "consumer_id": consumer_id,
                "user_id": record.get("user_id"),
                "session_id": record.get("session_id"),
                "created_at": _now_iso(),
            }
            stored = await self._redis.set(
                _SCORE_PREFIX + request.response_id,
                json.dumps(rating),
                ex=settings.feedback_record_ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            # Redis down, or a corrupt ownership record that cannot be trusted for
            # the scope check: refuse rather than accept an unverified rating.
            raise FeedbackUnavailableError(type(exc).__name__) from exc
        if not stored:
            return "already_rated"
        feedback_score.add(1, {"agent_id": agent, "score": bucket})
        return "recorded"


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": error_type, "message": message}})


def create_feedback_router(get_store: Callable[[], FeedbackStore]) -> APIRouter:
    """Build the ``POST /feedback`` router. ``get_store`` is resolved per request."""
    router = APIRouter()

    @router.post("/feedback", status_code=201)  # type: ignore[untyped-decorator]
    async def submit_feedback(
        request: FeedbackRequest, auth: AuthResult = Depends(require_edge_auth)
    ) -> Any:
        """Rate one answer the caller's consumer received (spec 47)."""
        if not settings.feedback_enabled:
            return _error(404, "feedback_disabled", "feedback is disabled")
        try:
            outcome = await get_store().submit(request, auth)
        except FeedbackUnavailableError:
            feedback_errors.add(1, {"reason": "store_unavailable"})
            return _error(503, "feedback_unavailable", "feedback store unavailable")
        if outcome == "unknown_response":
            feedback_errors.add(1, {"reason": "unknown_response"})
            return _error(404, "unknown_response", "unknown or expired response id")
        if outcome == "already_rated":
            feedback_errors.add(1, {"reason": "already_rated"})
            return _error(409, "already_rated", "this response was already rated")
        return {"response_id": request.response_id, "status": "recorded"}

    return router
