"""Tests for Phase 3.5 streaming — agentic loop SSE step events.

Verifies:
  - S1: run_agentic_loop_streaming yields step events as async generator
  - S2: correct step event types (StepToolCall, StepToolResult, StepFinalChunk, StepDone)
  - S4: tool args are sanitized (secrets masked); results pass guardrail
  - SSE framing: sse_stream_agentic emits valid OpenAI chat.completion.chunk
  - Non-streaming path (Phase 3) still works unchanged
  - Pseudo-streaming fallback preserved

Target: ≥90% coverage of agentic_loop_streaming.py and the sse_stream_agentic encoder.
"""
from __future__ import annotations

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.agentic_loop_streaming import (
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
    sse_stream,
    sse_stream_agentic,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_adapter(name="kube-mcp", tools=None):
    """Create a mock MCP adapter with tool specs."""
    adapter = AsyncMock()
    adapter.name = name
    adapter.url = f"http://{name}:8080"
    adapter.tools = tools or ["pods_list_in_namespace"]
    adapter.list_tool_specs = AsyncMock(return_value=[
        {
            "toolSpec": {
                "name": "pods_list_in_namespace",
                "description": "List pods in a namespace",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"namespace": {"type": "string"}},
                    }
                },
            }
        }
    ])
    adapter.call_tool = AsyncMock(return_value='[{"name": "pod-1"}, {"name": "pod-2"}]')
    return adapter


def _converse_tool_use_response(tool_name="pods_list_in_namespace", args=None):
    """Mock a Converse response with tool_use stop reason."""
    return {
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "content": [
            {
                "type": "tool_use",
                "toolUseId": "tu-123",
                "name": tool_name,
                "input": args or {"namespace": "monitoring"},
            }
        ],
    }


def _converse_final_response(text="There are 2 pods in monitoring."):
    """Mock a Converse final answer response."""
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 200, "output_tokens": 80},
        "content": [{"type": "text", "text": text}],
    }


# ---------------------------------------------------------------------------
# Unit: _sanitize_args_for_display (S4)
# ---------------------------------------------------------------------------


class TestSanitizeArgsForDisplay:

    def test_empty_args(self):
        assert _sanitize_args_for_display({}) == ""

    def test_normal_args(self):
        result = _sanitize_args_for_display({"namespace": "monitoring"})
        assert "namespace='monitoring'" in result

    def test_secret_key_masked(self):
        result = _sanitize_args_for_display({"api_key": "sk-12345"})
        assert '***' in result
        assert "sk-12345" not in result

    def test_password_masked(self):
        result = _sanitize_args_for_display({"password": "hunter2"})
        assert '***' in result
        assert "hunter2" not in result

    def test_token_masked(self):
        result = _sanitize_args_for_display({"auth_token": "bearer-xyz"})
        assert '***' in result
        assert "bearer-xyz" not in result

    def test_long_value_truncated(self):
        long_val = "x" * 100
        result = _sanitize_args_for_display({"data": long_val})
        assert "..." in result
        assert len(result) < 100

    def test_mixed_args(self):
        result = _sanitize_args_for_display({
            "namespace": "prod",
            "secret_key": "abc123",
        })
        assert "prod" in result
        assert "abc123" not in result
        assert "***" in result


# ---------------------------------------------------------------------------
# Unit: _summarize_tool_result (S4)
# ---------------------------------------------------------------------------


class TestSummarizeToolResult:

    def test_json_array(self):
        data = json.dumps([{"name": f"pod-{i}"} for i in range(10)])
        result = _summarize_tool_result("pods_list", data)
        assert "10 items" in result
        assert "📦" in result

    def test_json_object(self):
        data = json.dumps({"status": "ok", "count": 5, "data": []})
        result = _summarize_tool_result("get_status", data)
        # Object with empty "data" list → count_items returns None → "📦 ok"
        assert "📦" in result
        # Terse: never emits raw JSON content or char counts
        assert "chars" not in result

    def test_short_text(self):
        result = _summarize_tool_result("simple_tool", "ok")
        assert "ok" in result
        assert "📦" in result

    def test_multiline_text(self):
        text = "\n".join([f"line {i}" for i in range(20)])
        result = _summarize_tool_result("long_output", text)
        # count_items detects 20 non-header lines → "~20 items"
        assert "20" in result
        assert "📦" in result

    def test_long_single_line(self):
        text = "x" * 200
        result = _summarize_tool_result("big_result", text)
        # Terse mode: single line → "📦 ok", never shows char count
        assert "📦" in result
        assert "chars" not in result


# ---------------------------------------------------------------------------
# Integration: run_agentic_loop_streaming (S1/S2)
# ---------------------------------------------------------------------------


class TestAgenticLoopStreaming:

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **k: r)
    async def test_single_tool_call_yields_expected_events(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """A single tool call yields: ToolCall → ToolResult → FinalChunk → Done."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _converse_tool_use_response(),
            _converse_final_response("There are 2 pods."),
        ])

        adapter = _mock_adapter()
        events: list[AgenticStepEvent] = []

        async for event in run_agentic_loop_streaming(
            query="list pods in monitoring",
            system_prompt="You are helpful.",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="kubernetes",
            user_id="test-user",
            session_id="test-session",
        ):
            events.append(event)

        # Verify event sequence
        assert any(isinstance(e, StepToolCall) for e in events)
        assert any(isinstance(e, StepToolResult) for e in events)
        assert any(isinstance(e, StepFinalChunk) for e in events)
        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "stop"

        # Verify tool call content
        tool_call = next(e for e in events if isinstance(e, StepToolCall))
        assert tool_call.tool_name == "pods_list_in_namespace"
        assert "monitoring" in tool_call.args_display

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    async def test_no_tools_yields_final_directly(self, mock_bedrock):
        """When the model answers without tools, yield final chunks directly."""
        mock_bedrock.converse = AsyncMock(return_value=_converse_final_response("Direct answer."))

        adapter = _mock_adapter()
        events: list[AgenticStepEvent] = []

        async for event in run_agentic_loop_streaming(
            query="hello",
            system_prompt="You are helpful.",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="kubernetes",
            user_id="test-user",
            session_id="test-session",
        ):
            events.append(event)

        # No tool events, just final + done
        assert not any(isinstance(e, StepToolCall) for e in events)
        assert any(isinstance(e, StepFinalChunk) for e in events)
        assert isinstance(events[-1], StepDone)
        final_text = "".join(e.text for e in events if isinstance(e, StepFinalChunk))
        assert "Direct answer" in final_text

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=False)
    async def test_guardrail_blocked_args_yields_blocked_result(
        self, mock_args_guard, mock_bedrock
    ):
        """When guardrail blocks tool args, yield a blocked result event."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _converse_tool_use_response(),
            _converse_final_response("Could not complete."),
        ])

        adapter = _mock_adapter()
        events: list[AgenticStepEvent] = []

        async for event in run_agentic_loop_streaming(
            query="hack the system",
            system_prompt="You are helpful.",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="kubernetes",
            user_id="test-user",
            session_id="test-session",
        ):
            events.append(event)

        # Should have a ToolResult with blocked message
        result_events = [e for e in events if isinstance(e, StepToolResult)]
        assert any("blocked" in e.summary or "🚫" in e.summary for e in result_events)

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **k: r)
    @patch("src.core.agentic_loop_streaming.MAX_TOOL_STEPS", 1)
    async def test_budget_exhaustion_yields_length_done(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """When step budget exhausts, yield a degraded answer with finish_reason=length."""
        # Always return tool_use (never converge)
        mock_bedrock.converse = AsyncMock(return_value=_converse_tool_use_response())

        adapter = _mock_adapter()
        events: list[AgenticStepEvent] = []

        async for event in run_agentic_loop_streaming(
            query="infinite loop query",
            system_prompt="You are helpful.",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="kubernetes",
            user_id="test-user",
            session_id="test-session",
        ):
            events.append(event)

        # Should end with StepDone finish_reason="length"
        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "length"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result")
    async def test_result_redaction_passes_through(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """S4: guardrail-scanned result shows in summary, not raw tool output."""
        mock_result_guard.return_value = "[tool result redacted by guardrail — contains blocked content]"
        mock_bedrock.converse = AsyncMock(side_effect=[
            _converse_tool_use_response(),
            _converse_final_response("Done."),
        ])

        adapter = _mock_adapter()
        events: list[AgenticStepEvent] = []

        async for event in run_agentic_loop_streaming(
            query="show secrets",
            system_prompt="You are helpful.",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="kubernetes",
            user_id="test-user",
            session_id="test-session",
        ):
            events.append(event)

        # The raw tool output should never appear; the summary uses the redacted text
        result_events = [e for e in events if isinstance(e, StepToolResult)]
        assert len(result_events) == 1
        # The summary is derived from the redacted result
        assert "redacted" in result_events[0].summary or "📦" in result_events[0].summary


# ---------------------------------------------------------------------------
# Integration: sse_stream_agentic (SSE framing)
# ---------------------------------------------------------------------------


class TestSseStreamAgentic:

    @pytest.mark.asyncio
    async def test_emits_valid_openai_sse_frames(self):
        """sse_stream_agentic emits proper OpenAI SSE format."""

        async def _fake_events():
            yield StepToolCall(tool_name="pods_list", args_display='namespace="monitoring"')
            yield StepToolResult(tool_name="pods_list", summary="📦 263 items (4000 chars)")
            yield StepFinalChunk(text="There are 263 pods in monitoring.")
            yield StepDone(finish_reason="stop")

        frames: list[str] = []
        async for frame in sse_stream_agentic(_fake_events(), "aigent-squad-kubernetes"):
            frames.append(frame)

        # Must start with role prelude
        assert frames[0].startswith("data: ")
        prelude = json.loads(frames[0].removeprefix("data: ").strip())
        assert prelude["choices"][0]["delta"]["role"] == "assistant"
        assert prelude["object"] == "chat.completion.chunk"

        # Must end with [DONE]
        assert frames[-1] == "data: [DONE]\n\n"

        # Second-to-last should have finish_reason
        finish_frame = json.loads(frames[-2].removeprefix("data: ").strip())
        assert finish_frame["choices"][0]["finish_reason"] == "stop"

        # All intermediate frames should be valid JSON with content
        for frame in frames[1:-2]:
            if frame.strip() == "":
                continue
            data = json.loads(frame.removeprefix("data: ").strip())
            assert "choices" in data
            content = data["choices"][0]["delta"].get("content", "")
            assert content  # non-empty content deltas

    @pytest.mark.asyncio
    async def test_tool_call_emoji_in_stream(self):
        """StepToolCall renders with 🔧 emoji."""

        async def _events():
            yield StepToolCall(tool_name="get_pods", args_display='ns="default"')
            yield StepDone()

        frames = []
        async for frame in sse_stream_agentic(_events(), "test-model"):
            frames.append(frame)

        # Find the tool call frame (exclude the <details> summary wrapper)
        tool_frames = [
            f for f in frames
            if "🔧" in f and "get_pods" in f
        ]
        assert len(tool_frames) == 1
        assert "get_pods" in tool_frames[0]

    @pytest.mark.asyncio
    async def test_thinking_event_rendered(self):
        """StepThinking renders with 💭 emoji."""

        async def _events():
            yield StepThinking(text="I should check the pods first.")
            yield StepDone()

        frames = []
        async for frame in sse_stream_agentic(_events(), "test-model"):
            frames.append(frame)

        thinking_frames = [f for f in frames if "💭" in f]
        assert len(thinking_frames) == 1
        assert "check the pods" in thinking_frames[0]

    @pytest.mark.asyncio
    async def test_all_frames_have_same_id(self):
        """All chunks in a stream share the same completion id."""

        async def _events():
            yield StepFinalChunk(text="hello")
            yield StepDone()

        frames = []
        async for frame in sse_stream_agentic(_events(), "model"):
            frames.append(frame)

        ids = set()
        for f in frames:
            if f.startswith("data: {"):
                data = json.loads(f.removeprefix("data: ").strip())
                ids.add(data.get("id"))
        # All frames have the same id (one completion)
        assert len(ids) == 1


# ---------------------------------------------------------------------------
# Regression: non-streaming sse_stream still works (pseudo-streaming fallback)
# ---------------------------------------------------------------------------


class TestPseudoStreamingPreserved:

    @pytest.mark.asyncio
    async def test_sse_stream_emits_prelude_content_finish_done(self):
        """The legacy sse_stream (pseudo-streaming) still works for non-agentic."""
        result = {"response": "Hello world"}
        frames = []
        async for frame in sse_stream(result, "aigent-squad"):
            frames.append(frame)

        assert len(frames) == 4  # prelude + content + finish + [DONE]
        assert frames[-1] == "data: [DONE]\n\n"

        # Content frame has the full response
        content_frame = json.loads(frames[1].removeprefix("data: ").strip())
        assert content_frame["choices"][0]["delta"]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_sse_stream_empty_response(self):
        """sse_stream handles empty responses gracefully."""
        result = {"response": ""}
        frames = []
        async for frame in sse_stream(result, "aigent-squad"):
            frames.append(frame)

        # prelude + finish + [DONE] (no content frame for empty)
        assert len(frames) == 3
        assert frames[-1] == "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestStreamingEdgeCases:

    @pytest.mark.asyncio
    async def test_generator_without_done_still_terminates(self):
        """Safety: if generator ends without StepDone, SSE still closes cleanly."""

        async def _events():
            yield StepFinalChunk(text="partial")
            # No StepDone — generator just ends

        frames = []
        async for frame in sse_stream_agentic(_events(), "model"):
            frames.append(frame)

        # Must still end with [DONE]
        assert frames[-1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    async def test_empty_generator(self):
        """Empty generator still produces prelude + finish + DONE."""

        async def _events():
            yield StepDone()

        frames = []
        async for frame in sse_stream_agentic(_events(), "model"):
            frames.append(frame)

        assert frames[0].startswith("data: ")  # prelude
        assert frames[-1] == "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Additional coverage: budget exhaustion paths, adapter errors, S3 flag
# ---------------------------------------------------------------------------


class TestStreamingBudgetPaths:

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **k: r)
    @patch("src.core.agentic_loop_streaming.MAX_LOOP_DURATION_MS", 0)
    async def test_max_duration_budget_yields_length(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """MAX_LOOP_DURATION_MS=0 → immediate budget exhaustion → finish_reason=length."""
        mock_bedrock.converse = AsyncMock(return_value=_converse_tool_use_response())

        adapter = _mock_adapter()
        events: list = []
        async for event in run_agentic_loop_streaming(
            query="test",
            system_prompt="test",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="test",
            user_id="u",
            session_id="s",
        ):
            events.append(event)

        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "length"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **k: r)
    @patch("src.core.agentic_loop_streaming.MAX_LOOP_TOKENS", 1)
    async def test_max_tokens_budget_yields_length(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """MAX_LOOP_TOKENS=1 → token budget exhausted after first call."""
        mock_bedrock.converse = AsyncMock(return_value=_converse_tool_use_response())

        adapter = _mock_adapter()
        events: list = []
        async for event in run_agentic_loop_streaming(
            query="test",
            system_prompt="test",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="test",
            user_id="u",
            session_id="s",
        ):
            events.append(event)

        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "length"
        # Should contain the degraded note in a FinalChunk
        final_chunks = [e for e in events if isinstance(e, StepFinalChunk)]
        assert any("⚠️" in e.text or "concluir" in e.text for e in final_chunks)


class TestStreamingAdapterErrors:

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    async def test_adapter_list_tools_failure_degraded(self, mock_bedrock):
        """If an adapter's list_tool_specs fails, the loop continues without it."""
        mock_bedrock.converse = AsyncMock(return_value=_converse_final_response("no tools available"))

        adapter = _mock_adapter()
        adapter.list_tool_specs = AsyncMock(side_effect=RuntimeError("connect failed"))

        events: list = []
        async for event in run_agentic_loop_streaming(
            query="test",
            system_prompt="test",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="test",
            user_id="u",
            session_id="s",
        ):
            events.append(event)

        # Should still get a final answer (degraded, no tools)
        assert any(isinstance(e, StepFinalChunk) for e in events)
        assert isinstance(events[-1], StepDone)

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result", side_effect=lambda r, *a, **k: r)
    async def test_tool_not_in_router_yields_error_result(
        self, mock_result_guard, mock_args_guard, mock_bedrock
    ):
        """Tool not found in any adapter → yields error StepToolResult."""
        # Tool name differs from what adapter registered
        mock_bedrock.converse = AsyncMock(side_effect=[
            {
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "output_tokens": 30},
                "content": [{
                    "type": "tool_use",
                    "toolUseId": "tu-999",
                    "name": "unknown_tool",
                    "input": {},
                }],
            },
            _converse_final_response("fallback answer"),
        ])

        adapter = _mock_adapter()
        events: list = []
        async for event in run_agentic_loop_streaming(
            query="test",
            system_prompt="test",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="test",
            user_id="u",
            session_id="s",
        ):
            events.append(event)

        result_events = [e for e in events if isinstance(e, StepToolResult)]
        assert any("not found" in e.summary for e in result_events)


class TestExtendedThinkingFlag:

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming.ENABLE_EXTENDED_THINKING", True)
    async def test_thinking_blocks_emitted_when_flag_on(self, mock_bedrock):
        """S3: when extended-thinking flag is ON and model returns reasoning blocks."""
        mock_bedrock.converse = AsyncMock(return_value={
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 80},
            "content": [
                {"type": "reasoning", "text": "Let me think about this..."},
                {"type": "text", "text": "The answer is 42."},
            ],
        })

        adapter = _mock_adapter()
        events: list = []
        async for event in run_agentic_loop_streaming(
            query="what is the meaning?",
            system_prompt="test",
            history_text="",
            mcp_adapters=[adapter],
            agent_id="test",
            user_id="u",
            session_id="s",
        ):
            events.append(event)

        # Should have a StepThinking event
        thinking_events = [e for e in events if isinstance(e, StepThinking)]
        assert len(thinking_events) == 1
        assert "think about this" in thinking_events[0].text


class TestSseEncoderFallback:

    @pytest.mark.asyncio
    async def test_build_completion_non_streaming_unchanged(self):
        """Non-streaming build_completion still works (Phase 3 path)."""
        from src.supervisor.openai_compat import build_completion
        result = {"response": "test answer"}
        resp = build_completion(result, "aigent-squad")
        assert resp.choices[0].message.content == "test answer"
        assert resp.choices[0].finish_reason == "stop"
        assert resp.object == "chat.completion"
