"""Tests for the MCP → Converse tool-schema normalizer and adapter agentic interface (Phase 2)."""
from __future__ import annotations

import pytest

from src.core.tool_schema import (
    MAX_NESTING_DEPTH,
    ToolNameMap,
    build_tool_spec,
    normalize_input_schema,
)


# ---------------------------------------------------------------------------
# ToolNameMap tests
# ---------------------------------------------------------------------------


class TestToolNameMap:
    def test_register_simple(self):
        m = ToolNameMap()
        result = m.register("get-pods")
        assert result == "get_pods"
        assert m.to_server_name("get_pods") == "get-pods"
        assert m.to_converse_name("get-pods") == "get_pods"

    def test_register_no_hyphens(self):
        m = ToolNameMap()
        result = m.register("list_nodes")
        assert result == "list_nodes"
        assert m.to_server_name("list_nodes") == "list_nodes"

    def test_register_collision_disambiguation(self):
        m = ToolNameMap()
        # Both would map to "get_thing"
        r1 = m.register("get-thing")
        r2 = m.register("get_thing")
        assert r1 == "get_thing"
        assert r2 == "get_thing_1"
        assert m.to_server_name("get_thing") == "get-thing"
        assert m.to_server_name("get_thing_1") == "get_thing"

    def test_to_server_name_unknown(self):
        m = ToolNameMap()
        assert m.to_server_name("nonexistent") is None

    def test_has_converse(self):
        m = ToolNameMap()
        m.register("check-health")
        assert m.has_converse("check_health") is True
        assert m.has_converse("unknown") is False


# ---------------------------------------------------------------------------
# normalize_input_schema tests
# ---------------------------------------------------------------------------


class TestNormalizeInputSchema:
    def test_none_returns_generic_object(self):
        result = normalize_input_schema(None)
        assert result == {"type": "object", "properties": {}}

    def test_empty_dict_returns_generic_object(self):
        result = normalize_input_schema({})
        assert result == {"type": "object", "properties": {}}

    def test_simple_schema_preserved(self):
        schema = {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "K8s namespace"},
            },
            "required": ["namespace"],
        }
        result = normalize_input_schema(schema)
        assert result["type"] == "object"
        assert "namespace" in result["properties"]
        assert result["properties"]["namespace"]["type"] == "string"
        assert result["required"] == ["namespace"]

    def test_ref_resolution_inline(self):
        schema = {
            "type": "object",
            "properties": {
                "target": {"$ref": "#/$defs/Target"},
            },
            "$defs": {
                "Target": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
        }
        result = normalize_input_schema(schema)
        # $ref should be resolved; $defs stripped
        assert "$defs" not in result
        assert "$ref" not in result["properties"]["target"]
        assert result["properties"]["target"]["type"] == "object"
        assert "name" in result["properties"]["target"]["properties"]

    def test_ref_definitions_syntax(self):
        schema = {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/definitions/Item"},
            },
            "definitions": {
                "Item": {"type": "string"},
            },
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["item"]["type"] == "string"
        assert "definitions" not in result

    def test_unresolvable_ref_collapses_to_object(self):
        schema = {
            "type": "object",
            "properties": {
                "thing": {"$ref": "#/$defs/Missing"},
            },
            "$defs": {},
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["thing"] == {"type": "object"}

    def test_anyof_single_non_null_unwraps(self):
        schema = {
            "type": "object",
            "properties": {
                "limit": {
                    "anyOf": [
                        {"type": "integer"},
                        {"type": "null"},
                    ],
                    "description": "Max results",
                },
            },
        }
        result = normalize_input_schema(schema)
        prop = result["properties"]["limit"]
        assert prop["type"] == "integer"
        assert prop["description"] == "Max results"

    def test_anyof_multiple_real_types_collapses(self):
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "integer"},
                    ],
                    "description": "Could be string or int",
                },
            },
        }
        result = normalize_input_schema(schema)
        prop = result["properties"]["value"]
        assert prop["type"] == "object"
        assert prop["description"] == "Could be string or int"

    def test_oneof_simplification(self):
        schema = {
            "type": "object",
            "properties": {
                "field": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ],
                },
            },
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["field"]["type"] == "string"

    def test_depth_capping(self):
        # Build a schema nested beyond MAX_NESTING_DEPTH
        inner = {"type": "string"}
        for _ in range(MAX_NESTING_DEPTH + 2):
            inner = {"type": "object", "properties": {"nested": inner}}
        schema = inner
        result = normalize_input_schema(schema)

        # Walk down to verify it caps at the limit
        current = result
        depth = 0
        while "properties" in current and "nested" in current.get("properties", {}):
            current = current["properties"]["nested"]
            depth += 1
            if depth > MAX_NESTING_DEPTH + 5:
                break  # Safety — should never hit this
        # At some point, nested should become a bare {type: object}
        assert depth <= MAX_NESTING_DEPTH + 1

    def test_unsupported_keys_stripped(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name",
                    "examples": ["foo", "bar"],  # unsupported
                    "deprecated": True,  # unsupported
                },
            },
            "$schema": "http://json-schema.org/draft-07/schema#",  # unsupported at top level
        }
        result = normalize_input_schema(schema)
        assert "$schema" not in result
        prop = result["properties"]["name"]
        assert "examples" not in prop
        assert "deprecated" not in prop
        assert prop["type"] == "string"
        assert prop["description"] == "The name"

    def test_non_object_top_level_fixed(self):
        schema = {"type": "array", "items": {"type": "string"}}
        result = normalize_input_schema(schema)
        # Should be wrapped/fixed to object
        assert result["type"] == "object"


# ---------------------------------------------------------------------------
# build_tool_spec tests
# ---------------------------------------------------------------------------


class TestBuildToolSpec:
    def test_basic_spec(self):
        name_map = ToolNameMap()
        result = build_tool_spec(
            server_name="get-pods",
            description="List pods in a namespace",
            input_schema={
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
            },
            name_map=name_map,
        )
        assert "toolSpec" in result
        spec = result["toolSpec"]
        assert spec["name"] == "get_pods"
        assert spec["description"] == "List pods in a namespace"
        assert spec["inputSchema"]["json"]["type"] == "object"

    def test_missing_description_uses_fallback(self):
        name_map = ToolNameMap()
        result = build_tool_spec(
            server_name="my-tool",
            description="",
            input_schema=None,
            name_map=name_map,
        )
        spec = result["toolSpec"]
        assert "my-tool" in spec["description"]

    def test_long_description_truncated(self):
        name_map = ToolNameMap()
        long_desc = "x" * 2000
        result = build_tool_spec(
            server_name="tool",
            description=long_desc,
            input_schema=None,
            name_map=name_map,
        )
        assert len(result["toolSpec"]["description"]) <= 1024

    def test_none_schema_gets_generic(self):
        name_map = ToolNameMap()
        result = build_tool_spec(
            server_name="empty-schema-tool",
            description="No schema",
            input_schema=None,
            name_map=name_map,
        )
        json_schema = result["toolSpec"]["inputSchema"]["json"]
        assert json_schema == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# McpAdapter.call_tool fail-closed tests
# ---------------------------------------------------------------------------


class TestMcpAdapterCallToolFailClosed:
    """Test that call_tool enforces the allowlist fail-closed."""

    @pytest.mark.asyncio
    async def test_call_tool_refuses_non_allowlisted(self):
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="test",
            url="http://fake:8080",
            tools=["allowed_tool"],
        )
        with pytest.raises(PermissionError, match="not in read-only allowlist"):
            await adapter.call_tool("forbidden_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_refuses_hyphen_variant_not_allowlisted(self):
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="test",
            url="http://fake:8080",
            tools=["some_tool"],
        )
        # "not-in-list" does not map to anything in the allowlist
        with pytest.raises(PermissionError, match="not in read-only allowlist"):
            await adapter.call_tool("not_in_list", {})

    @pytest.mark.asyncio
    async def test_call_tool_resolves_hyphen_name_via_map(self):
        """If name map is populated, underscore→hyphen resolution works."""
        from src.core.adapters import McpAdapter
        from src.core.tool_schema import ToolNameMap

        adapter = McpAdapter(
            name="test",
            url="http://fake:8080",
            tools=["get-pods"],
        )
        # Simulate that list_tool_specs populated the name map
        name_map = ToolNameMap()
        name_map.register("get-pods")
        adapter._name_map = name_map

        # The tool IS allowlisted (as "get-pods"), but model calls it as "get_pods"
        # This should NOT raise PermissionError (it resolves correctly).
        # It will fail on MCP connection (which is expected — we only test allowlist logic)
        # We patch to avoid the actual connection
        result = await adapter.call_tool("get_pods", {})
        # Should fail on connection, NOT on permission
        assert "error" in result
        assert "not in read-only allowlist" not in result


# ---------------------------------------------------------------------------
# McpAdapter.list_tool_specs tests (mocked MCP session)
# ---------------------------------------------------------------------------


class TestMcpAdapterListToolSpecs:
    @pytest.mark.asyncio
    async def test_empty_allowlist_returns_empty(self):
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="empty",
            url="http://fake:8080",
            tools=[],
        )
        result = await adapter.list_tool_specs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_tool_specs_caches_result(self):
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="cached",
            url="http://fake:8080",
            tools=["tool_a"],
        )
        adapter.cache_ttl = 300
        # Pre-populate cache
        adapter._cached_tool_specs = [{"toolSpec": {"name": "tool_a"}}]
        import time
        adapter._specs_cached_at = time.time()

        result = await adapter.list_tool_specs()
        assert len(result) == 1
        assert result[0]["toolSpec"]["name"] == "tool_a"


# ---------------------------------------------------------------------------
# agent_config loop budget tests
# ---------------------------------------------------------------------------


class TestAgentConfigLoopBudget:
    def test_defaults_present(self):
        from src.core.agent_config import (
            MAX_LOOP_DURATION_MS,
            MAX_LOOP_TOKENS,
            MAX_TOOL_RESULT_CHARS,
            MAX_TOOL_STEPS,
        )
        assert MAX_TOOL_STEPS == 5
        assert MAX_LOOP_DURATION_MS == 15000
        assert MAX_LOOP_TOKENS == 50000
        assert MAX_TOOL_RESULT_CHARS == 8000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AIGENT_MAX_TOOL_STEPS", "10")
        monkeypatch.setenv("AIGENT_MAX_LOOP_DURATION_MS", "30000")
        monkeypatch.setenv("AIGENT_MAX_LOOP_TOKENS", "100000")
        monkeypatch.setenv("AIGENT_MAX_TOOL_RESULT_CHARS", "8000")
        # Re-import to pick up env
        import importlib
        import src.core.agent_config as ac
        importlib.reload(ac)
        assert ac.MAX_TOOL_STEPS == 10
        assert ac.MAX_LOOP_DURATION_MS == 30000
        assert ac.MAX_LOOP_TOKENS == 100000
        assert ac.MAX_TOOL_RESULT_CHARS == 8000
        # Reset for other tests
        monkeypatch.delenv("AIGENT_MAX_TOOL_STEPS")
        monkeypatch.delenv("AIGENT_MAX_LOOP_DURATION_MS")
        monkeypatch.delenv("AIGENT_MAX_LOOP_TOKENS")
        monkeypatch.delenv("AIGENT_MAX_TOOL_RESULT_CHARS")
        importlib.reload(ac)
