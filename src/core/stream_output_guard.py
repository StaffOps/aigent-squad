"""Output guard for the STREAMING agentic path (spec 14, L4 + L5).

The non-streaming path runs, on the final answer and in this order: L5 canary
detection (redact-and-continue), the L4 output filter (fail-closed) and the
structural response-quality scan (fail-closed). The streaming path used to emit
the model's final answer straight to the client and to history, so none of
those layers applied — and under ``LLM_PROVIDER != bedrock`` (L1 off) nothing
scanned the answer at all.

``guard_agentic_stream`` wraps the step-event generator and applies the same
layers before any part of the answer leaves the process:

- The agentic loop produces the final text in full before slicing it into
  ``StepFinalChunk`` events, so buffering the chunks until ``StepDone`` adds no
  user-visible latency. The full text is checked (a canary or a secret that
  straddles a chunk boundary is still caught), then re-sliced for progressive
  rendering.
- Block (output filter, quality guard, canary escalation, or an L1 OUTPUT
  block raised mid-loop) → the original text is discarded and the stream ends
  with a refusal chunk + ``StepDone(finish_reason="content_filter")``. The
  supervisor does not save a ``content_filter`` answer to history.
- Trace lines shown to the user (``StepThinking``, ``StepToolCall.args_display``,
  ``StepToolResult.summary``) get canary redaction, and are replaced by a placeholder when the output
  filter detects sensitive data in them. Hiding a trace line never ends the
  stream — the trace is auxiliary, the answer still gets its own check.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, List

from src.core.agentic_loop_streaming import (
    StepDone,
    StepFinalChunk,
    StepThinking,
    StepToolCall,
    StepToolResult,
)
from src.core.canary import CanaryGuard
from src.core.guardrail import GuardrailBlockedError
from src.core.logger import logger
from src.core.metrics import stream_output_interventions
from src.core.output_filter import OutputFilter
from src.core.response_quality import ResponseQualityGuard

# Same slice size the loop uses, so rendering cadence is unchanged.
FINAL_CHUNK_SIZE = 200

# Shown in place of a blocked answer. Mirrors the non-streaming 403 message.
REFUSAL_TEXT = "Request blocked by security guardrail."

# finish_reason for a blocked stream (OpenAI's value for filtered output).
BLOCKED_FINISH_REASON = "content_filter"

_HIDDEN_THINKING = "[reasoning hidden: contained sensitive data]"
_HIDDEN_TOOL_RESULT = "🚫 result hidden: contained sensitive data"
_HIDDEN_TOOL_ARGS = "[args hidden: contained sensitive data]"


class _StreamGuard:
    """Per-stream state: the layers plus the request identity they audit with."""

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        session_id: str,
        canary_guard: CanaryGuard,
        canary_tokens: List[str],
    ) -> None:
        self.agent_id = agent_id
        self.user_id = user_id
        self.session_id = session_id
        self.canary_guard = canary_guard
        self.canary_tokens = canary_tokens
        self.output_filter = OutputFilter()
        self.quality_guard = ResponseQualityGuard()

    def _canary(self, text: str) -> str:
        """L5: redact-and-continue; raises only on session escalation."""
        if not self.canary_tokens:
            return text
        return self.canary_guard.detect(
            text, self.canary_tokens,
            agent_id=self.agent_id,
            user_id=self.user_id,
            session_id=self.session_id,
        )

    def final_answer(self, text: str) -> str:
        """Same layers, same order as GenericAgent.process_request. Raises on block."""
        text = self._canary(text)
        self.output_filter.scan(
            text,
            agent_id=self.agent_id,
            user_id=self.user_id,
            session_id=self.session_id,
        )
        # Structural defects only: the streaming loop does not expose the tool
        # evidence, so resource-ID groundedness (needs infra_data) cannot run.
        # Same error handling as process_request: a block is fail-closed, any
        # other error in the quality guard is swallowed (spec 41 invariant).
        try:
            self.quality_guard.scan(
                text,
                agent_id=self.agent_id,
                user_id=self.user_id,
                session_id=self.session_id,
            )
        except GuardrailBlockedError:
            raise
        except Exception:
            pass
        return text

    def trace_line(self, text: str, placeholder: str) -> str:
        """Canary-redact a trace line; hide it entirely if L4 flags it."""
        text = self._canary(text)
        try:
            self.output_filter.scan(
                text,
                agent_id=self.agent_id,
                user_id=self.user_id,
                session_id=self.session_id,
            )
        except GuardrailBlockedError:
            stream_output_interventions.add(
                1, {"agent_id": self.agent_id, "action": "trace_redacted"},
            )
            return placeholder
        return text


def _slice(text: str) -> List[StepFinalChunk]:
    return [
        StepFinalChunk(text=text[i:i + FINAL_CHUNK_SIZE])
        for i in range(0, len(text), FINAL_CHUNK_SIZE)
    ]


async def guard_agentic_stream(
    events: AsyncGenerator[Any, None],
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    canary_guard: CanaryGuard,
    canary_tokens: List[str],
) -> AsyncGenerator[Any, None]:
    """Apply L5 canary + L4 output filter + quality guard to a step stream.

    Events other than final chunks, thinking and tool results pass through
    unchanged. See the module docstring for the block semantics.
    """
    guard = _StreamGuard(agent_id, user_id, session_id, canary_guard, canary_tokens)
    final_parts: List[str] = []
    try:
        try:
            async for event in events:
                if isinstance(event, StepFinalChunk):
                    final_parts.append(event.text)
                    continue
                if isinstance(event, StepDone):
                    for chunk in _slice(guard.final_answer("".join(final_parts))):
                        yield chunk
                    yield event
                    return
                if isinstance(event, StepThinking):
                    event = StepThinking(text=guard.trace_line(event.text, _HIDDEN_THINKING))
                elif isinstance(event, StepToolCall) and event.args_display:
                    # Args are model-written; key-name masking alone misses a
                    # secret passed as a value.
                    event = StepToolCall(
                        tool_name=event.tool_name,
                        args_display=guard.trace_line(event.args_display, _HIDDEN_TOOL_ARGS),
                    )
                elif isinstance(event, StepToolResult):
                    event = StepToolResult(
                        tool_name=event.tool_name,
                        summary=guard.trace_line(event.summary, _HIDDEN_TOOL_RESULT),
                    )
                yield event

            # Inner generator ended without StepDone: still never emit
            # unchecked text. The SSE encoder closes the stream itself.
            if final_parts:
                for chunk in _slice(guard.final_answer("".join(final_parts))):
                    yield chunk
        except GuardrailBlockedError as e:
            # Fail-closed: nothing of the buffered answer is emitted.
            stream_output_interventions.add(1, {"agent_id": agent_id, "action": "blocked"})
            logger.warning(
                "streamed answer blocked by output guard",
                extra={
                    "audit": True,
                    "event": "stream_output_blocked",
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "categories": list(e.categories or []),
                },
            )
            yield StepFinalChunk(text=REFUSAL_TEXT)
            yield StepDone(finish_reason=BLOCKED_FINISH_REASON)
    finally:
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()
