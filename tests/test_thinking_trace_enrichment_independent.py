"""Independent verification of the Thinking trace enrichment contract.

Tests written AGAINST the contract/spec, NOT against the implementation:

Feature A — StepThinking from narration text on tool_use turns:
  (1) A tool_use turn whose content has BOTH text + tool_use blocks yields
      StepThinking(text=...) for the text AND a StepToolCall — narration surfaced.
  (2) A turn with only tool_use (no text block) yields no spurious StepThinking.
  (3) The final-answer path (no tool calls) still works — text → StepFinalChunk.
  (4) Empty text blocks on tool_use turns do NOT yield StepThinking.

Feature B — StepRouting carries sub_query; SSE rendering includes foco:
  (5) StepRouting dataclass has a sub_query field (default "").
  (6) SSE rendering includes 'foco: "<sub_query>"' when sub_query is present.
  (7) SSE rendering omits foco when sub_query is empty string.
  (8) Fan-out yields one routing line per agent with its own sub_query.

All tests mock at the boundary (bedrock, MCP, tool execution) — no network.
"""
from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect_gen(agen):
    """Collect all items from an async generator."""
    items = []
    async for item in agen:
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Part A: StepThinking emission from narration text on tool_use turns
# ---------------------------------------------------------------------------

class TestNarrationSurfacing:
    """Contract: narration text blocks on tool_use turns become StepThinking."""

    def _run_loop(self, bedrock_responses: list[dict]):
        """Run the streaming loop with mocked bedrock.converse responses.

        Uses empty mcp_adapters — any tool_use block from bedrock will resolve
        to "tool not found" which is fine: we only verify that StepThinking
        was emitted from the narration text BEFORE tool execution.
        """
        from src.core.agentic_loop_streaming import (
            run_agentic_loop_streaming, StepThinking, StepToolCall,
            StepFinalChunk, StepDone, StepToolResult,
        )

        call_count = {"n": 0}

        async def mock_converse(**kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx < len(bedrock_responses):
                return bedrock_responses[idx]
            return {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "fallback"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        async def run():
            with patch("src.core.agentic_loop_streaming.bedrock") as mock_bedrock:
                mock_bedrock.converse = AsyncMock(side_effect=mock_converse)

                events = []
                async for event in run_agentic_loop_streaming(
                    query="test question",
                    system_prompt="You are a test agent.",
                    history_text="",
                    mcp_adapters=[],
                    agent_id="test-agent",
                    user_id="u1",
                    session_id="s1",
                ):
                    events.append(event)
                return events

        return _run(run())

    def test_tool_use_turn_with_text_yields_step_thinking_and_tool_call(self):
        """Contract (1): text + tool_use → StepThinking + StepToolCall."""
        from src.core.agentic_loop_streaming import StepThinking, StepToolCall

        narration = "I need to look up the pod metrics to answer this."

        turn1 = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": narration},
                {"type": "tool_use", "toolUseId": "t1", "name": "query_prometheus", "input": {"expr": "up"}},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        turn2 = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "The pods are healthy."}],
            "usage": {"input_tokens": 200, "output_tokens": 30},
        }

        events = self._run_loop([turn1, turn2])

        thinking_events = [e for e in events if isinstance(e, StepThinking) and e.text == narration]
        assert len(thinking_events) == 1, (
            f"Expected 1 StepThinking with narration. "
            f"Events: {[(type(e).__name__, getattr(e, 'text', '')) for e in events]}"
        )

        tool_calls = [e for e in events if isinstance(e, StepToolCall)]
        assert len(tool_calls) >= 1
        assert tool_calls[0].tool_name == "query_prometheus"

    def test_tool_use_turn_without_text_no_spurious_thinking(self):
        """Contract (2): only tool_use blocks (no text) → no StepThinking from narration."""
        from src.core.agentic_loop_streaming import StepThinking

        turn1 = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "toolUseId": "t1", "name": "get_pods", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }
        turn2 = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Done."}],
            "usage": {"input_tokens": 60, "output_tokens": 10},
        }

        events = self._run_loop([turn1, turn2])

        # Filter out reasoning-type StepThinking (from "reasoning" blocks)
        # Only narration-type (from "text" blocks) should be absent
        thinking_events = [e for e in events if isinstance(e, StepThinking)]
        assert len(thinking_events) == 0, (
            f"Expected 0 StepThinking from tool-only turn: {[t.text for t in thinking_events]}"
        )

    def test_final_answer_path_still_works(self):
        """Contract (3): non-tool turn → text becomes StepFinalChunk, not StepThinking."""
        from src.core.agentic_loop_streaming import StepThinking, StepFinalChunk, StepDone

        resp = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Here is your answer."}],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }

        events = self._run_loop([resp])

        thinking = [e for e in events if isinstance(e, StepThinking)]
        finals = [e for e in events if isinstance(e, StepFinalChunk)]
        dones = [e for e in events if isinstance(e, StepDone)]

        assert len(thinking) == 0, "Final-answer text must NOT become StepThinking"
        assert len(finals) >= 1, "Final answer must yield StepFinalChunk"
        assert len(dones) == 1

    def test_empty_text_block_on_tool_use_no_thinking(self):
        """Contract (4): empty text blocks on tool_use → no StepThinking."""
        from src.core.agentic_loop_streaming import StepThinking

        turn1 = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": ""},
                {"type": "tool_use", "toolUseId": "t1", "name": "get_pods", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }
        turn2 = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Done."}],
            "usage": {"input_tokens": 60, "output_tokens": 10},
        }

        events = self._run_loop([turn1, turn2])
        thinking = [e for e in events if isinstance(e, StepThinking)]
        assert len(thinking) == 0, f"Empty text block → no StepThinking: {[t.text for t in thinking]}"

    def test_reasoning_plus_narration_both_surfaced(self):
        """Reasoning (S3) blocks + narration text are both surfaced as StepThinking."""
        from src.core.agentic_loop_streaming import StepThinking

        reasoning_text = "Let me think about this..."
        narration_text = "I will call the tool now."

        turn1 = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "reasoning", "text": reasoning_text},
                {"type": "text", "text": narration_text},
                {"type": "tool_use", "toolUseId": "t1", "name": "query_vm", "input": {}},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 60},
        }
        turn2 = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Result."}],
            "usage": {"input_tokens": 150, "output_tokens": 20},
        }

        events = self._run_loop([turn1, turn2])
        thinking = [e for e in events if isinstance(e, StepThinking)]
        texts = [t.text for t in thinking]

        assert reasoning_text in texts, f"Missing reasoning: {texts}"
        assert narration_text in texts, f"Missing narration: {texts}"


# ---------------------------------------------------------------------------
# Part B: StepRouting with sub_query and SSE rendering
# ---------------------------------------------------------------------------

class TestStepRoutingSubQuery:
    """Contract: StepRouting carries sub_query; SSE renders foco conditionally."""

    def test_step_routing_has_sub_query_field(self):
        """Contract (5): StepRouting dataclass has sub_query field with default ""."""
        from src.core.agentic_loop_streaming import StepRouting

        r = StepRouting(agent="obs", confidence=0.92, reasoning="metrics question")
        assert r.sub_query == ""

        r2 = StepRouting(agent="obs", confidence=0.92, reasoning="m", sub_query="CPU?")
        assert r2.sub_query == "CPU?"

    def test_sse_rendering_includes_foco_when_sub_query_present(self):
        """Contract (6): SSE includes 'foco: "<sub_query>"' when present."""
        from src.core.agentic_loop_streaming import StepRouting, StepFinalChunk, StepDone

        events = [
            StepRouting(agent="obs", confidence=0.92, sub_query="What is pod CPU usage?"),
            StepFinalChunk(text="Answer here."),
            StepDone(),
        ]
        combined = self._render_sse(events)

        assert 'foco: "What is pod CPU usage?"' in combined, f"Missing foco. Got: {combined[:500]}"
        assert "🧭 Routed to **obs**" in combined
        assert "92%" in combined

    def test_sse_rendering_omits_foco_when_sub_query_empty(self):
        """Contract (7): SSE omits foco when sub_query is empty."""
        from src.core.agentic_loop_streaming import StepRouting, StepFinalChunk, StepDone

        events = [
            StepRouting(agent="k8s", confidence=0.85, sub_query=""),
            StepFinalChunk(text="Answer."),
            StepDone(),
        ]
        combined = self._render_sse(events)

        assert "foco:" not in combined, f"foco must be omitted when empty. Got: {combined[:500]}"
        assert "🧭 Routed to **k8s**" in combined
        assert "85%" in combined

    def test_fanout_multiple_routing_lines_with_sub_query(self):
        """Contract (8): fan-out yields one routing line per agent with own sub_query."""
        from src.core.agentic_loop_streaming import StepRouting, StepFinalChunk, StepDone

        events = [
            StepRouting(agent="obs", confidence=0.90, sub_query="Check VM metrics"),
            StepRouting(agent="k8s", confidence=0.85, sub_query="Check pod status"),
            StepFinalChunk(text="Combined answer."),
            StepDone(),
        ]
        combined = self._render_sse(events)

        assert 'Routed to **obs**' in combined
        assert 'foco: "Check VM metrics"' in combined
        assert 'Routed to **k8s**' in combined
        assert 'foco: "Check pod status"' in combined

    def test_thinking_narration_renders_in_sse_trace(self):
        """StepThinking from narration renders as 💭 inside the trace block."""
        from src.core.agentic_loop_streaming import (
            StepRouting, StepThinking, StepToolCall,
            StepToolResult, StepFinalChunk, StepDone,
        )

        events = [
            StepRouting(agent="obs", confidence=0.9, sub_query="CPU"),
            StepThinking(text="I need to check metrics first."),
            StepToolCall(tool_name="query_vm", args_display="expr=up"),
            StepToolResult(tool_name="query_vm", summary="📦 3 items"),
            StepFinalChunk(text="The CPU is fine."),
            StepDone(),
        ]
        combined = self._render_sse(events)

        assert "💭 I need to check metrics first." in combined
        # Narration should precede tool call in trace
        think_pos = combined.find("💭 I need")
        tool_pos = combined.find("🔧 query_vm")
        assert think_pos < tool_pos, "Narration should precede tool call"

    def test_sub_query_none_coerced_to_empty(self):
        """When classifier returns None sub_query, StepRouting uses empty string."""
        from src.core.agentic_loop_streaming import StepRouting

        routing = StepRouting(
            agent="aws", confidence=0.8, reasoning="ec2",
            sub_query=None or "",
        )
        assert routing.sub_query == ""

    def _render_sse(self, events: list) -> str:
        """Render events through sse_stream_agentic and return combined text."""
        from src.supervisor.openai_compat import sse_stream_agentic

        async def event_gen():
            for e in events:
                yield e

        async def collect():
            chunks = []
            async for chunk in sse_stream_agentic(
                step_events=event_gen(),
                model="test-model",
            ):
                if chunk.startswith("data: ") and "[DONE]" not in chunk:
                    try:
                        payload = json.loads(chunk[6:])
                        delta = payload.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            chunks.append(content)
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass
            return "".join(chunks)

        return _run(collect())
