"""Independent verification tests for spec-37 streaming defect fixes.

Written by test-author (INDEPENDENT of implementation author) against the
CONTRACT/spec, not the implementation internals.

Tests two defect fixes:
  DEFECT A — Streaming loop yields StepToolCall + StepToolResult events
  DEFECT B — Truncation marker correctly counts JSON object items

Runs via: docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
  "pip install -e '.[dev]' -q && pytest tests/test_spec37_independent_verification.py -v"
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.core.agent_config import MAX_TOOL_RESULT_CHARS
from src.core.agentic_loop import _truncate_with_marker
from src.core.agentic_loop_streaming import (
    StepDone,
    StepFinalChunk,
    StepToolCall,
    StepToolResult,
    run_agentic_loop_streaming,
)
from src.supervisor.openai_compat import sse_stream_agentic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _adapter(name="kube-mcp", tools=None, result=None):
    """Factory for mock MCP adapter."""
    tools = tools or ["get_pods"]
    a = AsyncMock()
    a.name = name
    a.url = f"http://{name}:8080"
    a.tools = tools
    a.list_tool_specs = AsyncMock(return_value=[
        {"toolSpec": {"name": t, "description": f"desc-{t}",
                      "inputSchema": {"json": {"type": "object", "properties": {"ns": {"type": "string"}}}}}}
        for t in tools
    ])
    a.call_tool = AsyncMock(return_value=result or '{"status":"ok"}')
    return a


def _tool_use_resp(tool="get_pods", args=None, tid="t1"):
    return {
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 50, "output_tokens": 30},
        "content": [{"type": "tool_use", "toolUseId": tid, "name": tool,
                     "input": args or {"ns": "default"}}],
    }


def _end_turn(text="Done."):
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 80, "output_tokens": 40},
        "content": [{"type": "text", "text": text}],
    }


async def _drain(gen) -> list:
    """Consume async generator."""
    out = []
    async for item in gen:
        out.append(item)
    return out


_LOOP_KW = dict(
    query="show pods",
    system_prompt="You are helpful.",
    history_text="",
    agent_id="k8s",
    user_id="u1",
    session_id="s1",
)


def _k8s_items_json(n: int) -> str:
    """Build a kube-mcp-style {"items": [...n...]} response exceeding 8000 chars."""
    pods = [{"metadata": {"name": f"pod-{i:04d}", "namespace": "ns",
             "uid": f"00000000-0000-0000-0000-{i:012d}"},
             "status": {"phase": "Running"}} for i in range(n)]
    return json.dumps({"kind": "PodList", "apiVersion": "v1", "items": pods, "metadata": {"resourceVersion": "999"}})


# ===========================================================================
# (1) Drive streaming loop generator directly — assert yielded event sequence
# ===========================================================================


class TestLoopYieldsToolEvents:
    """DEFECT A: The loop MUST yield StepToolCall (with tool name + sanitized
    args) AND StepToolResult (with redacted summary) BEFORE StepFinalChunk(s).
    Tests the LOOP directly — no SSE encoder in between."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_tool_call_event_contains_tool_name(self, _gr, _ga, mock_br):
        """StepToolCall event carries the correct tool_name."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn()])
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[_adapter()], **_LOOP_KW))

        tool_calls = [e for e in events if isinstance(e, StepToolCall)]
        assert len(tool_calls) >= 1
        assert tool_calls[0].tool_name == "get_pods"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_tool_call_event_has_sanitized_args(self, _gr, _ga, mock_br):
        """StepToolCall.args_display contains arg values but hides secrets."""
        mock_br.converse = AsyncMock(side_effect=[
            _tool_use_resp(args={"ns": "monitoring", "token": "SECRET"}),
            _end_turn(),
        ])
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[_adapter()], **_LOOP_KW))

        tc = [e for e in events if isinstance(e, StepToolCall)][0]
        assert "monitoring" in tc.args_display
        assert "SECRET" not in tc.args_display
        assert "***" in tc.args_display

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_tool_result_event_has_summary(self, _gr, _ga, mock_br):
        """StepToolResult event has a non-empty summary (never raw output)."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn()])
        adapter = _adapter(result='[{"pod":"a"},{"pod":"b"}]')
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **_LOOP_KW))

        results = [e for e in events if isinstance(e, StepToolResult)]
        assert len(results) >= 1
        assert results[0].summary  # non-empty
        assert "📦" in results[0].summary

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_ordering_tc_before_tr_before_final(self, _gr, _ga, mock_br):
        """StepToolCall appears before StepToolResult which appears before StepFinalChunk."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn()])
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[_adapter()], **_LOOP_KW))

        types = [type(e).__name__ for e in events]
        tc_i = types.index("StepToolCall")
        tr_i = types.index("StepToolResult")
        fc_i = types.index("StepFinalChunk")
        assert tc_i < tr_i < fc_i, f"Order wrong: TC@{tc_i} TR@{tr_i} FC@{fc_i}"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_step_done_is_last(self, _gr, _ga, mock_br):
        """StepDone is the terminal event."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn()])
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[_adapter()], **_LOOP_KW))

        assert isinstance(events[-1], StepDone)
        assert events[-1].finish_reason == "stop"


# ===========================================================================
# (2) End-to-end through sse_stream_agentic — SSE string assertions
# ===========================================================================


class TestSseStreamAgenticOutput:
    """DEFECT A e2e: the resulting SSE text MUST contain a 🔧 tool-call delta
    and a 📦 result delta, then the answer, then [DONE]."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_sse_contains_tool_call_emoji(self, _gr, _ga, mock_br):
        """SSE output contains 🔧 in a content delta (tool call step)."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn("answer")])
        gen = run_agentic_loop_streaming(mcp_adapters=[_adapter()], **_LOOP_KW)
        frames = []
        async for frame in sse_stream_agentic(gen, "aigent-squad"):
            frames.append(frame)

        joined = "".join(frames)
        assert "🔧" in joined
        assert "get_pods" in joined

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_sse_contains_result_emoji(self, _gr, _ga, mock_br):
        """SSE output contains 📦 in a content delta (tool result step)."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn("answer")])
        gen = run_agentic_loop_streaming(mcp_adapters=[_adapter()], **_LOOP_KW)
        frames = []
        async for frame in sse_stream_agentic(gen, "aigent-squad"):
            frames.append(frame)

        joined = "".join(frames)
        assert "📦" in joined

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_sse_ends_with_done(self, _gr, _ga, mock_br):
        """SSE output ends with 'data: [DONE]\\n\\n'."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn("ok")])
        gen = run_agentic_loop_streaming(mcp_adapters=[_adapter()], **_LOOP_KW)
        frames = []
        async for frame in sse_stream_agentic(gen, "aigent-squad"):
            frames.append(frame)

        assert frames[-1] == "data: [DONE]\n\n"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_sse_answer_appears_after_tool_steps(self, _gr, _ga, mock_br):
        """The final answer text appears in SSE AFTER the tool-call/result lines."""
        answer_text = "There are 5 pods running."
        mock_br.converse = AsyncMock(side_effect=[
            _tool_use_resp(), _end_turn(answer_text)])
        gen = run_agentic_loop_streaming(mcp_adapters=[_adapter()], **_LOOP_KW)
        frames = []
        async for frame in sse_stream_agentic(gen, "aigent-squad"):
            frames.append(frame)

        joined = "".join(frames)
        tool_pos = joined.index("🔧")
        answer_pos = joined.index(answer_text)
        assert tool_pos < answer_pos

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_sse_frames_are_valid_sse_format(self, _gr, _ga, mock_br):
        """Every SSE frame starts with 'data: ' and ends with '\\n\\n'."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn("x")])
        gen = run_agentic_loop_streaming(mcp_adapters=[_adapter()], **_LOOP_KW)
        frames = []
        async for frame in sse_stream_agentic(gen, "aigent-squad"):
            frames.append(frame)

        for frame in frames:
            assert frame.startswith("data: "), f"Bad frame: {frame[:40]}"
            assert frame.endswith("\n\n"), f"Missing trailing newlines: {frame[-10:]}"


# ===========================================================================
# (3) Truncation marker on k8s JSON OBJECT {"items":[...263...]} reports 263
# ===========================================================================


class TestTruncationJsonObject:
    """DEFECT B: kube-mcp returns JSON objects like {"items":[...]} — the
    truncation marker must count the list inside 'items', not lines or chars."""

    def test_263_items_reports_263(self):
        """{"items": [<263 pods>]} → marker says '263 items total'."""
        data = _k8s_items_json(263)
        assert len(data) > MAX_TOOL_RESULT_CHARS
        result = _truncate_with_marker(data, MAX_TOOL_RESULT_CHARS)
        assert "263 items total" in result

    def test_50_nodes_reports_50(self):
        """{"nodes": [<50 items>]} uses the well-known 'nodes' key."""
        nodes = [{"metadata": {"name": f"node-{i}"}} for i in range(50)]
        data = json.dumps({"kind": "NodeList", "nodes": nodes})
        # Force truncation with small limit
        result = _truncate_with_marker(data, 200)
        assert "50 items total" in result

    def test_results_key_counted(self):
        """{"results": [<120 items>]} → 120."""
        data = json.dumps({"results": [{"id": i} for i in range(120)]})
        result = _truncate_with_marker(data, 200)
        assert "120 items total" in result

    def test_data_key_counted(self):
        """{"data": [<90 items>]} (VictoriaMetrics format) → 90."""
        data = json.dumps({"status": "success", "data": list(range(90))})
        result = _truncate_with_marker(data, 100)
        assert "90 items total" in result

    def test_pods_key_counted(self):
        """{"pods": [<30 items>]} → 30."""
        data = json.dumps({"pods": [{"name": f"p{i}"} for i in range(30)]})
        result = _truncate_with_marker(data, 100)
        assert "30 items total" in result

    def test_records_key_counted(self):
        """{"records": [<200 items>]} → 200."""
        data = json.dumps({"records": list(range(200))})
        result = _truncate_with_marker(data, 100)
        assert "200 items total" in result

    def test_unknown_key_largest_list(self):
        """Object with non-standard keys → picks the largest list."""
        data = json.dumps({"foo": [1, 2], "bar": list(range(75)), "meta": "x"})
        result = _truncate_with_marker(data, 100)
        assert "75 items total" in result

    def test_marker_not_263_lines(self):
        """Regression: must NOT report line count instead of item count."""
        data = _k8s_items_json(263)
        line_count = len(data.splitlines())
        result = _truncate_with_marker(data, MAX_TOOL_RESULT_CHARS)
        # The marker must say 263, not whatever the line count is
        assert "263 items total" in result
        if line_count != 263:
            assert f"{line_count} items total" not in result

    def test_after_redaction_truncation_sees_redacted(self):
        """If B3 redaction shrinks the result below limit, no marker appears."""
        short_redacted = "[REDACTED]"
        result = _truncate_with_marker(short_redacted, MAX_TOOL_RESULT_CHARS)
        assert result == short_redacted  # no truncation
        assert "[truncated:" not in result


# ===========================================================================
# (4) Plain JSON array + line-list still counted correctly (no regression)
# ===========================================================================


class TestTruncationArrayAndLines:
    """Existing strategies (JSON array, multi-line text) MUST still work."""

    def test_json_array_100_items(self):
        """Plain JSON array [item, item, ...] → count elements."""
        data = json.dumps([{"id": i, "val": "x" * 20} for i in range(100)])
        result = _truncate_with_marker(data, 500)
        assert "100 items total" in result

    def test_json_array_takes_priority_over_lines(self):
        """A valid JSON array is counted as array, not as lines."""
        arr = json.dumps(list(range(42)))
        result = _truncate_with_marker(arr, 50)
        assert "42 items total" in result

    def test_multiline_text_counts_lines(self):
        """Non-JSON multiline → line count."""
        text = "\n".join(f"line {i}: data here xxxx" for i in range(300))
        result = _truncate_with_marker(text, 500)
        assert "300 items total" in result

    def test_single_line_blob_reports_chars(self):
        """Single long non-JSON line → char count fallback."""
        blob = "a" * 15000
        result = _truncate_with_marker(blob, 8000)
        assert "15000 chars total" in result

    def test_empty_json_array_still_truncates(self):
        """Edge: large serialized empty-looking structure — lines fallback."""
        # An array of empty strings that's still big
        data = json.dumps([""] * 500)
        result = _truncate_with_marker(data, 100)
        assert "500 items total" in result

    def test_json_array_with_nested_objects(self):
        """Array of complex objects → count top-level elements."""
        items = [{"metadata": {"name": f"svc-{i}", "labels": {"app": "x"}},
                  "spec": {"ports": [80, 443]}} for i in range(150)]
        data = json.dumps(items)
        result = _truncate_with_marker(data, 500)
        assert "150 items total" in result


# ===========================================================================
# (5) Read-only / budgets / fail-open unchanged
# ===========================================================================


class TestBudgetsAndFailOpen:
    """Verify fixes did not regress budgets or fail-open behavior."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_max_steps_budget_enforced(self, _gr, _ga, mock_br):
        """Loop stops after MAX_TOOL_STEPS even if model keeps requesting tools."""
        mock_br.converse = AsyncMock(return_value=_tool_use_resp())
        adapter = _adapter()
        with patch("src.core.agentic_loop_streaming.MAX_TOOL_STEPS", 3):
            events = await _drain(run_agentic_loop_streaming(
                mcp_adapters=[adapter], **_LOOP_KW))

        done = [e for e in events if isinstance(e, StepDone)]
        assert done[0].finish_reason == "length"
        assert adapter.call_tool.await_count <= 3

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_tool_error_does_not_crash_loop(self, _gr, _ga, mock_br):
        """Tool raising exception → error result inline, loop continues."""
        mock_br.converse = AsyncMock(side_effect=[_tool_use_resp(), _end_turn("fallback")])
        adapter = _adapter()
        adapter.call_tool = AsyncMock(side_effect=RuntimeError("timeout"))

        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **_LOOP_KW))

        types = [type(e).__name__ for e in events]
        assert "StepDone" in types  # completed, didn't crash
        assert "StepToolResult" in types  # error was reported as step

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_no_tool_use_direct_answer(self, _gr, _ga, mock_br):
        """Model responds end_turn immediately → no tool events, just final."""
        mock_br.converse = AsyncMock(return_value=_end_turn("direct answer"))
        events = await _drain(run_agentic_loop_streaming(
            mcp_adapters=[_adapter()], **_LOOP_KW))

        types = [type(e).__name__ for e in events]
        assert "StepToolCall" not in types
        assert "StepToolResult" not in types
        assert "StepFinalChunk" in types
        assert "StepDone" in types

    def test_truncation_fail_open_on_malformed_json(self):
        """Malformed JSON that starts with '{' but isn't valid → falls to lines."""
        bad = "{not valid json at all\n" * 500
        result = _truncate_with_marker(bad, 200)
        assert "[truncated:" in result
        # Should still work (fail-open) — uses line count
        assert "500 items total" in result

    def test_truncation_fail_open_empty_items_list(self):
        """{"items": []} with big metadata → falls to line or char count."""
        data = json.dumps({"items": [], "metadata": "x" * 10000})
        result = _truncate_with_marker(data, 500)
        assert "[truncated:" in result
        # items is empty so it should NOT say "0 items total"
        assert "0 items total" not in result

    def test_max_tool_result_chars_default_is_8000(self):
        """Budget constant unchanged at 8000."""
        assert MAX_TOOL_RESULT_CHARS == 8000
