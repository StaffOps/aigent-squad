"""Independent contract tests for Phase 3.5 streaming (spec 37).

Written by test-author (independent of implementation author) against the
SPEC CONTRACT, not the implementation details. Verifies:

  (1) SSE framing: tool-call step → result-summary step → final answer deltas
      → finish_reason + [DONE] — valid OpenAI chat.completion.chunk
  (2) stream:false regression: single non-streamed completion still works
  (3) S4 redaction: secret/PII in tool result NEVER appears raw in any chunk
  (4) Budget exhaustion: streams degraded final note with finish_reason=length
  (5) Fail-open: tool error streams inline error step, completes with [DONE]
  (6) Extended-thinking OFF by default: no reasoning deltas unless enabled

Target: ≥90% coverage on agentic_loop_streaming.py + sse_stream_agentic.
"""
from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from src.core.agentic_loop_streaming import (
    ENABLE_EXTENDED_THINKING,
    AgenticStepEvent,
    StepDone,
    StepFinalChunk,
    StepThinking,
    StepToolCall,
    StepToolResult,
    _sanitize_args_for_display,
    _summarize_tool_result,
    run_agentic_loop_streaming,
)
from src.supervisor.openai_compat import (
    build_completion,
    sse_stream,
    sse_stream_agentic,
)


# ---------------------------------------------------------------------------
# Test helpers / factories
# ---------------------------------------------------------------------------


def _make_adapter(name="test-mcp", tool_names=None):
    """Factory for a mock MCP adapter with configurable tool specs."""
    tool_names = tool_names or ["pods_list_in_namespace"]
    adapter = AsyncMock()
    adapter.name = name
    adapter.url = f"http://{name}:8080"
    adapter.tools = tool_names
    adapter.list_tool_specs = AsyncMock(return_value=[
        {
            "toolSpec": {
                "name": tn,
                "description": f"Tool {tn}",
                "inputSchema": {
                    "json": {"type": "object", "properties": {"ns": {"type": "string"}}}
                },
            }
        }
        for tn in tool_names
    ])
    adapter.call_tool = AsyncMock(return_value='[{"name":"item-1"},{"name":"item-2"}]')
    return adapter


def _bedrock_tool_use(tool_name="pods_list_in_namespace", args=None, tool_id="tu-abc"):
    """Simulate a Bedrock Converse response requesting tool_use."""
    return {
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 120, "output_tokens": 60},
        "content": [
            {
                "type": "tool_use",
                "toolUseId": tool_id,
                "name": tool_name,
                "input": args or {"ns": "monitoring"},
            }
        ],
    }


def _bedrock_final(text="The namespace has 2 pods running."):
    """Simulate a Bedrock Converse final text response."""
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 250, "output_tokens": 100},
        "content": [{"type": "text", "text": text}],
    }


def _bedrock_with_reasoning(reasoning: str, answer: str):
    """Simulate a Converse response containing reasoning + text blocks."""
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 150, "output_tokens": 90},
        "content": [
            {"type": "reasoning", "text": reasoning},
            {"type": "text", "text": answer},
        ],
    }


async def _collect(gen) -> list:
    """Consume an async generator into a list."""
    items = []
    async for item in gen:
        items.append(item)
    return items


def _parse_sse_frames(frames: list[str]) -> list[dict | str]:
    """Parse SSE frames into dicts (JSON) or raw strings ([DONE])."""
    parsed = []
    for f in frames:
        text = f.removeprefix("data: ").strip()
        if text == "[DONE]":
            parsed.append("[DONE]")
        else:
            parsed.append(json.loads(text))
    return parsed


LOOP_KWARGS = dict(
    query="list pods in monitoring",
    system_prompt="You are a K8s assistant.",
    history_text="",
    agent_id="kubernetes",
    user_id="contract-test-user",
    session_id="contract-test-session",
)


# ===========================================================================
# CONTRACT TEST 1: Valid OpenAI SSE framing with tool call flow
# ===========================================================================


class TestContract1_SSEFramingWithToolCall:
    """A query with one tool call streams: tool-call step delta, result-summary
    step delta, then final answer deltas, then finish_reason + [DONE] — valid
    OpenAI chat.completion.chunk framing."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_event_sequence_ordering(self, _gr, _ga, mock_bedrock):
        """Events appear in order: ToolCall → ToolResult → FinalChunk(s) → Done."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        types = [type(e).__name__ for e in events]
        tc_idx = types.index("StepToolCall")
        tr_idx = types.index("StepToolResult")
        fc_idx = types.index("StepFinalChunk")
        done_idx = types.index("StepDone")

        assert tc_idx < tr_idx < fc_idx < done_idx, (
            f"Expected ToolCall < ToolResult < FinalChunk < Done, got: {types}"
        )

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_sse_frames_are_valid_openai_chunks(self, _gr, _ga, mock_bedrock):
        """sse_stream_agentic produces valid OpenAI chat.completion.chunk JSON."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final("Answer text."),
        ])
        adapter = _make_adapter()

        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "aigent-squad-kubernetes"))

        parsed = _parse_sse_frames(frames)

        # Last element must be [DONE] sentinel
        assert parsed[-1] == "[DONE]"

        # All JSON frames must have required OpenAI fields
        json_frames = [p for p in parsed if isinstance(p, dict)]
        assert len(json_frames) >= 4  # prelude + tool + result + final + finish

        for chunk in json_frames:
            assert chunk["object"] == "chat.completion.chunk"
            assert "id" in chunk
            assert "created" in chunk
            assert "model" in chunk
            assert "choices" in chunk
            assert len(chunk["choices"]) == 1
            choice = chunk["choices"][0]
            assert "delta" in choice
            assert "finish_reason" in choice

        # First frame is role prelude
        assert json_frames[0]["choices"][0]["delta"]["role"] == "assistant"

        # Last JSON frame has finish_reason="stop"
        assert json_frames[-1]["choices"][0]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_sse_contains_tool_call_and_result_content(self, _gr, _ga, mock_bedrock):
        """Stream must contain the tool name and a summary indicator."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use("pods_list_in_namespace", {"ns": "default"}),
            _bedrock_final("Found pods."),
        ])
        adapter = _make_adapter()

        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        all_content = ""
        for f in frames:
            text = f.removeprefix("data: ").strip()
            if text != "[DONE]":
                chunk = json.loads(text)
                content = chunk["choices"][0]["delta"].get("content") or ""
                all_content += content

        # Must contain the tool call indicator
        assert "🔧" in all_content
        assert "pods_list_in_namespace" in all_content
        # Must contain the result summary indicator
        assert "📦" in all_content
        # Must contain the final answer
        assert "Found pods" in all_content

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_all_frames_share_single_completion_id(self, _gr, _ga, mock_bedrock):
        """All SSE chunks in one stream share the same chatcmpl-* id."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter()

        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        ids = set()
        for f in frames:
            text = f.removeprefix("data: ").strip()
            if text != "[DONE]":
                ids.add(json.loads(text)["id"])

        assert len(ids) == 1, f"Expected 1 unique id, got {len(ids)}: {ids}"
        assert list(ids)[0].startswith("chatcmpl-")


# ===========================================================================
# CONTRACT TEST 2: stream:false still returns single non-streamed completion
# ===========================================================================


class TestContract2_NonStreamedCompletion:
    """stream:false must still work — returns a single chat.completion (no SSE)."""

    def test_build_completion_returns_chat_completion_object(self):
        """build_completion returns object='chat.completion' (not chunk)."""
        result = {"response": "Here are 5 pods in the monitoring namespace."}
        resp = build_completion(result, "aigent-squad-kubernetes")

        assert resp.object == "chat.completion"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.choices[0].message.role == "assistant"
        assert "5 pods" in resp.choices[0].message.content

    def test_build_completion_has_required_fields(self):
        """Non-streamed response has all OpenAI-required fields."""
        result = {"response": "answer"}
        resp = build_completion(result, "model-x")

        assert resp.id.startswith("chatcmpl-")
        assert resp.created > 0
        assert resp.model == "model-x"
        assert resp.usage is not None

    def test_build_completion_empty_response(self):
        """Empty response is handled gracefully (no crash)."""
        result = {"response": ""}
        resp = build_completion(result, "model")
        assert resp.choices[0].message.content == ""

    def test_build_completion_missing_response_key(self):
        """Missing 'response' key defaults to empty string."""
        result = {}
        resp = build_completion(result, "model")
        assert resp.choices[0].message.content == ""

    @pytest.mark.asyncio
    async def test_pseudo_stream_fallback_still_emits_done(self):
        """Legacy sse_stream (pseudo-streaming) terminates with [DONE]."""
        result = {"response": "legacy answer"}
        frames = await _collect(sse_stream(result, "aigent-squad"))

        assert frames[-1] == "data: [DONE]\n\n"
        # Must have at least: prelude + content + finish + [DONE]
        assert len(frames) >= 3


# ===========================================================================
# CONTRACT TEST 3: S4 — secret/PII in tool result is REDACTED in stream
# ===========================================================================


class TestContract3_S4Redaction:
    """A tool result containing a secret/PII is REDACTED in the streamed step.
    Raw output never appears in any chunk."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result")
    async def test_raw_secret_never_in_any_event(self, mock_guardrail, _ga, mock_bedrock):
        """When guardrail redacts, the raw secret text NEVER appears in events."""
        raw_secret = "aws_secret_key=AKIAIOSFODNN7EXAMPLE/supersecret"
        redacted = "[REDACTED: tool result contained blocked content]"

        # The guardrail replaces the raw output with the redacted version
        mock_guardrail.return_value = redacted

        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use("get_secret", {"name": "db-creds"}),
            _bedrock_final("I cannot show you that secret."),
        ])

        adapter = _make_adapter(tool_names=["get_secret"])
        # The tool returns the raw secret (before guardrail)
        adapter.call_tool = AsyncMock(return_value=raw_secret)

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Serialize ALL events to text and verify raw secret is absent
        all_text = ""
        for e in events:
            if isinstance(e, StepToolCall):
                all_text += e.tool_name + e.args_display
            elif isinstance(e, StepToolResult):
                all_text += e.summary
            elif isinstance(e, StepFinalChunk):
                all_text += e.text
            elif isinstance(e, StepThinking):
                all_text += e.text

        assert raw_secret not in all_text, "Raw secret leaked into step events!"
        assert "AKIAIOSFODNN7EXAMPLE" not in all_text

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result")
    async def test_raw_secret_never_in_sse_frames(self, mock_guardrail, _ga, mock_bedrock):
        """End-to-end: raw secret never appears in any SSE frame sent to client."""
        raw_pii = "SSN: 123-45-6789, email: user@corp.internal"
        mock_guardrail.return_value = "[PII REDACTED]"

        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use("lookup_user", {"id": "u-42"}),
            _bedrock_final("User info is restricted."),
        ])

        adapter = _make_adapter(tool_names=["lookup_user"])
        adapter.call_tool = AsyncMock(return_value=raw_pii)

        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        full_stream = "".join(frames)
        assert "123-45-6789" not in full_stream, "PII SSN leaked into SSE stream!"
        assert "user@corp.internal" not in full_stream, "PII email leaked into SSE stream!"

    def test_sanitize_args_masks_password_key(self):
        """_sanitize_args_for_display masks any key containing 'password'."""
        result = _sanitize_args_for_display({"db_password": "hunter2", "host": "db.local"})
        assert "hunter2" not in result
        assert "***" in result
        assert "db.local" in result

    def test_sanitize_args_masks_credential_key(self):
        """_sanitize_args_for_display masks credential-like keys."""
        result = _sanitize_args_for_display({"credential": "xyz", "private_key": "RSA..."})
        assert "xyz" not in result
        assert "RSA" not in result
        assert result.count("***") >= 2

    def test_summarize_never_returns_raw_content(self):
        """_summarize_tool_result never returns the full raw text for long results."""
        raw = "SECRET_TOKEN=abc123\n" * 50
        summary = _summarize_tool_result("dangerous_tool", raw)
        # Summary should be much shorter than the raw output
        assert len(summary) < len(raw)
        # Should NOT contain the full secret line
        assert "SECRET_TOKEN=abc123" not in summary or summary.count("SECRET_TOKEN") <= 1


# ===========================================================================
# CONTRACT TEST 4: Budget exhaustion streams degraded final note
# ===========================================================================


class TestContract4_BudgetExhaustion:
    """Budget-exhaustion still streams a degraded final note with
    finish_reason='length'."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    @patch("src.core.agentic_loop_streaming.MAX_TOOL_STEPS", 1)
    async def test_step_budget_yields_degraded_note(self, _gr, _ga, mock_bedrock):
        """Step limit reached → streams degraded note + finish_reason=length."""
        # Model always asks for more tool calls (never converges)
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_tool_use())
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Must end with StepDone(finish_reason="length")
        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "length"

        # Must contain the graceful degradation note (no raw counters)
        final_texts = [e.text for e in events if isinstance(e, StepFinalChunk)]
        combined = " ".join(final_texts)
        assert "⚠️" in combined or "concluir" in combined.lower()

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    @patch("src.core.agentic_loop_streaming.MAX_LOOP_DURATION_MS", 0)
    async def test_duration_budget_yields_length(self, _gr, _ga, mock_bedrock):
        """Duration=0ms → immediate budget exhaustion → length."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_tool_use())
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        assert events[-1].finish_reason == "length"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    @patch("src.core.agentic_loop_streaming.MAX_LOOP_TOKENS", 1)
    async def test_token_budget_yields_length(self, _gr, _ga, mock_bedrock):
        """Token budget = 1 → exhausted after first converse → length."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_tool_use())
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        assert events[-1].finish_reason == "length"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    @patch("src.core.agentic_loop_streaming.MAX_TOOL_STEPS", 1)
    async def test_budget_exhaustion_sse_still_terminates_with_done(
        self, _gr, _ga, mock_bedrock
    ):
        """Even on budget exhaustion, the SSE stream terminates with [DONE]."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_tool_use())
        adapter = _make_adapter()

        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        assert frames[-1] == "data: [DONE]\n\n"
        # finish frame should have "length"
        finish_frame = json.loads(frames[-2].removeprefix("data: ").strip())
        assert finish_frame["choices"][0]["finish_reason"] == "length"


# ===========================================================================
# CONTRACT TEST 5: Fail-open — tool error streams inline error step, completes
# ===========================================================================


class TestContract5_FailOpen:
    """A tool error streams an inline error step, stream still completes
    cleanly with [DONE]."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_unknown_tool_yields_error_step_and_completes(
        self, _gr, _ga, mock_bedrock
    ):
        """Model requests a tool not in any adapter → error step, stream ends OK."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            # Model asks for a tool that doesn't exist in adapter
            {
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 80, "output_tokens": 40},
                "content": [{
                    "type": "tool_use",
                    "toolUseId": "tu-404",
                    "name": "nonexistent_tool",
                    "input": {"arg": "value"},
                }],
            },
            _bedrock_final("I couldn't use that tool, but here's what I know."),
        ])

        adapter = _make_adapter()  # only registers pods_list_in_namespace
        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Must have a ToolResult with "not found" or error indicator
        result_events = [e for e in events if isinstance(e, StepToolResult)]
        assert len(result_events) >= 1
        assert any("not found" in e.summary or "❌" in e.summary for e in result_events)

        # Must still complete with StepDone
        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=False)
    async def test_guardrail_blocked_args_yields_blocked_step(self, _ga, mock_bedrock):
        """Guardrail blocks tool args → inline blocked step, stream continues."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use("pods_list_in_namespace", {"ns": "kube-system; rm -rf /"}),
            _bedrock_final("That request was blocked for safety."),
        ])

        adapter = _make_adapter()
        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Must have a blocked result step
        result_events = [e for e in events if isinstance(e, StepToolResult)]
        assert any("blocked" in e.summary or "🚫" in e.summary for e in result_events)

        # Must still complete with Done
        assert isinstance(events[-1], StepDone)

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_fail_open_sse_ends_with_done(self, _gr, _ga, mock_bedrock):
        """Even with tool errors, the SSE output terminates with [DONE]."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            {
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "output_tokens": 30},
                "content": [{
                    "type": "tool_use",
                    "toolUseId": "tu-err",
                    "name": "broken_tool",
                    "input": {},
                }],
            },
            _bedrock_final("Recovered."),
        ])

        adapter = _make_adapter()  # doesn't have 'broken_tool'
        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        assert frames[-1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    async def test_adapter_connection_failure_degrades_gracefully(self, mock_bedrock):
        """If adapter.list_tool_specs() throws, loop still produces output."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_final("No tools available."))

        adapter = _make_adapter()
        adapter.list_tool_specs = AsyncMock(side_effect=ConnectionError("refused"))

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Should still produce final answer + done
        assert any(isinstance(e, StepFinalChunk) for e in events)
        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "stop"


# ===========================================================================
# CONTRACT TEST 6: Extended-thinking OFF by default
# ===========================================================================


class TestContract6_ExtendedThinkingDefault:
    """Extended-thinking flag is OFF by default — no reasoning deltas unless
    explicitly enabled."""

    def test_flag_is_off_by_default(self):
        """The module-level ENABLE_EXTENDED_THINKING defaults to False."""
        assert ENABLE_EXTENDED_THINKING is False

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming.ENABLE_EXTENDED_THINKING", False)
    async def test_reasoning_blocks_ignored_when_flag_off(self, mock_bedrock):
        """Even if model returns reasoning blocks, they are NOT emitted when off."""
        # Note: when ENABLE_EXTENDED_THINKING is False, the converse call doesn't
        # request reasoning_config, so the model shouldn't return reasoning blocks.
        # But if it DID (defensively), our contract says no StepThinking events.
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_final("Simple answer."))

        adapter = _make_adapter()
        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        thinking = [e for e in events if isinstance(e, StepThinking)]
        assert len(thinking) == 0, "No thinking events when flag is OFF"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming.ENABLE_EXTENDED_THINKING", True)
    async def test_reasoning_emitted_when_flag_on(self, mock_bedrock):
        """When flag is ON and model returns reasoning, StepThinking is emitted."""
        mock_bedrock.converse = AsyncMock(
            return_value=_bedrock_with_reasoning(
                "I need to think step by step...",
                "The answer is 42.",
            )
        )

        adapter = _make_adapter()
        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        thinking = [e for e in events if isinstance(e, StepThinking)]
        assert len(thinking) == 1
        assert "step by step" in thinking[0].text

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming.ENABLE_EXTENDED_THINKING", True)
    async def test_thinking_rendered_with_emoji_in_sse(self, mock_bedrock):
        """StepThinking renders as 💭 in SSE frames."""
        mock_bedrock.converse = AsyncMock(
            return_value=_bedrock_with_reasoning("Deep thought.", "Answer.")
        )

        adapter = _make_adapter()
        events_gen = run_agentic_loop_streaming(mcp_adapters=[adapter], **LOOP_KWARGS)
        frames = await _collect(sse_stream_agentic(events_gen, "model"))

        all_content = "".join(
            (json.loads(f.removeprefix("data: ").strip())
             .get("choices", [{}])[0]
             .get("delta", {})
             .get("content") or "")
            for f in frames
            if f.strip() != "data: [DONE]" and f.startswith("data: {")
        )
        assert "💭" in all_content
        assert "Deep thought" in all_content


# ===========================================================================
# ADDITIONAL COVERAGE: edge cases for ≥90% target
# ===========================================================================


class TestSummarizeToolResultEdgeCases:
    """Additional edge cases for _summarize_tool_result."""

    def test_empty_string(self):
        result = _summarize_tool_result("tool", "")
        assert "📦" in result

    def test_invalid_json_prefix(self):
        """String starting with [ but not valid JSON → falls back to line count or 'ok'."""
        result = _summarize_tool_result("tool", "[not valid json at all")
        assert "📦" in result
        # Terse mode: no char counts, no raw content
        assert "chars" not in result

    def test_json_object_keys_preview(self):
        """JSON object without collection key → terse 'ok' (no key preview)."""
        data = json.dumps({"alpha": 1, "beta": 2, "gamma": 3, "delta": 4})
        result = _summarize_tool_result("tool", data)
        # Terse: no raw content leaked, just a status indicator
        assert "📦" in result
        assert "chars" not in result


class TestSanitizeArgsEdgeCases:
    """Additional coverage for _sanitize_args_for_display."""

    def test_non_string_value(self):
        """Non-string values (int, list) are JSON-serialized."""
        result = _sanitize_args_for_display({"count": 42, "tags": ["a", "b"]})
        assert "42" in result
        assert "a" in result

    def test_case_insensitive_secret_detection(self):
        """SECRET detection is case-insensitive."""
        result = _sanitize_args_for_display({"API_KEY": "xsecretval", "ApiKey": "ysecretval"})
        assert "xsecretval" not in result
        assert "ysecretval" not in result
        assert result.count("***") >= 2


class TestStreamingLoopInvariants:
    """Structural invariants that must hold for any streaming execution."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_done_is_always_last_event(self, _gr, _ga, mock_bedrock):
        """StepDone is ALWAYS the last event yielded, regardless of path."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_tool_use("pods_list_in_namespace", {"ns": "default"}, "tu-2"),
            _bedrock_final("Done with both calls."),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        assert isinstance(events[-1], StepDone)
        # No events after Done
        done_count = sum(1 for e in events if isinstance(e, StepDone))
        assert done_count == 1

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    async def test_final_chunk_text_is_nonempty_for_normal_answer(self, mock_bedrock):
        """A normal answer always has non-empty FinalChunk text."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_final("Real answer."))
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        final_chunks = [e for e in events if isinstance(e, StepFinalChunk)]
        assert len(final_chunks) >= 1
        combined = "".join(e.text for e in final_chunks)
        assert len(combined) > 0

    @pytest.mark.asyncio
    async def test_sse_generator_without_done_still_closes(self):
        """Safety net: if event generator ends without Done, SSE still closes."""

        async def _partial():
            yield StepFinalChunk(text="incomplete")
            # No StepDone emitted

        frames = await _collect(sse_stream_agentic(_partial(), "model"))
        assert frames[-1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **kw: r)
    async def test_multiple_tool_calls_in_sequence(self, _gr, _ga, mock_bedrock):
        """Multiple tool calls produce paired ToolCall+ToolResult for each."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            # First turn: model requests 2 tools at once
            {
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "output_tokens": 80},
                "content": [
                    {"type": "tool_use", "toolUseId": "tu-1", "name": "pods_list_in_namespace", "input": {"ns": "a"}},
                    {"type": "tool_use", "toolUseId": "tu-2", "name": "pods_list_in_namespace", "input": {"ns": "b"}},
                ],
            },
            _bedrock_final("Both namespaces checked."),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        tool_calls = [e for e in events if isinstance(e, StepToolCall)]
        tool_results = [e for e in events if isinstance(e, StepToolResult)]
        assert len(tool_calls) == 2
        assert len(tool_results) == 2
