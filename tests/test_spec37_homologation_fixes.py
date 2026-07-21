"""Independent contract tests for spec-37 homologation defect fixes.

Written by test-author (independent of implementation author) against the
CONTRACT, not the implementation. Verifies two fixes:

  DEFECT 1 — Streaming loop tool-calling parity:
    (1) stream:true flow with converse returning tool_use then end_turn →
        tool IS executed (call_tool invoked), step deltas emitted, MULTIPLE
        content events (not single end_turn).
    (2) Streaming and non-streaming loops produce the SAME tool-execution
        behavior for the same mocked converse sequence.

  DEFECT 2 — Truncation marker with true item count:
    (3) Tool result > MAX_TOOL_RESULT_CHARS gets '[truncated: ... N items
        total ...]' marker with CORRECT N (e.g. 263-item JSON array → 263).
    (4) Marker applied AFTER B3 redaction (order preserved).
    (5) Default MAX_TOOL_RESULT_CHARS is 8000, env-overridable.
    (6) Read-only/budgets/fail-open behavior unchanged.

Docker: python:3.11-slim (stub deps + env). Does NOT modify implementation.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from src.core.agent_config import MAX_TOOL_RESULT_CHARS
from src.core.agentic_loop import _truncate_with_marker, run_agentic_loop
from src.core.agentic_loop_streaming import (
    StepDone,
    StepFinalChunk,
    StepToolCall,
    StepToolResult,
    run_agentic_loop_streaming,
)


# ---------------------------------------------------------------------------
# Test helpers / factories
# ---------------------------------------------------------------------------


def _make_adapter(name="test-mcp", tool_names=None, call_result=None):
    """Factory for a mock MCP adapter."""
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
    adapter.call_tool = AsyncMock(
        return_value=call_result or '[{"name":"pod-1"},{"name":"pod-2"}]'
    )
    return adapter


def _bedrock_tool_use(tool_name="pods_list_in_namespace", args=None, tool_id="tu-001"):
    """Simulate a Bedrock Converse response requesting tool_use."""
    return {
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "content": [
            {
                "type": "tool_use",
                "toolUseId": tool_id,
                "name": tool_name,
                "input": args or {"ns": "monitoring"},
            }
        ],
    }


def _bedrock_final(text="There are 263 pods in the namespace."):
    """Simulate a Bedrock Converse final text response."""
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 200, "output_tokens": 80},
        "content": [{"type": "text", "text": text}],
    }


async def _collect(gen) -> list:
    """Consume an async generator into a list."""
    items = []
    async for item in gen:
        items.append(item)
    return items


LOOP_KWARGS = dict(
    query="list all pods in monitoring",
    system_prompt="You are a K8s assistant.",
    history_text="",
    agent_id="kubernetes",
    user_id="test-user",
    session_id="test-session",
)


def _make_263_pod_json() -> str:
    """Generate a JSON array of 263 pods (~30KB, exceeds 8000 chars)."""
    pods = [
        {"metadata": {"name": f"pod-{i:03d}", "namespace": "monitoring",
                      "uid": f"uid-{i:08d}"}, "status": {"phase": "Running"}}
        for i in range(263)
    ]
    return json.dumps(pods)


# ===========================================================================
# CONTRACT 5: Default MAX_TOOL_RESULT_CHARS is 8000, env-overridable
# ===========================================================================


class TestContract5_MaxToolResultCharsDefault:
    """MAX_TOOL_RESULT_CHARS defaults to 8000 and responds to env override."""

    def test_default_is_8000(self):
        """The module-level constant is 8000 (raised from old 4000)."""
        assert MAX_TOOL_RESULT_CHARS == 8000

    def test_env_override(self, monkeypatch):
        """AIGENT_MAX_TOOL_RESULT_CHARS env var overrides the default."""
        monkeypatch.setenv("AIGENT_MAX_TOOL_RESULT_CHARS", "12000")
        # Re-import to pick up the env var
        import importlib
        import src.core.agent_config as cfg_mod
        importlib.reload(cfg_mod)
        assert cfg_mod.MAX_TOOL_RESULT_CHARS == 12000
        # Restore
        monkeypatch.delenv("AIGENT_MAX_TOOL_RESULT_CHARS", raising=False)
        importlib.reload(cfg_mod)


# ===========================================================================
# CONTRACT 3: Truncation marker has correct item count
# ===========================================================================


class TestContract3_TruncationMarkerCorrectCount:
    """A tool result larger than MAX_TOOL_RESULT_CHARS gets a marker
    '[truncated: showing first N chars of M items total]' with the
    CORRECT M — the true item count, not the truncated visible count."""

    def test_json_array_263_items_reports_263(self):
        """263-item JSON array → marker says '263 items total'."""
        big_json = _make_263_pod_json()
        assert len(big_json) > 8000, "Test data must exceed max chars"

        result = _truncate_with_marker(big_json, 8000)

        # Must start with the truncation marker
        assert result.startswith("[truncated:")
        # Must report 263 items
        assert "263 items total" in result
        # The body after marker must be exactly 8000 chars of original
        marker_end = result.index("\n") + 1
        body = result[marker_end:]
        assert len(body) == 8000
        assert body == big_json[:8000]

    def test_multiline_text_counts_lines(self):
        """Non-JSON multiline text → marker reports line count."""
        lines = [f"line-{i}: some data here padding" for i in range(500)]
        big_text = "\n".join(lines)
        assert len(big_text) > 8000

        result = _truncate_with_marker(big_text, 8000)

        assert result.startswith("[truncated:")
        # Should report 500 items (lines)
        assert "500 items total" in result

    def test_unparseable_blob_reports_char_count(self):
        """Binary-like blob → marker reports raw char count."""
        blob = "x" * 20000  # single line, not JSON
        result = _truncate_with_marker(blob, 8000)

        assert result.startswith("[truncated:")
        assert "20000 chars total" in result

    def test_short_result_no_marker(self):
        """Result shorter than max_chars → returned unchanged (no marker)."""
        short = '{"pods": [1,2,3]}'
        result = _truncate_with_marker(short, 8000)
        assert result == short
        assert "[truncated:" not in result

    def test_exact_boundary_no_marker(self):
        """Result exactly at max_chars → no truncation."""
        exact = "a" * 8000
        result = _truncate_with_marker(exact, 8000)
        assert result == exact

    def test_one_over_boundary_triggers_marker(self):
        """Result at max_chars+1 → truncation triggered."""
        over = "a" * 8001
        result = _truncate_with_marker(over, 8000)
        assert result.startswith("[truncated:")

    def test_custom_max_chars(self):
        """Works with any max_chars value, not just 8000."""
        items = json.dumps([{"id": i} for i in range(50)])
        result = _truncate_with_marker(items, 100)
        assert "50 items total" in result

    def test_json_object_with_items_key_reports_item_count(self):
        """kube-mcp returns {"items": [...]} — counts elements in items key."""
        pods = [
            {"metadata": {"name": f"pod-{i}", "namespace": "monitoring"}}
            for i in range(263)
        ]
        k8s_response = json.dumps({"kind": "PodList", "items": pods, "metadata": {}})
        assert len(k8s_response) > 8000, "Test data must exceed max chars"

        result = _truncate_with_marker(k8s_response, 8000)

        assert result.startswith("[truncated:")
        assert "263 items total" in result

    def test_json_object_with_results_key(self):
        """JSON object with 'results' key — counts that list."""
        data = json.dumps({"results": [{"id": i} for i in range(150)], "total": 150})
        result = _truncate_with_marker(data, 200)
        assert "150 items total" in result

    def test_json_object_with_data_key(self):
        """JSON object with 'data' key (VM query format) — counts that list."""
        data = json.dumps({"status": "success", "data": [{"metric": i} for i in range(80)]})
        result = _truncate_with_marker(data, 200)
        assert "80 items total" in result

    def test_json_object_fallback_to_largest_list(self):
        """JSON object without known keys — uses largest list value."""
        data = json.dumps({"small": [1, 2], "big_list": list(range(100)), "meta": "x"})
        result = _truncate_with_marker(data, 200)
        assert "100 items total" in result

    def test_json_object_no_list_values_falls_to_lines(self):
        """JSON object with no list values → falls through to line count."""
        # Object with only scalar/dict values, big enough to exceed limit
        big_obj = json.dumps({"key_" + str(i): "val_" + ("x" * 50) for i in range(200)})
        result = _truncate_with_marker(big_obj, 500)
        # Should fall through to line count or char count (no list inside)
        assert result.startswith("[truncated:")
        # Should NOT say "0 items total" — should use line or char count
        assert "0 items total" not in result


# ===========================================================================
# CONTRACT 1: Streaming loop does tool-calling (not single end_turn)
# ===========================================================================


class TestContract1_StreamingToolCalling:
    """stream:true flow where converse returns tool_use then end_turn →
    the tool IS executed (call_tool invoked), a StepToolCall delta AND
    a StepToolResult delta are streamed, then final answer → MULTIPLE
    content chunks (not 1)."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_tool_is_executed_call_tool_invoked(self, _gr, _ga, mock_bedrock):
        """call_tool is actually invoked when model requests tool_use."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # call_tool MUST have been called
        adapter.call_tool.assert_awaited_once_with(
            "pods_list_in_namespace", {"ns": "monitoring"}
        )

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_step_deltas_emitted(self, _gr, _ga, mock_bedrock):
        """StepToolCall and StepToolResult deltas ARE streamed."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        types = [type(e).__name__ for e in events]
        assert "StepToolCall" in types, f"No StepToolCall in {types}"
        assert "StepToolResult" in types, f"No StepToolResult in {types}"

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_multiple_content_chunks_not_single(self, _gr, _ga, mock_bedrock):
        """Final answer produces MULTIPLE StepFinalChunk events (chunked),
        proving it's not a single end_turn blob."""
        # Make the final answer long enough to chunk (>200 chars)
        long_answer = "Pod monitoring-agent-abc is running. " * 20  # ~740 chars
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(text=long_answer),
        ])
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        final_chunks = [e for e in events if isinstance(e, StepFinalChunk)]
        assert len(final_chunks) > 1, (
            f"Expected >1 final chunks for long answer, got {len(final_chunks)}"
        )

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_event_ordering_tc_tr_fc_done(self, _gr, _ga, mock_bedrock):
        """Events appear: ToolCall → ToolResult → FinalChunk(s) → Done."""
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
        assert tc_idx < tr_idx < fc_idx < done_idx

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_multiple_tool_use_blocks_all_executed(self, _gr, _ga, mock_bedrock):
        """Multiple tool_use blocks in one turn → all tools executed."""
        multi_tool_resp = {
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 80},
            "content": [
                {"type": "tool_use", "toolUseId": "tu-1",
                 "name": "tool_a", "input": {"x": "1"}},
                {"type": "tool_use", "toolUseId": "tu-2",
                 "name": "tool_b", "input": {"y": "2"}},
            ],
        }
        mock_bedrock.converse = AsyncMock(side_effect=[
            multi_tool_resp,
            _bedrock_final(),
        ])
        adapter = _make_adapter(tool_names=["tool_a", "tool_b"])

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Both tools called
        assert adapter.call_tool.await_count == 2
        # Two StepToolCall events
        tool_calls = [e for e in events if isinstance(e, StepToolCall)]
        assert len(tool_calls) == 2
        assert {tc.tool_name for tc in tool_calls} == {"tool_a", "tool_b"}


# ===========================================================================
# CONTRACT 2: Streaming and non-streaming produce SAME tool-execution behavior
# ===========================================================================


class TestContract2_StreamingNonStreamingParity:
    """For the same mocked converse sequence, both loops execute tools
    identically — no divergence in tool-calling logic."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop.bedrock")
    @patch("src.core.agentic_loop._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_non_streaming_calls_tool(self, _gr, _ga, mock_bedrock):
        """Non-streaming loop calls the tool for tool_use → end_turn."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter()

        result = await run_agentic_loop(mcp_adapters=[adapter], **LOOP_KWARGS)

        adapter.call_tool.assert_awaited_once_with(
            "pods_list_in_namespace", {"ns": "monitoring"}
        )
        assert "263 pods" in result

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    @patch("src.core.agentic_loop.bedrock")
    @patch("src.core.agentic_loop._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_both_loops_call_tool_same_args(
        self, _gr_ns, _ga_ns, mock_bedrock_ns, _gr_s, _ga_s, mock_bedrock_s
    ):
        """Both loops invoke call_tool with identical arguments."""
        converse_seq = [_bedrock_tool_use(), _bedrock_final()]

        # Non-streaming
        mock_bedrock_ns.converse = AsyncMock(side_effect=list(converse_seq))
        adapter_ns = _make_adapter(name="ns-adapter")
        await run_agentic_loop(mcp_adapters=[adapter_ns], **LOOP_KWARGS)

        # Streaming
        mock_bedrock_s.converse = AsyncMock(side_effect=list(converse_seq))
        adapter_s = _make_adapter(name="s-adapter")
        await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter_s], **LOOP_KWARGS
        ))

        # Both called with same tool name + args
        ns_call = adapter_ns.call_tool.call_args
        s_call = adapter_s.call_tool.call_args
        assert ns_call == s_call, (
            f"Non-streaming called {ns_call}, streaming called {s_call}"
        )

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_streaming_uses_shared_truncate_with_marker(self, _gr, _ga, mock_bedrock):
        """Streaming loop imports and uses _truncate_with_marker from agentic_loop
        — single shared implementation, can't drift."""
        # Verify the import exists (this is a structural contract)
        from src.core.agentic_loop_streaming import _truncate_with_marker as stream_fn
        from src.core.agentic_loop import _truncate_with_marker as loop_fn
        assert stream_fn is loop_fn, (
            "Streaming must import _truncate_with_marker from agentic_loop (shared)"
        )


# ===========================================================================
# CONTRACT 4: Truncation marker applied AFTER B3 redaction
# ===========================================================================


class TestContract4_MarkerAfterRedaction:
    """Truncation happens AFTER guardrail redaction — if B3 redacts the result,
    the marker counts items in the redacted text (not the original)."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    async def test_redacted_result_then_truncated(self, _ga, mock_bedrock):
        """When B3 redacts (replaces entire result), truncation sees the
        redacted version — not the original large output."""
        # B3 will replace with a short redaction notice
        redacted_msg = "[tool result redacted by guardrail — contains blocked content]"

        with patch(
            "src.core.agentic_loop_streaming._guardrail_tool_result",
            return_value=redacted_msg,
        ):
            mock_bedrock.converse = AsyncMock(side_effect=[
                _bedrock_tool_use(),
                _bedrock_final(),
            ])
            # Adapter returns huge result, but B3 replaces it
            adapter = _make_adapter(call_result=_make_263_pod_json())

            events = await _collect(run_agentic_loop_streaming(
                mcp_adapters=[adapter], **LOOP_KWARGS
            ))

        # The redacted message is short → no truncation marker should appear
        # because len(redacted_msg) < MAX_TOOL_RESULT_CHARS
        # This proves truncation runs AFTER redaction
        # (If it ran before, the 263-item JSON would have been truncated with marker)
        tool_results = [e for e in events if isinstance(e, StepToolResult)]
        assert len(tool_results) == 1
        # The converse() received the short redacted text, not the truncated 263-item JSON
        second_call_messages = mock_bedrock.converse.call_args_list[1]
        # Messages are passed as keyword 'messages'
        msgs = second_call_messages.kwargs.get("messages") or second_call_messages[1].get("messages", [])
        # Find the user message with tool result
        user_msg = [m for m in msgs if m.get("role") == "user"]
        if user_msg:
            content = user_msg[-1].get("content", [])
            if content and isinstance(content, list) and isinstance(content[0], dict):
                text_in_result = content[0].get("toolResult", {}).get("content", [{}])[0].get("text", "")
                # Should contain the redacted message, NOT "[truncated:"
                assert "[truncated:" not in text_in_result
                assert "redacted by guardrail" in text_in_result

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop.bedrock")
    @patch("src.core.agentic_loop._guardrail_tool_args", return_value=True)
    async def test_non_streaming_also_truncates_after_redaction(self, _ga, mock_bedrock):
        """Non-streaming loop also applies truncation AFTER B3 redaction."""
        redacted_msg = "[tool result redacted by guardrail — contains blocked content]"

        with patch(
            "src.core.agentic_loop._guardrail_tool_result",
            return_value=redacted_msg,
        ):
            mock_bedrock.converse = AsyncMock(side_effect=[
                _bedrock_tool_use(),
                _bedrock_final(),
            ])
            adapter = _make_adapter(call_result=_make_263_pod_json())

            result = await run_agentic_loop(mcp_adapters=[adapter], **LOOP_KWARGS)

        # The second converse call should have redacted message, not truncated JSON
        second_call = mock_bedrock.converse.call_args_list[1]
        msgs = second_call.kwargs.get("messages", [])
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        if user_msgs:
            content = user_msgs[-1].get("content", [])
            if content and isinstance(content[0], dict):
                text_in_result = content[0].get("toolResult", {}).get("content", [{}])[0].get("text", "")
                assert "[truncated:" not in text_in_result


# ===========================================================================
# CONTRACT 6: Read-only / budgets / fail-open unchanged
# ===========================================================================


class TestContract6_UnchangedBehavior:
    """Existing budgets, fail-open, and read-only semantics preserved."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_budget_max_steps_streaming(self, _gr, _ga, mock_bedrock):
        """MAX_TOOL_STEPS budget still enforced in streaming loop."""
        # Return tool_use forever — budget should cut it off
        mock_bedrock.converse = AsyncMock(
            return_value=_bedrock_tool_use()
        )
        adapter = _make_adapter()

        with patch("src.core.agentic_loop_streaming.MAX_TOOL_STEPS", 2):
            events = await _collect(run_agentic_loop_streaming(
                mcp_adapters=[adapter], **LOOP_KWARGS
            ))

        done_events = [e for e in events if isinstance(e, StepDone)]
        assert len(done_events) == 1
        assert done_events[0].finish_reason == "length"
        # Should have called tool at most 2 times (2 steps)
        assert adapter.call_tool.await_count <= 2

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_fail_open_tool_error_streaming(self, _gr, _ga, mock_bedrock):
        """Tool error → inline error result, loop continues to final answer."""
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(text="I couldn't get the pods."),
        ])
        adapter = _make_adapter()
        adapter.call_tool = AsyncMock(side_effect=Exception("connection refused"))

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # Should complete with Done (not crash)
        types = [type(e).__name__ for e in events]
        assert "StepDone" in types
        # Should still have a tool result step (error summary)
        assert "StepToolResult" in types

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_no_tools_direct_answer(self, _gr, _ga, mock_bedrock):
        """If model gives end_turn immediately (no tool_use), stream final only."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_final())
        adapter = _make_adapter()

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        types = [type(e).__name__ for e in events]
        assert "StepToolCall" not in types
        assert "StepFinalChunk" in types
        assert "StepDone" in types
        adapter.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop.bedrock")
    @patch("src.core.agentic_loop._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_budget_max_steps_non_streaming(self, _gr, _ga, mock_bedrock):
        """MAX_TOOL_STEPS budget also enforced in non-streaming loop."""
        mock_bedrock.converse = AsyncMock(return_value=_bedrock_tool_use())
        adapter = _make_adapter()

        with patch("src.core.agentic_loop.MAX_TOOL_STEPS", 2):
            result = await run_agentic_loop(mcp_adapters=[adapter], **LOOP_KWARGS)

        assert "budget was exhausted" in result
        assert adapter.call_tool.await_count <= 2


# ===========================================================================
# Additional coverage: streaming helpers (changed code)
# ===========================================================================


class TestStreamingHelpers:
    """Cover _sanitize_args_for_display and _summarize_tool_result which are
    part of the streaming defect-1 fix."""

    def test_sanitize_args_hides_secrets(self):
        from src.core.agentic_loop_streaming import _sanitize_args_for_display
        args = {"namespace": "monitoring", "password": "s3cret!", "token": "abc123"}
        display = _sanitize_args_for_display(args)
        assert "monitoring" in display
        assert "s3cret!" not in display
        assert "abc123" not in display
        assert '***' in display

    def test_sanitize_args_truncates_long_values(self):
        from src.core.agentic_loop_streaming import _sanitize_args_for_display
        args = {"data": "x" * 100}
        display = _sanitize_args_for_display(args)
        assert "..." in display
        assert len(display) < 100

    def test_sanitize_args_empty(self):
        from src.core.agentic_loop_streaming import _sanitize_args_for_display
        assert _sanitize_args_for_display({}) == ""

    def test_summarize_json_array(self):
        from src.core.agentic_loop_streaming import _summarize_tool_result
        data = json.dumps([{"name": f"item-{i}"} for i in range(10)])
        summary = _summarize_tool_result("test_tool", data)
        assert "10 items" in summary
        assert "📦" in summary

    def test_summarize_json_object(self):
        from src.core.agentic_loop_streaming import _summarize_tool_result
        data = json.dumps({"name": "pod", "status": "Running", "ip": "10.0.0.1"})
        summary = _summarize_tool_result("test_tool", data)
        assert "📦" in summary
        assert "object" in summary

    def test_summarize_multiline_text(self):
        from src.core.agentic_loop_streaming import _summarize_tool_result
        data = "line1: something\nline2: else\nline3: more"
        summary = _summarize_tool_result("test_tool", data)
        assert "📦" in summary
        assert "3 lines" in summary

    def test_summarize_short_text(self):
        from src.core.agentic_loop_streaming import _summarize_tool_result
        data = "OK"
        summary = _summarize_tool_result("test_tool", data)
        assert "📦" in summary
        assert "OK" in summary


class TestToConverseAssistantBlocks:
    """Cover _to_converse_assistant_blocks helper (shared between loops)."""

    def test_text_block(self):
        from src.core.agentic_loop import _to_converse_assistant_blocks
        blocks = [{"type": "text", "text": "Hello"}]
        result = _to_converse_assistant_blocks(blocks)
        assert result == [{"text": "Hello"}]

    def test_tool_use_block(self):
        from src.core.agentic_loop import _to_converse_assistant_blocks
        blocks = [{"type": "tool_use", "toolUseId": "tu-1",
                   "name": "test", "input": {"a": 1}}]
        result = _to_converse_assistant_blocks(blocks)
        assert result == [{"toolUse": {"toolUseId": "tu-1", "name": "test", "input": {"a": 1}}}]

    def test_mixed_blocks(self):
        from src.core.agentic_loop import _to_converse_assistant_blocks
        blocks = [
            {"type": "text", "text": "I'll call a tool"},
            {"type": "tool_use", "toolUseId": "tu-1", "name": "x", "input": {}},
        ]
        result = _to_converse_assistant_blocks(blocks)
        assert len(result) == 2


class TestErrorToolResult:
    """Cover _error_tool_result helper."""

    def test_builds_error_structure(self):
        from src.core.agentic_loop import _error_tool_result
        result = _error_tool_result("tu-99", "something broke")
        assert result["toolResult"]["toolUseId"] == "tu-99"
        assert result["toolResult"]["status"] == "error"
        assert "something broke" in result["toolResult"]["content"][0]["text"]


class TestTruncationInLoopIntegration:
    """Verify truncation is applied correctly in the actual loop context
    (integration with real call flow, not just unit-testing the helper)."""

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop.bedrock")
    @patch("src.core.agentic_loop._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_large_result_truncated_in_context_non_streaming(self, _gr, _ga, mock_bedrock):
        """Non-streaming loop truncates large tool results before passing to model."""
        big_json = _make_263_pod_json()
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(text="There are 263 pods."),
        ])
        adapter = _make_adapter(call_result=big_json)

        result = await run_agentic_loop(mcp_adapters=[adapter], **LOOP_KWARGS)

        # The second converse call should have truncated content
        second_call = mock_bedrock.converse.call_args_list[1]
        msgs = second_call.kwargs.get("messages", [])
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert user_msgs, "Expected user message with tool result"
        content = user_msgs[-1]["content"]
        text = content[0]["toolResult"]["content"][0]["text"]
        assert "[truncated:" in text
        assert "263 items total" in text

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_large_result_truncated_in_context_streaming(self, _gr, _ga, mock_bedrock):
        """Streaming loop truncates large tool results before passing to model."""
        big_json = _make_263_pod_json()
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(text="There are 263 pods."),
        ])
        adapter = _make_adapter(call_result=big_json)

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        # The second converse call should have truncated content
        second_call = mock_bedrock.converse.call_args_list[1]
        msgs = second_call.kwargs.get("messages", [])
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert user_msgs
        content = user_msgs[-1]["content"]
        text = content[0]["toolResult"]["content"][0]["text"]
        assert "[truncated:" in text
        assert "263 items total" in text

    @pytest.mark.asyncio
    @patch("src.core.agentic_loop_streaming.bedrock")
    @patch("src.core.agentic_loop_streaming._guardrail_tool_args", return_value=True)
    @patch("src.core.agentic_loop_streaming._guardrail_tool_result",
           side_effect=lambda r, *a, **kw: r)
    async def test_small_result_not_truncated(self, _gr, _ga, mock_bedrock):
        """Small tool results pass through without truncation marker."""
        small_result = '[{"name":"pod-1"}]'
        mock_bedrock.converse = AsyncMock(side_effect=[
            _bedrock_tool_use(),
            _bedrock_final(),
        ])
        adapter = _make_adapter(call_result=small_result)

        events = await _collect(run_agentic_loop_streaming(
            mcp_adapters=[adapter], **LOOP_KWARGS
        ))

        second_call = mock_bedrock.converse.call_args_list[1]
        msgs = second_call.kwargs.get("messages", [])
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        content = user_msgs[-1]["content"]
        text = content[0]["toolResult"]["content"][0]["text"]
        assert "[truncated:" not in text
