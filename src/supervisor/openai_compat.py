"""OpenAI-compatible bridge for the supervisor.

Exposes the AIgent-squad as OpenAI-format models so LibreChat (or any
OpenAI-compatible client) can consume it natively — no extra gateway.

This module is a thin translation layer: it shapes requests/responses to the
OpenAI Chat Completions contract and delegates the actual work to the existing
``supervisor.process_request(...)``. It contains NO orchestration logic.

Models exposed:
    aigent-squad           → classifier auto-routes (fan-out / investigation)
    aigent-squad-<agent>   → force a specific specialist (bypass classifier)
"""

from __future__ import annotations

import time
import uuid
from typing import AsyncGenerator, Optional

from pydantic import BaseModel, Field

# Prefix that namespaces every model id this bridge exposes.
MODEL_PREFIX = "aigent-squad"
OWNER = "aigent-squad"


class UnknownModelError(ValueError):
    """Raised when a requested model id maps to no known target."""


# ─── OpenAI request/response models ─────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str = ""


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    user: Optional[str] = None


class DeltaContent(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaContent
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice]


class MessageContent(BaseModel):
    role: str = "assistant"
    content: str


class CompletionChoice(BaseModel):
    index: int = 0
    message: MessageContent
    finish_reason: str = "stop"


class UsageInfo(BaseModel):
    # Real token accounting is deferred (spec 10/27); zeros for now.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: UsageInfo = Field(default_factory=UsageInfo)


class ModelObject(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = OWNER


class ModelsList(BaseModel):
    object: str = "list"
    data: list[ModelObject]


# ─── Model listing & target resolution ──────────────────────────────

def list_models(agent_names: list[str]) -> ModelsList:
    """Build the /v1/models payload: the auto model + one per agent."""
    ids = [MODEL_PREFIX] + [f"{MODEL_PREFIX}-{name}" for name in agent_names]
    return ModelsList(data=[ModelObject(id=mid) for mid in ids])


def resolve_target(model: str, agent_names: list[str]) -> Optional[str]:
    """Map an OpenAI model id to a routing target.

    Returns:
        None              → auto-route via the classifier (model == "aigent-squad")
        "<agent>"         → force that specialist

    Raises:
        UnknownModelError → the id is neither the auto model nor a known agent.
    """
    if model == MODEL_PREFIX:
        return None
    if model.startswith(f"{MODEL_PREFIX}-"):
        agent = model[len(MODEL_PREFIX) + 1:]
        if agent in agent_names:
            return agent
    raise UnknownModelError(f"model '{model}' not found")


# ─── Prompt translation ─────────────────────────────────────────────

def messages_to_user_input(messages: list[ChatMessage]) -> str:
    """Collapse an OpenAI messages array into the supervisor's user_input.

    The squad is turn-oriented and keeps history server-side (DynamoDB, keyed by
    session). We forward the last user turn, prefixed by any system messages as
    context. Prior assistant turns are dropped (the squad reloads its own
    history). If there is no user message, fall back to the last message.
    """
    systems = [m.content for m in messages if m.role == "system" and m.content]
    users = [m.content for m in messages if m.role == "user" and m.content]

    last_user = users[-1] if users else (messages[-1].content if messages else "")
    if systems:
        return "\n\n".join(systems + [last_user]).strip()
    return last_user.strip()


# ─── Helpers ────────────────────────────────────────────────────────

def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _now() -> int:
    return int(time.time())


def _extract_text(result: dict) -> str:
    """Pull the human-facing answer out of the supervisor result dict."""
    return result.get("response", "") or ""


# ─── Encoders ───────────────────────────────────────────────────────

def build_completion(result: dict, model: str) -> ChatCompletionResponse:
    """Non-streaming: supervisor result dict → OpenAI chat.completion."""
    return ChatCompletionResponse(
        id=_completion_id(),
        created=_now(),
        model=model,
        choices=[CompletionChoice(message=MessageContent(content=_extract_text(result)))],
    )


async def sse_stream(result: dict, model: str) -> AsyncGenerator[str, None]:
    """Streaming: emit OpenAI SSE frames for a completed supervisor result.

    Pseudo-streaming: the supervisor returns a full answer today (no token
    streaming until spec 06), so we emit a role prelude, the whole answer as one
    content delta, a finish frame, then the [DONE] sentinel. The frame format is
    forward-compatible with real per-token streaming.
    """
    cid = _completion_id()
    created = _now()

    prelude = ChatCompletionChunk(
        id=cid, created=created, model=model,
        choices=[ChunkChoice(delta=DeltaContent(role="assistant", content=""))],
    )
    yield f"data: {prelude.model_dump_json()}\n\n"

    content = _extract_text(result)
    if content:
        body = ChatCompletionChunk(
            id=cid, created=created, model=model,
            choices=[ChunkChoice(delta=DeltaContent(content=content))],
        )
        yield f"data: {body.model_dump_json()}\n\n"

    final = ChatCompletionChunk(
        id=cid, created=created, model=model,
        choices=[ChunkChoice(delta=DeltaContent(), finish_reason="stop")],
    )
    yield f"data: {final.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"
