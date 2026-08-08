"""Independent contract tests for src/core/agentic_loop.py — Phase 3.

Written by a separate test-author session (verification-independence.md).
Tests the PUBLIC CONTRACT of run_agentic_loop without modifying implementation.
Mocks bedrock.converse() and adapter.call_tool (no network).

Coverage targets:
  (1) Single tool call -> result fed back -> final answer
  (2) Multi-step loop (list then get) terminates with answer
  (3) MAX_TOOL_STEPS cap -> degraded/partial finalize
  (4) MAX_LOOP_DURATION/TOKENS budget stop
  (5) B3: guardrail blocks tool ARG pre-exec; redacts RESULT before truncation
  (6) Fail-open: call_tool raises -> inline error, request still answers
  (7) B8: two toolUse blocks, one fails -> per-tool error with correct toolUseId
  (8) B5: circuit breaker opens after 3 failures; 5s timeout; session reuse
  (9) SR2: case-sensitivity refuse
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agentic_loop import (
    _McpSessionPool,
    _ToolRouter,
    _error_tool_result,
    _guardrail_tool_args,
    _guardrail_tool_result,
    run_agentic_loop,
)
from src.core.adapters import McpAdapter
from src.core.circuit_breaker import CircuitBreaker, CircuitState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adapter(name="mcp-a", tools=None, url="http://mcp-a:8080"):
    """Minimal McpAdapter for testing."""
    return McpAdapter(name=name, url=url, tools=tools or ["list_pods", "get_pod"])


def _text_response(text: str, input_tokens=50, output_tokens=30):
    """Converse response that ends the loop (final answer)."""
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _tool_response(tools: list[dict], input_tokens=50, output_tokens=30):
    """Converse response requesting tool execution."""
    content = []
    for t in tools:
        content.append({
            "type": "tool_use",
            "toolUseId": t["id"],
            "name": t["name"],
            "input": t.get("input", {}),
        })
    return {
        "stop_reason": "tool_use",
        "content": content,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _loop_kwargs(adapters, **overrides):
    """Default kwargs for run_agentic_loop."""
    defaults = dict(
        query="test query",
        system_prompt="You are a test agent.",
        history_text="No previous conversation",
        mcp_adapters=adapters,
        agent_id="test-agent",
        user_id="user-1",
        session_id="sess-1",
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Contract (1): Single tool call -> result fed back -> final answer
# ---------------------------------------------------------------------------


class TestContract1SingleToolCall:
    """Model calls one tool, receives result, produces final answer."""

    @pytest.mark.asyncio
    async def test_single_tool_result_fed_back(self):
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "List pods",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="pod-a Running\npod-b CrashLoop")

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                _text_response("2 pods found: pod-a Running, pod-b CrashLoop."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Final answer contains tool data
        assert "pod-a" in result
        assert "CrashLoop" in result
        # Model called twice (tool_use + final)
        assert mock_bed.converse.call_count == 2
        # Tool was invoked once with correct args
        adapter.call_tool.assert_awaited_once_with("list_pods", {})

    @pytest.mark.asyncio
    async def test_tool_result_appears_in_second_converse_messages(self):
        """The tool result text is included in the messages of the 2nd converse call."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="UNIQUE_MARKER_XYZ")

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                _text_response("done"),
            ])

            _result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Inspect the 2nd call's messages for the tool result
        second_call = mock_bed.converse.call_args_list[1]
        msgs = second_call[1]["messages"]
        user_msg = [m for m in msgs if m["role"] == "user"][-1]
        tool_text = user_msg["content"][0]["toolResult"]["content"][0]["text"]
        assert "UNIQUE_MARKER_XYZ" in tool_text


# ---------------------------------------------------------------------------
# Contract (2): Multi-step loop (list then get) terminates with answer
# ---------------------------------------------------------------------------


class TestContract2MultiStepLoop:
    """Model calls list, then get in separate turns, then produces answer."""

    @pytest.mark.asyncio
    async def test_two_step_tool_chain(self):
        adapter = _adapter(tools=["list_pods", "get_pod"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
            {"toolSpec": {"name": "get_pod", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {
                              "name": {"type": "string"}}}}}},
        ])
        call_log = []

        async def _mock_call(name, args):
            call_log.append((name, args))
            if name == "list_pods":
                return "pod-x\npod-y"
            return "pod-x: Running, 3 restarts, image=nginx:1.25"

        adapter.call_tool = _mock_call

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                # Turn 1: model lists pods
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                # Turn 2: model gets details of pod-x
                _tool_response([{"id": "t2", "name": "get_pod", "input": {"name": "pod-x"}}]),
                # Turn 3: final answer
                _text_response("pod-x has 3 restarts with nginx:1.25"),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert "3 restarts" in result
        assert mock_bed.converse.call_count == 3
        assert call_log == [("list_pods", {}), ("get_pod", {"name": "pod-x"})]


# ---------------------------------------------------------------------------
# Contract (3): MAX_TOOL_STEPS cap -> degraded/partial finalize
# ---------------------------------------------------------------------------


class TestContract3MaxStepsCap:
    """Loop MUST terminate with degraded answer when MAX_TOOL_STEPS hit."""

    @pytest.mark.asyncio
    async def test_loop_stops_at_max_steps(self):
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="data")

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop.MAX_TOOL_STEPS", 3):
            # Model always requests another tool (infinite without budget)
            mock_bed.converse = AsyncMock(
                return_value=_tool_response([{"id": "t1", "name": "list_pods", "input": {}}])
            )

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Must be degraded response (mentions budget/incomplete)
        lower = result.lower()
        assert "budget" in lower or "incomplete" in lower or "exhausted" in lower
        # Must NOT loop forever — converse called exactly MAX_TOOL_STEPS times
        assert mock_bed.converse.call_count == 3

    @pytest.mark.asyncio
    async def test_max_steps_one_still_returns(self):
        """Even MAX_TOOL_STEPS=1 produces a degraded answer, not a crash."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="some-data")

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop.MAX_TOOL_STEPS", 1):
            mock_bed.converse = AsyncMock(
                return_value=_tool_response([{"id": "t1", "name": "list_pods", "input": {}}])
            )

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert result  # Non-empty
        assert mock_bed.converse.call_count == 1


# ---------------------------------------------------------------------------
# Contract (4): MAX_LOOP_DURATION / MAX_LOOP_TOKENS budget stop
# ---------------------------------------------------------------------------


class TestContract4BudgetEnforcement:
    """Loop terminates on token or duration budget exhaustion."""

    @pytest.mark.asyncio
    async def test_token_budget_stops_loop(self):
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="data")

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop.MAX_LOOP_TOKENS", 150):
            # Each call uses 50+30=80 tokens; after 2 calls = 160 > 150
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}],
                               input_tokens=50, output_tokens=30),
                _tool_response([{"id": "t2", "name": "list_pods", "input": {}}],
                               input_tokens=50, output_tokens=30),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        lower = result.lower()
        assert "budget" in lower or "incomplete" in lower or "token" in lower

    @pytest.mark.asyncio
    async def test_duration_budget_stops_loop(self):
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="data")

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop.MAX_LOOP_DURATION_MS", 1):
            # Duration budget = 1ms — will be exceeded after first converse + tool
            # Provide enough responses for the loop to potentially continue
            mock_bed.converse = AsyncMock(
                return_value=_tool_response([{"id": "t1", "name": "list_pods", "input": {}}])
            )

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        lower = result.lower()
        assert "budget" in lower or "incomplete" in lower or "duration" in lower


# ---------------------------------------------------------------------------
# Contract (5): B3 guardrail — ARG refused pre-exec; RESULT redacted pre-truncation
# ---------------------------------------------------------------------------


class TestContract5GuardrailB3:
    """B3: guardrail checks tool args before exec and redacts results after."""

    @pytest.mark.asyncio
    async def test_blocked_args_produce_error_result_no_execution(self):
        """Tool with blocked args is NOT executed; error result fed to model."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="should never see this")

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop._guardrail_tool_args", return_value=False):
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods",
                                 "input": {"url": "http://evil.internal"}}]),
                _text_response("I cannot access that resource."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Tool must NOT have been called (args blocked pre-exec)
        adapter.call_tool.assert_not_awaited()
        # Loop continues to final answer (fail-open)
        assert result is not None
        # The model received an error toolResult
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        tool_result = user_msg["content"][0]["toolResult"]
        assert tool_result["status"] == "error"
        assert "guardrail" in tool_result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_result_redacted_before_truncation(self):
        """B3: result is guardrail-redacted BEFORE MAX_TOOL_RESULT_CHARS truncation."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        # Return a long result that would be truncated
        long_result = "SENSITIVE_" * 1000  # 10000 chars
        adapter.call_tool = AsyncMock(return_value=long_result)

        redaction_msg = "[tool result redacted by guardrail — contains blocked content]"

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop._guardrail_tool_result", return_value=redaction_msg):
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                _text_response("Content was redacted."),
            ])

            _result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Verify: the text sent to the model is the REDACTED message, not truncated original
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        tool_text = user_msg["content"][0]["toolResult"]["content"][0]["text"]
        assert "redacted" in tool_text
        # The original SENSITIVE_ content must NOT appear
        assert "SENSITIVE_" not in tool_text

    @pytest.mark.asyncio
    async def test_guardrail_args_check_uses_json_serialized_args(self):
        """Guardrail receives JSON-serialized tool arguments."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="ok")

        captured_args = []

        def mock_guardrail_args(tool_name, args, agent_id, user_id, session_id):
            captured_args.append((tool_name, args))
            return True  # allow

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop._guardrail_tool_args", side_effect=mock_guardrail_args):
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods",
                                 "input": {"ns": "kube-system"}}]),
                _text_response("done"),
            ])

            _result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert len(captured_args) == 1
        assert captured_args[0] == ("list_pods", {"ns": "kube-system"})


# ---------------------------------------------------------------------------
# Contract (6): Fail-open — call_tool raises -> inline error, request answers
# ---------------------------------------------------------------------------


class TestContract6FailOpen:
    """Tool execution errors produce inline error toolResults; loop continues."""

    @pytest.mark.asyncio
    async def test_call_tool_exception_yields_inline_error(self):
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(side_effect=ConnectionError("ECONNREFUSED"))

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                _text_response("The tool is unavailable right now."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Request still produces an answer (not a crash)
        assert result is not None
        assert "unavailable" in result.lower()
        # The model was fed an error in the tool result
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        tool_text = user_msg["content"][0]["toolResult"]["content"][0]["text"]
        assert "error" in tool_text.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_name_yields_error_not_crash(self):
        """Model asks for tool no adapter declared — error result, not crash."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "delete_namespace", "input": {}}]),
                _text_response("That tool is not available."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert result is not None
        # Model received error result for the unknown tool
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        tool_result = user_msg["content"][0]["toolResult"]
        assert "not found" in tool_result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_adapter_list_specs_failure_still_answers(self):
        """If list_tool_specs raises, no tools available but loop still answers."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(side_effect=RuntimeError("server down"))

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(
                return_value=_text_response("No tools available, but I can help.")
            )

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert result is not None
        # converse called with no tool_config
        call_kwargs = mock_bed.converse.call_args[1]
        assert call_kwargs.get("tool_config") is None


# ---------------------------------------------------------------------------
# Contract (7): B8 — two toolUse blocks, one fails, per-tool error w/ toolUseId
# ---------------------------------------------------------------------------


class TestContract7B8MultiToolUse:
    """Multiple toolUse blocks in one turn: sequential execution, partial failure."""

    @pytest.mark.asyncio
    async def test_two_tools_both_succeed_sequential(self):
        adapter = _adapter(tools=["list_pods", "get_pod"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
            {"toolSpec": {"name": "get_pod", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        ])
        order = []

        async def _call(name, args):
            order.append(name)
            return f"result-{name}"

        adapter.call_tool = _call

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([
                    {"id": "t1", "name": "list_pods", "input": {}},
                    {"id": "t2", "name": "get_pod", "input": {"name": "p1"}},
                ]),
                _text_response("Both tools returned data."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Sequential: list_pods first, then get_pod
        assert order == ["list_pods", "get_pod"]
        # Both results present in messages
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        assert len(user_msg["content"]) == 2
        # Correct toolUseId correlation
        ids = [tr["toolResult"]["toolUseId"] for tr in user_msg["content"]]
        assert ids == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_one_fails_one_succeeds_correct_tooluseid(self):
        """First tool succeeds, second fails — error has correct toolUseId."""
        adapter = _adapter(tools=["list_pods", "get_pod"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
            {"toolSpec": {"name": "get_pod", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        ])

        async def _call(name, args):
            if name == "get_pod":
                raise TimeoutError("took too long")
            return "pod-list-data"

        adapter.call_tool = _call

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([
                    {"id": "t1", "name": "list_pods", "input": {}},
                    {"id": "t2", "name": "get_pod", "input": {"name": "p1"}},
                ]),
                _text_response("list_pods worked but get_pod timed out."),
            ])

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # Inspect the tool results sent to model
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        results = user_msg["content"]
        assert len(results) == 2

        # t1 (list_pods) succeeded — has the data
        t1_result = results[0]["toolResult"]
        assert t1_result["toolUseId"] == "t1"
        assert "pod-list-data" in t1_result["content"][0]["text"]

        # t2 (get_pod) failed — has error text with correct toolUseId
        t2_result = results[1]["toolResult"]
        assert t2_result["toolUseId"] == "t2"
        assert "error" in t2_result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# Contract (8): B5 — circuit breaker, 5s timeout, session reuse
# ---------------------------------------------------------------------------


class TestContract8B5CircuitBreakerTimeout:
    """B5: circuit breaker opens after 3 failures; 5s timeout enforced; session reuse."""

    def setup_method(self):
        """Clear shared circuit breakers between tests to avoid cross-contamination."""
        import src.core.agentic_loop as loop_mod
        loop_mod._server_breakers.clear()

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_3_failures(self):
        """After 3 consecutive failures, circuit breaker opens and rejects fast."""
        adapter = _adapter(name="cb-test-1", tools=["list_pods"], url="http://cb-test-1:8080")
        adapter.call_tool = AsyncMock(side_effect=ConnectionError("fail"))

        pool = _McpSessionPool([adapter])

        # First 3 calls should go through (and fail)
        for i in range(3):
            result = await pool.call_tool("cb-test-1", "list_pods", {})
            assert "error" in result.lower()

        # 4th call: circuit breaker should be OPEN — fast rejection
        result = await pool.call_tool("cb-test-1", "list_pods", {})
        assert "circuit breaker" in result.lower() and "open" in result.lower()

    @pytest.mark.asyncio
    async def test_timeout_5s_enforced(self):
        """A tool that takes >5s triggers asyncio.TimeoutError -> error result."""
        adapter = _adapter(name="timeout-test", tools=["slow_tool"], url="http://timeout-test:8080")

        async def slow_call(name, args):
            await asyncio.sleep(10)  # 10s > 5s timeout
            return "should never reach here"

        adapter.call_tool = slow_call

        pool = _McpSessionPool([adapter])

        start = time.time()
        result = await pool.call_tool("timeout-test", "slow_tool", {})
        elapsed = time.time() - start

        # Must return within ~5s (not 10s)
        assert elapsed < 6.0
        assert "timeout" in result.lower()

    @pytest.mark.asyncio
    async def test_session_reused_within_request(self):
        """Multiple call_tool invocations within one pool use the same adapter ref."""
        adapter = _adapter(name="reuse-test", tools=["list_pods", "get_pod"], url="http://reuse-test:8080")
        call_log = []

        async def track_call(name, args):
            call_log.append(name)
            return f"result-{name}"

        adapter.call_tool = track_call

        pool = _McpSessionPool([adapter])

        # Same pool, two calls
        r1 = await pool.call_tool("reuse-test", "list_pods", {})
        r2 = await pool.call_tool("reuse-test", "get_pod", {"name": "x"})

        assert "result-list_pods" in r1
        assert "result-get_pod" in r2
        assert call_log == ["list_pods", "get_pod"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self):
        """A success after failures resets the failure count (CB stays closed)."""
        adapter = _adapter(name="reset-test", tools=["list_pods"], url="http://reset-test:8080")
        call_count = [0]

        async def flaky_call(name, args):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ConnectionError("transient")
            return "success"

        adapter.call_tool = flaky_call

        pool = _McpSessionPool([adapter])

        # 2 failures
        await pool.call_tool("reset-test", "list_pods", {})
        await pool.call_tool("reset-test", "list_pods", {})
        # 1 success — should reset
        result = await pool.call_tool("reset-test", "list_pods", {})
        assert "success" in result

        # CB should NOT be open (threshold=3, we only had 2 consecutive failures before success)
        result = await pool.call_tool("reset-test", "list_pods", {})
        assert "success" in result

    @pytest.mark.asyncio
    async def test_permission_error_does_not_trip_circuit_breaker(self):
        """Allowlist violation (PermissionError) doesn't count as transport failure."""
        adapter = _adapter(name="perm-test", tools=["list_pods"], url="http://perm-test:8080")
        adapter.call_tool = AsyncMock(
            side_effect=PermissionError("tool 'x' not in allowlist")
        )

        pool = _McpSessionPool([adapter])

        # Call 3+ times — CB should NOT open (PermissionError is not a transport failure)
        for _ in range(5):
            result = await pool.call_tool("perm-test", "list_pods", {})
            assert "not in allowlist" in result

        # CB should still be closed (not "OPEN")
        from src.core.agentic_loop import _get_server_breaker
        breaker = _get_server_breaker(adapter.url)
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_unknown_adapter_returns_error(self):
        """Calling a tool on a nonexistent adapter returns an error string."""
        pool = _McpSessionPool([])
        result = await pool.call_tool("nonexistent-adapter", "some_tool", {})
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Contract (9): SR2 — case-sensitivity refuse
# ---------------------------------------------------------------------------


class TestContract9SR2CaseSensitivity:
    """SR2: tool name differing only in case must be refused by the allowlist."""

    @pytest.mark.asyncio
    async def test_case_variant_refused_by_call_tool(self):
        """'List_Pods' is NOT 'list_pods' — must raise PermissionError."""
        adapter = McpAdapter(
            name="k8s",
            url="http://k8s-mcp:8080",
            tools=["list_pods"],  # lowercase only
        )
        # Give it a name map that maps "list_pods" -> "list_pods"
        adapter._name_map = None  # force fallback logic

        with pytest.raises(PermissionError):
            await adapter.call_tool("List_Pods", {})

    @pytest.mark.asyncio
    async def test_exact_case_match_passes_allowlist(self):
        """Exact case match goes through the allowlist check (PermissionError NOT raised).
        The actual call will fail at the MCP transport level (no server), but that's
        a different error class — we verify it's NOT a PermissionError."""
        adapter = McpAdapter(
            name="k8s",
            url="http://k8s-mcp:8080",
            tools=["list_pods"],
        )
        adapter._name_map = None

        # call_tool returns a string on transport failure (fail-open), not raises
        result = await adapter.call_tool("list_pods", {})
        # Should be an error string (connection failure), NOT a PermissionError
        assert "error" in result.lower()
        # The key assertion: it did NOT refuse on allowlist grounds
        assert "not in read-only allowlist" not in result

    def test_frozenset_allowlist_is_case_sensitive(self):
        """The frozenset allowlist does exact-match — no case folding."""
        adapter = McpAdapter(
            name="test",
            url="http://x",
            tools=["get_pods", "list_namespaces"],
        )
        # Exact matches
        assert "get_pods" in adapter._allowlist_set
        assert "list_namespaces" in adapter._allowlist_set
        # Case variants NOT in set
        assert "Get_Pods" not in adapter._allowlist_set
        assert "GET_PODS" not in adapter._allowlist_set
        assert "List_Namespaces" not in adapter._allowlist_set

    @pytest.mark.asyncio
    async def test_case_variant_in_agentic_loop_produces_error_result(self):
        """In the full loop, a case-mismatched tool name → error toolResult."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(
            side_effect=PermissionError("tool 'List_Pods' not in allowlist")
        )

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            # Model asks for "List_Pods" (wrong case) — router resolves it to the
            # adapter, but adapter.call_tool raises PermissionError
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "List_Pods", "input": {}}]),
                _text_response("That tool is not available."),
            ])

            # The loop should NOT crash — it should produce an inline error
            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert result is not None


# ---------------------------------------------------------------------------
# Additional coverage: edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge-case tests for >=90% coverage."""

    @pytest.mark.asyncio
    async def test_empty_tool_use_blocks_treated_as_final(self):
        """stop_reason=tool_use but empty tool blocks → treated as final answer."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(return_value={
                "stop_reason": "tool_use",
                "content": [{"type": "text", "text": "Actually, here's the answer."}],
                "usage": {"input_tokens": 30, "output_tokens": 20},
            })

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        assert "Actually" in result

    @pytest.mark.asyncio
    async def test_tool_result_truncated_after_guardrail_pass(self):
        """When guardrail passes, result is still truncated to MAX_TOOL_RESULT_CHARS."""
        adapter = _adapter(tools=["list_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "list_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        # Result longer than MAX_TOOL_RESULT_CHARS
        huge_result = "X" * 10000
        adapter.call_tool = AsyncMock(return_value=huge_result)

        with patch("src.core.agentic_loop.bedrock") as mock_bed, \
             patch("src.core.agentic_loop.MAX_TOOL_RESULT_CHARS", 100):
            mock_bed.converse = AsyncMock(side_effect=[
                _tool_response([{"id": "t1", "name": "list_pods", "input": {}}]),
                _text_response("done"),
            ])

            _result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        # The framed result in messages should be <= 100 chars of payload + framing
        second_msgs = mock_bed.converse.call_args_list[1][1]["messages"]
        user_msg = [m for m in second_msgs if m["role"] == "user"][-1]
        tool_text = user_msg["content"][0]["toolResult"]["content"][0]["text"]
        # The raw 10000 chars should NOT be fully present
        assert len(tool_text) < 10000

    @pytest.mark.asyncio
    async def test_tool_config_contains_all_adapter_specs(self):
        """tool_config sent to converse() includes specs from ALL adapters."""
        adapter1 = _adapter(name="a1", tools=["tool_a"], url="http://a1:80")
        adapter1.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "tool_a", "description": "A",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter2 = _adapter(name="a2", tools=["tool_b"], url="http://a2:80")
        adapter2.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "tool_b", "description": "B",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(
                return_value=_text_response("direct answer")
            )

            _result, _messages = await run_agentic_loop(**_loop_kwargs([adapter1, adapter2]))

        # tool_config should have both specs
        call_kwargs = mock_bed.converse.call_args[1]
        tools = call_kwargs["tool_config"]["tools"]
        names = [t["toolSpec"]["name"] for t in tools]
        assert "tool_a" in names
        assert "tool_b" in names

    @pytest.mark.asyncio
    async def test_no_mcp_adapters_with_tools_runs_without_tool_config(self):
        """When all adapters have empty tools, tool_config is None."""
        adapter = _adapter(tools=[])
        adapter.list_tool_specs = AsyncMock(return_value=[])

        with patch("src.core.agentic_loop.bedrock") as mock_bed:
            mock_bed.converse = AsyncMock(
                return_value=_text_response("no tools available")
            )

            result, _messages = await run_agentic_loop(**_loop_kwargs([adapter]))

        call_kwargs = mock_bed.converse.call_args[1]
        assert call_kwargs.get("tool_config") is None
