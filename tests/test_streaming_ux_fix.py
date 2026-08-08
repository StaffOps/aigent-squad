"""Verify the streaming UX fix: terse results, details wrapping, no counter leaks."""
import asyncio
import json

import pytest


def test_count_items():
    from src.core.truncation import count_items
    assert count_items(json.dumps([1, 2, 3])) == 3
    assert count_items("") is None
    assert count_items("single line") is None
    assert count_items("NAME READY\npod1 1/1\npod2 1/1\n") == 2
    print("✅ count_items OK")


def test_summarize_tool_result():
    from src.core.agentic_loop_streaming import _summarize_tool_result

    # JSON array → terse count
    r = _summarize_tool_result("test", json.dumps([{"a": 1}, {"b": 2}, {"c": 3}]))
    assert r == "📦 3 items", f"Got: {r}"
    assert "chars" not in r

    # Empty
    r = _summarize_tool_result("test", "")
    assert r == "📦 empty", f"Got: {r}"

    # Single line short text
    r = _summarize_tool_result("test", "some short result")
    assert r == "📦 ok", f"Got: {r}"

    # Multi-line (kubectl-like table)
    r = _summarize_tool_result("test", "NAME  READY\npod1  1/1\npod2  1/1\n")
    assert "items" in r and "chars" not in r, f"Got: {r}"

    # Error-like
    r = _summarize_tool_result("test", "error: connection refused")
    assert "error" in r, f"Got: {r}"
    assert "chars" not in r

    # JSON object with collection
    r = _summarize_tool_result("t", json.dumps({"items": [1, 2, 3, 4, 5]}))
    assert r == "📦 5 items", f"Got: {r}"

    print("✅ _summarize_tool_result: all assertions pass (terse, no raw JSON)")


@pytest.mark.asyncio
async def test_details_wrapping():
    from src.core.agentic_loop_streaming import (
        StepDone, StepFinalChunk, StepRouting, StepToolCall, StepToolResult
    )
    from src.supervisor.openai_compat import sse_stream_agentic

    async def fake_events():
        yield StepRouting(agent="observability", confidence=0.92)
        yield StepToolCall(tool_name="query_metrics", args_display='ns="monitoring"')
        yield StepToolResult(tool_name="query_metrics", summary="📦 3 items")
        yield StepToolCall(tool_name="get_pods", args_display='ns="monitoring"')
        yield StepToolResult(tool_name="get_pods", summary="📦 12 items")
        yield StepFinalChunk(text="Here is the answer about your pods.")
        yield StepDone(finish_reason="stop")

    frames = []
    async for frame in sse_stream_agentic(fake_events(), "aigent-squad"):
        frames.append(frame)

    # Concatenate all content deltas
    full_content = ""
    for f in frames:
        if f.startswith("data: {"):
            data = json.loads(f[len("data: "):].strip())
            c = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if c:
                full_content += c

    # Structure assertions
    assert "<think>" in full_content, "Missing <think>"
    assert "🔧 Tool trace" in full_content
    assert "</think>" in full_content, "Missing </think>"
    assert full_content.index("<think>") < full_content.index("🔧 query_metrics")
    assert full_content.index("</think>") < full_content.index("Here is the answer")
    # No raw JSON / chars leaked
    assert "chars" not in full_content
    assert "[DONE]" in frames[-1]

    print("✅ sse_stream_agentic: <think> wrapping correct")
    print(f"   Full stream content:\n---\n{full_content}\n---")


@pytest.mark.asyncio
async def test_no_counters_leaked():
    from src.core.agentic_loop_streaming import (
        StepDone, StepFinalChunk, StepToolCall, StepToolResult
    )
    from src.supervisor.openai_compat import sse_stream_agentic

    graceful_msg = (
        "⚠️ I could not complete the full investigation within the "
        "available time — here is what I was able to collect:"
    )

    async def fake_budget_events():
        yield StepToolCall(tool_name="list_pods", args_display="")
        yield StepToolResult(tool_name="list_pods", summary="📦 5 items")
        yield StepFinalChunk(text=graceful_msg)
        yield StepDone(finish_reason="length")

    full = ""
    async for frame in sse_stream_agentic(fake_budget_events(), "aigent-squad"):
        if frame.startswith("data: {"):
            data = json.loads(frame[len("data: "):].strip())
            c = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if c:
                full += c

    # No raw counters
    assert "steps=" not in full
    assert "elapsed=" not in full
    assert "tokens=" not in full
    assert "/5" not in full
    assert "⚠️" in full
    print("✅ Budget exhaustion: no internal counters leaked to user")


@pytest.mark.asyncio
async def test_no_details_when_no_trace():
    """When there are no tool calls, no <think> block should appear."""
    from src.core.agentic_loop_streaming import StepDone, StepFinalChunk
    from src.supervisor.openai_compat import sse_stream_agentic

    async def direct_answer():
        yield StepFinalChunk(text="Direct answer without tools.")
        yield StepDone(finish_reason="stop")

    full = ""
    async for frame in sse_stream_agentic(direct_answer(), "aigent-squad"):
        if frame.startswith("data: {"):
            data = json.loads(frame[len("data: "):].strip())
            c = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if c:
                full += c

    assert "<think>" not in full
    assert "Direct answer without tools." in full
    print("✅ No <think> emitted when no trace steps present")


if __name__ == "__main__":
    test_count_items()
    test_summarize_tool_result()
    asyncio.run(test_details_wrapping())
    asyncio.run(test_no_counters_leaked())
    asyncio.run(test_no_details_when_no_trace())
    print("\n✅ ALL STREAMING UX TESTS PASS")
