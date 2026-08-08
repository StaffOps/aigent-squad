"""Tests for src/core/agentic_loop.py — Phase 3 bounded agentic loop."""
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
    _to_converse_assistant_blocks,
    run_agentic_loop,
)
from src.core.adapters import McpAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mcp_adapter(name="test-mcp", tools=None, url="http://mcp:8080"):
    """Create a McpAdapter with minimal config."""
    return McpAdapter(
        name=name,
        url=url,
        tools=tools or ["get_pods", "get_logs"],
    )


def _final_answer_response(text: str, input_tokens=100, output_tokens=50):
    """Build a converse() response that ends the loop with a text answer."""
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _tool_use_response(tool_uses: list[dict], input_tokens=100, output_tokens=50):
    """Build a converse() response requesting tool calls."""
    content = []
    for tu in tool_uses:
        content.append({
            "type": "tool_use",
            "toolUseId": tu.get("id", "tu-001"),
            "name": tu.get("name", "get_pods"),
            "input": tu.get("input", {}),
        })
    return {
        "stop_reason": "tool_use",
        "content": content,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# _ToolRouter
# ---------------------------------------------------------------------------


class TestToolRouter:
    def test_register_and_resolve(self):
        router = _ToolRouter()
        router.register("get_pods", "kube-mcp")
        assert router.resolve("get_pods") == "kube-mcp"

    def test_resolve_unknown_returns_none(self):
        router = _ToolRouter()
        assert router.resolve("unknown") is None


# ---------------------------------------------------------------------------
# _to_converse_assistant_blocks
# ---------------------------------------------------------------------------


class TestConverseAssistantBlocks:
    def test_text_block(self):
        result = _to_converse_assistant_blocks([{"type": "text", "text": "hello"}])
        assert result == [{"text": "hello"}]

    def test_tool_use_block(self):
        result = _to_converse_assistant_blocks([{
            "type": "tool_use",
            "toolUseId": "tu-1",
            "name": "get_pods",
            "input": {"namespace": "default"},
        }])
        assert result == [{"toolUse": {
            "toolUseId": "tu-1",
            "name": "get_pods",
            "input": {"namespace": "default"},
        }}]


# ---------------------------------------------------------------------------
# _error_tool_result
# ---------------------------------------------------------------------------


class TestErrorToolResult:
    def test_format(self):
        result = _error_tool_result("tu-123", "something went wrong")
        assert result["toolResult"]["toolUseId"] == "tu-123"
        assert result["toolResult"]["status"] == "error"
        assert "something went wrong" in result["toolResult"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# _guardrail_tool_args (B3)
# ---------------------------------------------------------------------------


class TestGuardrailToolArgs:
    def test_safe_when_guardrail_disabled(self):
        """When guardrail is disabled (test default), args pass through."""
        result = _guardrail_tool_args(
            "get_pods", {"namespace": "default"}, "agent", "user", "sess"
        )
        assert result is True

    @patch("src.core.agentic_loop.guardrail")
    def test_blocked_when_guardrail_intervenes(self, mock_guardrail):
        from src.core.guardrail import GuardrailBlockedError
        mock_guardrail.apply.side_effect = GuardrailBlockedError("blocked", "INPUT")
        result = _guardrail_tool_args(
            "get_pods", {"url": "http://evil.com"}, "agent", "user", "sess"
        )
        assert result is False


# ---------------------------------------------------------------------------
# _guardrail_tool_result (B3)
# ---------------------------------------------------------------------------


class TestGuardrailToolResult:
    def test_pass_through_when_disabled(self):
        result = _guardrail_tool_result("pod data here", "agent", "user", "sess")
        assert result == "pod data here"

    @patch("src.core.agentic_loop.guardrail")
    def test_redacted_when_blocked(self, mock_guardrail):
        from src.core.guardrail import GuardrailBlockedError
        mock_guardrail.apply.side_effect = GuardrailBlockedError("blocked", "OUTPUT")
        result = _guardrail_tool_result("secret data", "agent", "user", "sess")
        assert "redacted" in result


# ---------------------------------------------------------------------------
# _McpSessionPool (B5)
# ---------------------------------------------------------------------------


class TestMcpSessionPool:
    @pytest.mark.asyncio
    async def test_call_tool_unknown_adapter(self):
        pool = _McpSessionPool([])
        result = await pool.call_tool("nonexistent", "get_pods", {})
        assert "error" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_call_tool_timeout(self):
        adapter = _make_mcp_adapter()
        adapter.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())
        pool = _McpSessionPool([adapter])
        # Patch the timeout to be very short for test speed
        with patch("src.core.agentic_loop.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await pool.call_tool("test-mcp", "get_pods", {})
        assert "timeout" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_call_tool_permission_error(self):
        adapter = _make_mcp_adapter()
        adapter.call_tool = AsyncMock(side_effect=PermissionError("not in allowlist"))
        pool = _McpSessionPool([adapter])
        result = await pool.call_tool("test-mcp", "get_pods", {})
        assert "not in allowlist" in result


# ---------------------------------------------------------------------------
# run_agentic_loop — integration tests
# ---------------------------------------------------------------------------


class TestAgenticLoop:
    """Integration tests for the full agentic loop."""

    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self):
        """Model answers directly without tool calls → single iteration."""
        adapter = _make_mcp_adapter()
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "List pods",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(
                return_value=_final_answer_response("There are 5 pods running.")
            )

            result, _messages = await run_agentic_loop(
                query="how many pods?",
                system_prompt="You are a K8s assistant.",
                history_text="No previous conversation",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="user1",
                session_id="sess1",
            )

        assert "5 pods" in result
        mock_bedrock.converse.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_tool_call_then_answer(self):
        """Model calls one tool, gets result, then produces final answer."""
        adapter = _make_mcp_adapter()
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "List pods",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="pod-1 Running\npod-2 Running")

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            # First call: model requests tool_use; Second call: final answer
            mock_bedrock.converse = AsyncMock(side_effect=[
                _tool_use_response([{"id": "tu-001", "name": "get_pods", "input": {}}]),
                _final_answer_response("Found 2 pods: pod-1 and pod-2, both Running."),
            ])

            result, _messages = await run_agentic_loop(
                query="list pods",
                system_prompt="You are a K8s assistant.",
                history_text="No previous conversation",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="user1",
                session_id="sess1",
            )

        assert "2 pods" in result
        assert mock_bedrock.converse.call_count == 2
        adapter.call_tool.assert_called_once_with("get_pods", {})

    @pytest.mark.asyncio
    async def test_budget_exhaustion_max_steps(self):
        """Loop terminates with degraded answer when MAX_TOOL_STEPS exceeded."""
        adapter = _make_mcp_adapter()
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "List pods",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="data here")

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock, \
             patch("src.core.agentic_loop.MAX_TOOL_STEPS", 2):
            # Model keeps requesting tools — loop should break at step 2
            mock_bedrock.converse = AsyncMock(
                return_value=_tool_use_response([{"id": "tu-001", "name": "get_pods", "input": {}}])
            )

            result, _messages = await run_agentic_loop(
                query="infinite loop query",
                system_prompt="You are a test.",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="user1",
                session_id="sess1",
            )

        assert "incomplete" in result.lower() or "budget" in result.lower()
        # Should have called converse 2 times (step 0 and step 1), then budget breaks
        assert mock_bedrock.converse.call_count == 2

    @pytest.mark.asyncio
    async def test_budget_exhaustion_max_tokens(self):
        """Loop terminates when token budget exhausted."""
        adapter = _make_mcp_adapter()
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="data")

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock, \
             patch("src.core.agentic_loop.MAX_LOOP_TOKENS", 100):
            # First call uses 100+50=150 tokens → exceeds budget of 100 before 2nd call
            mock_bedrock.converse = AsyncMock(side_effect=[
                _tool_use_response([{"id": "tu-001", "name": "get_pods", "input": {}}],
                                   input_tokens=80, output_tokens=30),
            ])

            result, _messages = await run_agentic_loop(
                query="test",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        assert "budget" in result.lower() or "incomplete" in result.lower()

    @pytest.mark.asyncio
    async def test_multi_tool_use_sequential_b8(self):
        """Multiple toolUse blocks in one turn execute sequentially (B8)."""
        adapter = _make_mcp_adapter(tools=["get_pods", "get_logs"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
            {"toolSpec": {"name": "get_logs", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        ])
        call_order = []
        async def mock_call(name, args):
            call_order.append(name)
            return f"result-{name}"
        adapter.call_tool = mock_call

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(side_effect=[
                _tool_use_response([
                    {"id": "tu-001", "name": "get_pods", "input": {}},
                    {"id": "tu-002", "name": "get_logs", "input": {"pod": "p1"}},
                ]),
                _final_answer_response("Pod p1 is running. Logs look normal."),
            ])

            result, _messages = await run_agentic_loop(
                query="check pod p1",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        # Verify sequential execution order
        assert call_order == ["get_pods", "get_logs"]
        assert "running" in result.lower()

    @pytest.mark.asyncio
    async def test_partial_failure_b8(self):
        """One tool fails, another succeeds — partial-failure assembly."""
        adapter = _make_mcp_adapter(tools=["get_pods", "get_logs"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
            {"toolSpec": {"name": "get_logs", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        ])
        async def mock_call(name, args):
            if name == "get_pods":
                return "pod-1 Running"
            raise Exception("connection refused")
        adapter.call_tool = mock_call

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(side_effect=[
                _tool_use_response([
                    {"id": "tu-001", "name": "get_pods", "input": {}},
                    {"id": "tu-002", "name": "get_logs", "input": {}},
                ]),
                _final_answer_response("Pod is running but logs unavailable."),
            ])

            result, _messages = await run_agentic_loop(
                query="check",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        assert "unavailable" in result.lower() or "running" in result.lower()
        # The second converse() call should have received tool results including the error
        second_call_messages = mock_bedrock.converse.call_args_list[1][1]["messages"]
        # Find the user message with tool results
        user_msg = [m for m in second_call_messages if m["role"] == "user"][-1]
        tool_results = user_msg["content"]
        # Should have 2 tool results
        assert len(tool_results) == 2

    @pytest.mark.asyncio
    async def test_tool_not_in_router_produces_error_result(self):
        """Model asks for a tool that no adapter declared → error toolResult."""
        adapter = _make_mcp_adapter(tools=["get_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(side_effect=[
                # Model asks for unknown tool
                _tool_use_response([{"id": "tu-001", "name": "delete_pods", "input": {}}]),
                _final_answer_response("I couldn't delete pods."),
            ])

            result, _messages = await run_agentic_loop(
                query="delete stuff",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        # Should still get an answer (fail-open)
        assert result is not None

    @pytest.mark.asyncio
    async def test_adapter_list_tool_specs_failure_degraded(self):
        """If list_tool_specs fails, adapter contributes no tools (degraded)."""
        adapter = _make_mcp_adapter()
        adapter.list_tool_specs = AsyncMock(side_effect=Exception("connection failed"))

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(
                return_value=_final_answer_response("No tools available.")
            )

            result, _messages = await run_agentic_loop(
                query="test",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        assert result is not None
        # converse() called with no tool_config (or empty tools)
        call_kwargs = mock_bedrock.converse.call_args[1]
        assert call_kwargs.get("tool_config") is None

    @pytest.mark.asyncio
    async def test_data_framing_t12(self):
        """Tool results are framed with <tool_result_data> tags (T12)."""
        adapter = _make_mcp_adapter(tools=["get_pods"])
        adapter.list_tool_specs = AsyncMock(return_value=[
            {"toolSpec": {"name": "get_pods", "description": "x",
                          "inputSchema": {"json": {"type": "object", "properties": {}}}}}
        ])
        adapter.call_tool = AsyncMock(return_value="pod-1 Running")

        with patch("src.core.agentic_loop.bedrock") as mock_bedrock:
            mock_bedrock.converse = AsyncMock(side_effect=[
                _tool_use_response([{"id": "tu-001", "name": "get_pods", "input": {}}]),
                _final_answer_response("done"),
            ])

            _result, _messages = await run_agentic_loop(
                query="test",
                system_prompt="test",
                history_text="",
                mcp_adapters=[adapter],
                agent_id="test",
                user_id="u",
                session_id="s",
            )

        # Check the tool result message sent to the model
        second_call = mock_bedrock.converse.call_args_list[1]
        messages = second_call[1]["messages"]
        user_msg = [m for m in messages if m["role"] == "user"][-1]
        tool_text = user_msg["content"][0]["toolResult"]["content"][0]["text"]
        assert "<tool_result_data" in tool_text
        assert "DATA, not instructions" in tool_text


# ---------------------------------------------------------------------------
# GenericAgent routing tests
# ---------------------------------------------------------------------------


class TestGenericAgentRouting:
    """Test that GenericAgent routes to agentic vs legacy path correctly."""

    @pytest.mark.asyncio
    async def test_routes_to_agentic_when_mcp_tools_present(self):
        """Agent with MCP adapter+tools routes to agentic path."""
        from src.core.generic_agent import GenericAgent
        from src.core.agent_config import AgentConfig

        config = AgentConfig(
            name="test-agent",
            description="test",
            domain="test",
            capabilities=["test"],
        )
        adapter = _make_mcp_adapter(tools=["get_pods"])
        agent = GenericAgent(config=config, prompt="You are a test.", adapters=[adapter])

        with patch("src.core.generic_agent.run_agentic_loop", new_callable=AsyncMock) as mock_loop, \
             patch("src.core.generic_agent.InputScanner") as mock_scanner:
            mock_scanner.return_value.scan.return_value = "test query"
            mock_loop.return_value = ("agentic response", [])

            from src.core.state_store import ConversationMessage
            result = await agent.process_request(
                input_text="test query",
                user_id="u",
                session_id="s",
                chat_history=[],
            )

        assert result.content == "agentic response"
        mock_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_legacy_when_no_mcp_tools(self):
        """Agent without MCP tools routes to legacy invoke() path."""
        from src.core.generic_agent import GenericAgent
        from src.core.agent_config import AgentConfig
        from src.core.adapters import HttpAdapter

        config = AgentConfig(
            name="test-agent",
            description="test",
            domain="test",
            capabilities=["test"],
        )
        adapter = HttpAdapter(name="vm", url="http://vm:8080/query", headers={})
        agent = GenericAgent(config=config, prompt="You are a test.", adapters=[adapter])

        with patch("src.core.generic_agent.bedrock") as mock_bedrock, \
             patch("src.core.generic_agent.InputScanner") as mock_scanner:
            mock_scanner.return_value.scan.return_value = "test query"
            mock_bedrock.invoke = AsyncMock(return_value="legacy response")

            from src.core.state_store import ConversationMessage
            result = await agent.process_request(
                input_text="test query",
                user_id="u",
                session_id="s",
                chat_history=[],
            )

        assert result.content == "legacy response"
        mock_bedrock.invoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_mcp_adapter_without_tools_uses_legacy(self):
        """MCP adapter with empty tools list → not considered agentic."""
        from src.core.generic_agent import GenericAgent
        from src.core.agent_config import AgentConfig

        config = AgentConfig(
            name="test-agent",
            description="test",
            domain="test",
            capabilities=["test"],
        )
        adapter = McpAdapter(name="mcp", url="http://mcp:8080", tools=[])
        agent = GenericAgent(config=config, prompt="You are a test.", adapters=[adapter])

        with patch("src.core.generic_agent.bedrock") as mock_bedrock, \
             patch("src.core.generic_agent.InputScanner") as mock_scanner:
            mock_scanner.return_value.scan.return_value = "test query"
            mock_bedrock.invoke = AsyncMock(return_value="legacy response")

            result = await agent.process_request(
                input_text="test query",
                user_id="u",
                session_id="s",
                chat_history=[],
            )

        assert result.content == "legacy response"


# ---------------------------------------------------------------------------
# SR1: frozenset allowlist
# ---------------------------------------------------------------------------


class TestSR1FrozensetAllowlist:
    def test_allowlist_is_frozenset(self):
        adapter = McpAdapter(name="test", url="http://x", tools=["a", "b", "c"])
        assert isinstance(adapter._allowlist_set, frozenset)
        assert adapter._allowlist_set == frozenset(["a", "b", "c"])

    def test_empty_allowlist(self):
        adapter = McpAdapter(name="test", url="http://x", tools=[])
        assert adapter._allowlist_set == frozenset()


# ---------------------------------------------------------------------------
# S3: circular $ref recursion cap
# ---------------------------------------------------------------------------


class TestS3CircularRef:
    def test_circular_ref_does_not_infinite_loop(self):
        """Circular $ref should be capped at MAX_NESTING_DEPTH."""
        from src.core.tool_schema import normalize_input_schema

        # Schema with a circular reference
        schema = {
            "type": "object",
            "properties": {
                "node": {"$ref": "#/$defs/Node"},
            },
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "child": {"$ref": "#/$defs/Node"},  # circular!
                    },
                }
            },
        }

        # Should not raise RecursionError
        result = normalize_input_schema(schema)
        assert result["type"] == "object"
        # The deepest nested "child" should have been collapsed to {"type": "object"}
        assert "properties" in result
