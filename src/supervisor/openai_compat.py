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

import logging
import os

# Tool-trace presentation (streaming). Chat UIs (LibreChat, Open WebUI) render
# <think>/<thinking> as a collapsible "Thinking" panel but SANITIZE raw HTML like
# <details> (shows as plain text). Configurable via AIGENT_TRACE_STYLE so it adapts
# to the client: think (default) | details | plain | off.
_TRACE_STYLE = os.environ.get("AIGENT_TRACE_STYLE", "think").lower()
_TRACE_OFF = _TRACE_STYLE == "off"
_TRACE_OPEN = {
    "think": "<think>\n🔧 Tool trace\n\n",
    "details": "<details>\n<summary>🔧 Tool trace</summary>\n\n",
    "plain": "🔧 Tool trace\n",
    "off": "",
}.get(_TRACE_STYLE, "<think>\n🔧 Tool trace\n\n")
_TRACE_CLOSE = {
    "think": "\n</think>\n\n",
    "details": "\n</details>\n\n",
    "plain": "\n\n",
    "off": "",
}.get(_TRACE_STYLE, "\n</think>\n\n")

import time
import uuid
from typing import Any, AsyncGenerator, Optional

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
    # Spec 41 (B-16 Phase-2): structured quality assessment — namespaced
    # extension, non-streaming only. Standard OpenAI clients ignore unknown
    # top-level fields. message.content is byte-identical to today.
    x_aigent: Optional[dict] = Field(default=None, json_schema_extra={"description": "Structured quality assessment (spec 41)"})

    def model_dump(self, **kwargs) -> dict:
        """Override to omit x_aigent when None (clean contract for clients)."""
        data: dict[str, Any] = super().model_dump(**kwargs)
        if data.get("x_aigent") is None:
            data.pop("x_aigent", None)
        return data


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
        None              → auto-route via the classifier (model == "aigent-squad",
                            or any unrecognized model id — e.g. "base", "large",
                            "gpt-4" sent by external clients like Grafana LLM app)
        "<agent>"         → force that specialist (model == "aigent-squad-<agent>")

    Unrecognized model ids are auto-routed (returns None) with a debug log instead
    of raising UnknownModelError. This allows external integrations that cannot set
    an arbitrary model id (e.g. Grafana LLM app) to work without HTTP 400.
    """
    import logging

    if model == MODEL_PREFIX:
        return None
    if model.startswith(f"{MODEL_PREFIX}-"):
        agent = model[len(MODEL_PREFIX) + 1:]
        if agent in agent_names:
            return agent
    # Unrecognized model id → auto-route (same as bare "aigent-squad").
    logging.getLogger(__name__).debug(
        "unrecognized model id '%s' auto-routed (treated as auto)", model
    )
    return None


# ─── Prompt translation ─────────────────────────────────────────────

def messages_to_user_input(messages: list[ChatMessage]) -> str:
    """Collapse an OpenAI messages array into the supervisor's user_input.

    The squad is turn-oriented and keeps history server-side (DynamoDB, keyed by
    session). We forward ONLY the last user turn. Prior assistant turns are
    dropped (the squad reloads its own history).

    SYSTEM MESSAGES ARE DROPPED. Integration clients (Grafana LLM app, LibreChat,
    Continue.dev) inject their own system prompts ("You are a helpful assistant"),
    which (a) add no routing value — the squad has its own system prompt and
    classifier catalog — and (b) are NOT end-user input, so guardrailing them
    produces false positives: a benign persona prompt trips the Bedrock
    prompt-injection detection and the whole request 403s.

    INVARIANT: this is safe only while system messages originate from
    authenticated *client code* (bearer-token admission control), never from
    end-user free-text. If end-users ever gain system-message authoring, revisit
    this decision (see the security section in AGENTS.md).
    """
    users = [m.content for m in messages if m.role == "user" and m.content]
    if users:
        return users[-1].strip()
    # Fallback: last message of any NON-system role (never a system message).
    non_system = [m.content for m in messages if m.role != "system" and m.content]
    return non_system[-1].strip() if non_system else ""


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
    # Spec 41: surface structured quality assessment under namespaced extension.
    #
    # The assessment arrives in TWO shapes depending on the caller:
    #   - dataclass  — when build_completion runs in the same process that
    #     produced it (supervisor-internal callers, unit tests);
    #   - plain dict — on the DEPLOYED path, because the gateway owns
    #     /v1/chat/completions and gets the supervisor result via
    #     `resp.json()` (supervisor_client.process), which turns the dataclass
    #     into a JSON object.
    # Handling only the dataclass made this field inert in production: the
    # attribute access raised AttributeError, the bare `except` swallowed it,
    # and x_aigent was silently omitted from every response while the metrics
    # kept showing the assessment was produced. Homologated 2026-08-08.
    x_aigent: Optional[dict] = None
    assessment = result.get("quality_assessment")
    if assessment is not None:
        try:
            if isinstance(assessment, dict):
                # Asymmetric on purpose: `confidence` has no default on the
                # dataclass, so its absence is a genuine anomaly worth raising
                # (the except below logs + omits the field). `unverified_claims`
                # defaults to [], so an absent/None value is normal.
                confidence = assessment["confidence"]
                unverified_claims = assessment.get("unverified_claims") or []
            else:
                confidence = assessment.confidence
                unverified_claims = assessment.unverified_claims
            # Reject a non-sequence rather than coerce it: `list("a claim")`
            # would silently yield ['a',' ','c',...] — trading the old silent
            # omission for silently WRONG data on the wire. Flagged by
            # independent review of ff6ad19.
            if isinstance(unverified_claims, (str, bytes)) or not isinstance(
                unverified_claims, (list, tuple)
            ):
                raise TypeError(
                    f"unverified_claims must be a list, got {type(unverified_claims).__name__}"
                )
            x_aigent = {
                "quality": {
                    "confidence": confidence,
                    "unverified_claims": list(unverified_claims),
                }
            }
        except Exception as exc:
            # Non-blocking by design (spec 41 invariant): a malformed assessment
            # must never fail the answer. But it MUST NOT be silent either —
            # silence is what hid this bug through 56 passing tests.
            logging.getLogger(__name__).warning(
                "spec41: could not surface x_aigent from assessment "
                "(type=%s): %s",
                type(assessment).__name__,
                exc,
            )

    return ChatCompletionResponse(
        id=_completion_id(),
        created=_now(),
        model=model,
        choices=[CompletionChoice(message=MessageContent(content=_extract_text(result)))],
        x_aigent=x_aigent,
    )


async def sse_stream(result: dict, model: str) -> AsyncGenerator[str, None]:
    """Streaming: emit OpenAI SSE frames for a completed supervisor result.

    Pseudo-streaming fallback: used when the agentic streaming path is not
    available (e.g. classifier/synthesis or legacy non-agentic agents). Emits
    a role prelude, the whole answer as one content delta, a finish frame, then
    the [DONE] sentinel.
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


async def sse_stream_agentic(
    step_events: AsyncGenerator,
    model: str,
) -> AsyncGenerator[str, None]:
    """Real incremental streaming: convert agentic step events to SSE deltas.

    Phase 3.5 (S1/S2): emits proper OpenAI chat.completion.chunk frames as the
    agentic loop runs — thinking, tool calls, results, and final answer chunks
    render progressively in LibreChat.

    Stream shape (visual UX; wrapper via AIGENT_TRACE_STYLE, default <think> which
    LibreChat/Open WebUI render as a collapsible "Thinking" panel):
      <think>
      🔧 Tool trace

      🧭 Routed to **obs** (confidence 92%)
      🔧 query_metrics(namespace="monitoring")
      📦 3 items
      🔧 get_pods(namespace="monitoring")
      📦 12 items
      </think>

      [final answer streamed here, clean and separated]

    Protocol (OpenAI SSE):
      1. Role prelude (role="assistant", content="")
      2. Details-open block before first trace step
      3. Terse step events (tool calls + results, no raw JSON)
      4. Details-close right before the first final-answer chunk
      5. Final answer chunks (the actual answer)
      6. StepDone → finish_reason delta + [DONE]

    Security (S4): step events already contain guardrail-scanned/sanitized text.
    This encoder trusts the upstream loop's B3 enforcement.
    """
    from src.core.agentic_loop_streaming import (
        StepDone,
        StepFinalChunk,
        StepRouting,
        StepThinking,
        StepToolCall,
        StepToolResult,
    )

    cid = _completion_id()
    created = _now()

    def _make_chunk(content: str) -> str:
        """Helper: build an SSE frame with a content delta."""
        c = ChatCompletionChunk(
            id=cid, created=created, model=model,
            choices=[ChunkChoice(delta=DeltaContent(content=content))],
        )
        return f"data: {c.model_dump_json()}\n\n"

    # 1. Role prelude
    prelude = ChatCompletionChunk(
        id=cid, created=created, model=model,
        choices=[ChunkChoice(delta=DeltaContent(role="assistant", content=""))],
    )
    yield f"data: {prelude.model_dump_json()}\n\n"

    # State: whether the <details> block is open (trace steps are flowing)
    details_opened = False
    details_closed = False

    # 2. Stream step events
    async for event in step_events:
        if isinstance(event, (StepRouting, StepThinking, StepToolCall, StepToolResult)):
            if _TRACE_OFF:
                continue  # trace suppressed for this client
            # Open the trace block on the first trace-type event
            if not details_opened:
                yield _make_chunk(_TRACE_OPEN)
                details_opened = True

            if isinstance(event, StepRouting):
                foco = f' — foco: "{event.sub_query}"' if event.sub_query else ""
                yield _make_chunk(
                    f"🧭 Routed to **{event.agent}** "
                    f"(confidence {event.confidence:.0%}){foco}\n"
                )
            elif isinstance(event, StepThinking):
                yield _make_chunk(f"💭 {event.text}\n")
            elif isinstance(event, StepToolCall):
                if event.args_display:
                    yield _make_chunk(
                        f"🔧 {event.tool_name}({event.args_display})\n"
                    )
                else:
                    yield _make_chunk(f"🔧 {event.tool_name}()\n")
            elif isinstance(event, StepToolResult):
                yield _make_chunk(f"{event.summary}\n")

        elif isinstance(event, StepFinalChunk):
            # Close <details> right before first answer chunk
            if details_opened and not details_closed:
                yield _make_chunk(_TRACE_CLOSE)
                details_closed = True
            yield _make_chunk(event.text)

        elif isinstance(event, StepDone):
            # Close <details> if no final chunk followed the trace
            if details_opened and not details_closed:
                yield _make_chunk(_TRACE_CLOSE)
                details_closed = True

            # 3. Terminal frame
            final = ChatCompletionChunk(
                id=cid, created=created, model=model,
                choices=[ChunkChoice(
                    delta=DeltaContent(),
                    finish_reason=event.finish_reason,
                )],
            )
            yield f"data: {final.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

    # Safety: if generator exhausts without StepDone, still close cleanly
    if details_opened and not details_closed:
        yield _make_chunk(_TRACE_CLOSE)
    final = ChatCompletionChunk(
        id=cid, created=created, model=model,
        choices=[ChunkChoice(delta=DeltaContent(), finish_reason="stop")],
    )
    yield f"data: {final.model_dump_json()}\n\n"
    yield "data: [DONE]\n\n"
