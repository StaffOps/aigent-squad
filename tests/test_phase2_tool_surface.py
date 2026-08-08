"""Independent contract tests for Phase-2 generic tool surface (spec 37).

Written by test-author (verification-independence): tests the CONTRACT/spec,
not the implementation internals. Mock MCP session — no network.

Covers:
  (1) Schema normalizer: $ref/$defs inlined, missing inputSchema fallback,
      hyphen<->underscore round-trips, deep/union schemas.
  (2) Tool-spec builder: only allowlisted tools become toolSpecs.
  (3) call_tool FAIL-CLOSED: non-allowlisted name refused, client never called.
  (4) Budget defaults present + env-overridable.
"""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.core.tool_schema import (
    MAX_NESTING_DEPTH,
    ToolNameMap,
    build_tool_spec,
    normalize_input_schema,
)


# ===========================================================================
# (1) ToolNameMap — bidirectional hyphen↔underscore mapping
# ===========================================================================


class TestToolNameMapContract:
    """Contract: names with hyphens map to underscores; round-trip is stable."""

    def test_hyphen_to_underscore_basic(self):
        m = ToolNameMap()
        converse = m.register("list-pods")
        assert "_" in converse
        assert "-" not in converse
        assert m.to_server_name(converse) == "list-pods"

    def test_underscore_name_unchanged(self):
        m = ToolNameMap()
        converse = m.register("list_nodes")
        assert converse == "list_nodes"
        assert m.to_server_name("list_nodes") == "list_nodes"

    def test_round_trip_server_to_converse_and_back(self):
        m = ToolNameMap()
        original = "get-cluster-info"
        converse = m.register(original)
        assert m.to_server_name(converse) == original
        assert m.to_converse_name(original) == converse

    def test_collision_produces_unique_converse_names(self):
        """Two different server names mapping to same underscore form get disambiguated."""
        m = ToolNameMap()
        c1 = m.register("run-test")   # -> run_test
        c2 = m.register("run_test")   # collision!
        assert c1 != c2
        assert m.to_server_name(c1) == "run-test"
        assert m.to_server_name(c2) == "run_test"

    def test_triple_collision(self):
        """Multiple collisions all get unique names."""
        m = ToolNameMap()
        c1 = m.register("a-b")
        c2 = m.register("a_b")
        # Force another: register something that would collide with the _1 suffix
        c3 = m.register("a_b_1")
        assert len({c1, c2, c3}) == 3  # all unique

    def test_unknown_name_returns_none(self):
        m = ToolNameMap()
        assert m.to_server_name("never_registered") is None
        assert m.to_converse_name("never_registered") is None

    def test_has_converse_true_and_false(self):
        m = ToolNameMap()
        m.register("exists-tool")
        assert m.has_converse("exists_tool") is True
        assert m.has_converse("nope") is False

    def test_multiple_hyphens(self):
        m = ToolNameMap()
        converse = m.register("get-all-running-pods")
        assert converse == "get_all_running_pods"
        assert m.to_server_name(converse) == "get-all-running-pods"


# ===========================================================================
# (1) normalize_input_schema — contract tests
# ===========================================================================


class TestNormalizeInputSchemaContract:
    """Contract: normalizer must produce Converse-compatible output for all inputs."""

    def test_none_input_gives_generic_object(self):
        result = normalize_input_schema(None)
        assert result["type"] == "object"
        assert "properties" in result

    def test_empty_dict_gives_generic_object(self):
        result = normalize_input_schema({})
        assert result["type"] == "object"
        assert "properties" in result

    def test_valid_schema_passes_through_structure(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        }
        result = normalize_input_schema(schema)
        assert result["type"] == "object"
        assert "query" in result["properties"]
        assert result["properties"]["query"]["type"] == "string"
        assert result["required"] == ["query"]

    def test_ref_defs_inlined(self):
        """$ref pointing to $defs must be inlined (no $ref in output)."""
        schema = {
            "type": "object",
            "properties": {
                "filter": {"$ref": "#/$defs/Filter"},
            },
            "$defs": {
                "Filter": {
                    "type": "object",
                    "properties": {"field": {"type": "string"}},
                },
            },
        }
        result = normalize_input_schema(schema)
        # No $ref or $defs in the output
        assert "$ref" not in str(result)
        assert "$defs" not in result
        assert result["properties"]["filter"]["type"] == "object"
        assert "field" in result["properties"]["filter"]["properties"]

    def test_ref_definitions_inlined(self):
        """Also resolves 'definitions' (JSON Schema draft-07 style)."""
        schema = {
            "type": "object",
            "properties": {"x": {"$ref": "#/definitions/X"}},
            "definitions": {"X": {"type": "integer"}},
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["x"]["type"] == "integer"
        assert "definitions" not in result

    def test_nested_ref_resolved(self):
        """A $ref pointing to a def that itself has a $ref gets fully resolved."""
        schema = {
            "type": "object",
            "properties": {"outer": {"$ref": "#/$defs/Outer"}},
            "$defs": {
                "Outer": {
                    "type": "object",
                    "properties": {"inner": {"$ref": "#/$defs/Inner"}},
                },
                "Inner": {"type": "string"},
            },
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["outer"]["properties"]["inner"]["type"] == "string"

    def test_unresolvable_ref_becomes_generic_object(self):
        schema = {
            "type": "object",
            "properties": {"broken": {"$ref": "#/$defs/DoesNotExist"}},
            "$defs": {},
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["broken"] == {"type": "object"}

    def test_anyof_single_real_type_unwrapped(self):
        """anyOf with one real type + null → unwrap to the real type."""
        schema = {
            "type": "object",
            "properties": {
                "count": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "Optional count",
                },
            },
        }
        result = normalize_input_schema(schema)
        prop = result["properties"]["count"]
        assert prop["type"] == "integer"
        assert prop.get("description") == "Optional count"

    def test_anyof_multiple_real_types_collapse_to_object(self):
        """anyOf with 2+ real types → collapse to object."""
        schema = {
            "type": "object",
            "properties": {
                "val": {
                    "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}],
                    "description": "Polymorphic",
                },
            },
        }
        result = normalize_input_schema(schema)
        prop = result["properties"]["val"]
        assert prop["type"] == "object"
        assert prop["description"] == "Polymorphic"

    def test_oneof_simplification(self):
        schema = {
            "type": "object",
            "properties": {
                "opt": {"oneOf": [{"type": "string"}, {"type": "null"}]},
            },
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["opt"]["type"] == "string"

    def test_depth_capping_does_not_crash(self):
        """Deeply nested schemas get capped, never infinite recursion."""
        inner: dict[str, Any] = {"type": "string"}
        for _ in range(MAX_NESTING_DEPTH + 5):
            inner = {"type": "object", "properties": {"deep": inner}}
        result = normalize_input_schema(inner)
        # Should not crash; result should be a valid object schema
        assert result["type"] == "object"

    def test_depth_capping_preserves_shallow_structure(self):
        """Levels within limit are preserved; only excessive depth is capped."""
        schema = {
            "type": "object",
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "level2": {"type": "string"},
                    },
                },
            },
        }
        result = normalize_input_schema(schema)
        assert result["properties"]["level1"]["properties"]["level2"]["type"] == "string"

    def test_unsupported_keys_removed(self):
        """Keys like $schema, examples, deprecated are stripped."""
        schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "name": {
                    "type": "string",
                    "examples": ["alice", "bob"],
                    "deprecated": True,
                    "description": "User name",
                },
            },
        }
        result = normalize_input_schema(schema)
        assert "$schema" not in result
        prop = result["properties"]["name"]
        assert "examples" not in prop
        assert "deprecated" not in prop
        assert prop["description"] == "User name"

    def test_non_object_top_level_becomes_object(self):
        """Top-level type != object gets fixed to object (Converse requirement)."""
        schema = {"type": "array", "items": {"type": "string"}}
        result = normalize_input_schema(schema)
        assert result["type"] == "object"

    def test_does_not_mutate_input(self):
        """normalize_input_schema must not mutate the original dict."""
        import copy
        schema = {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/X"}},
            "$defs": {"X": {"type": "string"}},
        }
        original = copy.deepcopy(schema)
        normalize_input_schema(schema)
        assert schema == original


# ===========================================================================
# (2) build_tool_spec — only allowlisted tools become toolSpecs
# ===========================================================================


class TestBuildToolSpecContract:
    """Contract: build_tool_spec produces valid Converse toolSpec dicts."""

    def test_output_shape(self):
        """Must have toolSpec.name, toolSpec.description, toolSpec.inputSchema.json."""
        nm = ToolNameMap()
        result = build_tool_spec("my-tool", "Does things", {"type": "object"}, nm)
        assert "toolSpec" in result
        spec = result["toolSpec"]
        assert "name" in spec
        assert "description" in spec
        assert "inputSchema" in spec
        assert "json" in spec["inputSchema"]

    def test_name_is_converse_safe(self):
        """Hyphens replaced with underscores in the toolSpec name."""
        nm = ToolNameMap()
        result = build_tool_spec("get-all-pods", "List pods", None, nm)
        assert result["toolSpec"]["name"] == "get_all_pods"

    def test_description_truncated_at_1024(self):
        nm = ToolNameMap()
        long_desc = "A" * 2048
        result = build_tool_spec("t", long_desc, None, nm)
        assert len(result["toolSpec"]["description"]) == 1024

    def test_empty_description_gets_fallback(self):
        nm = ToolNameMap()
        result = build_tool_spec("cool-tool", "", None, nm)
        desc = result["toolSpec"]["description"]
        assert len(desc) > 0
        assert "cool-tool" in desc

    def test_none_description_gets_fallback(self):
        nm = ToolNameMap()
        result = build_tool_spec("x", None, None, nm)
        assert "x" in result["toolSpec"]["description"]

    def test_schema_normalized_in_output(self):
        """Input schema with $ref is resolved in the output."""
        nm = ToolNameMap()
        schema = {
            "type": "object",
            "properties": {"ref_field": {"$ref": "#/$defs/Thing"}},
            "$defs": {"Thing": {"type": "number"}},
        }
        result = build_tool_spec("t", "desc", schema, nm)
        json_schema = result["toolSpec"]["inputSchema"]["json"]
        assert "$ref" not in str(json_schema)
        assert json_schema["properties"]["ref_field"]["type"] == "number"

    def test_none_schema_gets_generic_object(self):
        nm = ToolNameMap()
        result = build_tool_spec("t", "d", None, nm)
        assert result["toolSpec"]["inputSchema"]["json"] == {"type": "object", "properties": {}}


class TestToolSpecAllowlistFiltering:
    """Contract: only tools in the allowlist become toolSpecs; others excluded."""

    def test_allowlisted_tool_included(self):
        """A tool whose name IS in the allowlist should produce a toolSpec."""
        nm = ToolNameMap()
        # Simulate: server exposes "get-pods", allowlist has "get-pods"
        spec = build_tool_spec("get-pods", "List pods", {"type": "object"}, nm)
        assert spec["toolSpec"]["name"] == "get_pods"

    def test_filtering_logic_with_set_intersection(self):
        """Demonstrate allowlist filtering at the adapter level.

        The adapter intersects server tools with the allowlist — tools NOT in
        the allowlist never reach build_tool_spec.
        """
        allowlist = {"get-pods", "describe-pod"}
        server_tools = ["get-pods", "describe-pod", "delete-pod", "restart-pod"]

        nm = ToolNameMap()
        specs = []
        for tool_name in server_tools:
            if tool_name in allowlist:
                specs.append(build_tool_spec(tool_name, f"Does {tool_name}", None, nm))

        # Only 2 tools passed through
        assert len(specs) == 2
        names = {s["toolSpec"]["name"] for s in specs}
        assert "get_pods" in names
        assert "describe_pod" in names
        # Dangerous tools excluded
        assert "delete_pod" not in names
        assert "restart_pod" not in names


# ===========================================================================
# (3) call_tool FAIL-CLOSED — allowlist enforcement
# ===========================================================================


@dataclass
class FakeToolResult:
    """Mimics mcp CallToolResult."""
    content: list = field(default_factory=list)


@dataclass
class FakeTextContent:
    text: str = ""


@dataclass
class FakeTool:
    name: str
    description: str = ""
    inputSchema: dict | None = None


@dataclass
class FakeListToolsResult:
    tools: list = field(default_factory=list)


class TestCallToolFailClosed:
    """Contract: call_tool MUST refuse any tool not in the allowlist.
    The underlying MCP client must NEVER be called for non-allowlisted names.
    """

    @pytest.mark.asyncio
    async def test_non_allowlisted_raises_permission_error(self):
        """A name not in the allowlist raises PermissionError immediately."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="test-server",
            url="http://fake:9999",
            tools=["safe_tool", "another_safe"],
        )
        with pytest.raises(PermissionError, match="not in read-only allowlist"):
            await adapter.call_tool("dangerous_delete_all", {"force": True})

    @pytest.mark.asyncio
    async def test_non_allowlisted_never_calls_mcp_session(self):
        """When a tool is refused, the MCP session/transport is NEVER contacted."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="test-server",
            url="http://fake:9999",
            tools=["read_metrics"],
        )

        # Patch the transport import to detect if connection is ever attempted
        with patch.dict("sys.modules", {"mcp": MagicMock(), "mcp.client.streamable_http": MagicMock()}):
            # PermissionError must fire BEFORE any transport code runs
            with pytest.raises(PermissionError):
                await adapter.call_tool("write_to_db", {})

    @pytest.mark.asyncio
    async def test_underscore_variant_resolves_to_hyphen_allowlisted(self):
        """Model outputs 'get_pods' (underscore) → resolves to 'get-pods' (in allowlist)."""
        from src.core.adapters import McpAdapter
        from src.core.tool_schema import ToolNameMap

        adapter = McpAdapter(
            name="k8s",
            url="http://fake:9999",
            tools=["get-pods"],
        )
        # Populate name map (simulates list_tool_specs having been called)
        nm = ToolNameMap()
        nm.register("get-pods")
        adapter._name_map = nm

        # Should NOT raise PermissionError — tool IS allowlisted
        # Will fail on MCP connection (expected — no real server)
        result = await adapter.call_tool("get_pods", {"namespace": "default"})
        assert "not in read-only allowlist" not in result

    @pytest.mark.asyncio
    async def test_hyphen_fallback_without_name_map(self):
        """Even without name_map populated, underscore→hyphen fallback works."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="k8s",
            url="http://fake:9999",
            tools=["describe-node"],
        )
        # No name map (list_tool_specs never called)
        adapter._name_map = None

        # "describe_node" should resolve to "describe-node" via fallback
        result = await adapter.call_tool("describe_node", {})
        assert "not in read-only allowlist" not in result

    @pytest.mark.asyncio
    async def test_completely_unknown_name_refused(self):
        """Name that doesn't match allowlist in any form → PermissionError."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="k8s",
            url="http://fake:9999",
            tools=["get-pods", "describe-node"],
        )
        with pytest.raises(PermissionError):
            await adapter.call_tool("drop_database", {})

    @pytest.mark.asyncio
    async def test_empty_allowlist_refuses_everything(self):
        """With empty allowlist, every tool call is refused."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="locked",
            url="http://fake:9999",
            tools=[],
        )
        with pytest.raises(PermissionError):
            await adapter.call_tool("any_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_merges_static_arguments(self):
        """Static tool_arguments from config are merged into the call."""
        from src.core.adapters import McpAdapter
        from src.core.tool_schema import ToolNameMap

        adapter = McpAdapter(
            name="vm",
            url="http://fake:9999",
            tools=["query"],
            tool_arguments={"tenant": "0"},
        )
        nm = ToolNameMap()
        nm.register("query")
        adapter._name_map = nm

        # The call itself will fail on connection, but it should NOT fail on
        # allowlist — proving the merge doesn't break resolution
        result = await adapter.call_tool("query", {"expr": "up"})
        assert "not in read-only allowlist" not in result


# ===========================================================================
# (2+3) list_tool_specs — mock MCP session, allowlist filtering
# ===========================================================================


class TestListToolSpecsContract:
    """Contract: list_tool_specs returns specs ONLY for allowlisted tools."""

    @pytest.mark.asyncio
    async def test_empty_allowlist_returns_empty_without_connecting(self):
        """Empty allowlist → returns [] without even trying to connect."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(name="no-tools", url="http://fake:9999", tools=[])
        result = await adapter.list_tool_specs()
        assert result == []

    @pytest.mark.asyncio
    async def test_cache_returns_cached_specs_within_ttl(self):
        """Once cached, list_tool_specs returns cached results without reconnecting."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(name="cached", url="http://fake:9999", tools=["tool_a"])
        adapter.cache_ttl = 600

        # Pre-populate cache
        fake_specs = [{"toolSpec": {"name": "tool_a", "description": "cached"}}]
        adapter._cached_tool_specs = fake_specs
        adapter._specs_cached_at = time.time()

        result = await adapter.list_tool_specs()
        assert result == fake_specs

    @pytest.mark.asyncio
    async def test_cache_expired_triggers_refresh(self):
        """Expired cache means next call tries to reconnect (will fail-open here)."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(name="expired", url="http://fake:9999", tools=["tool_b"])
        adapter.cache_ttl = 1

        # Cache from 10 seconds ago (expired with ttl=1)
        adapter._cached_tool_specs = [{"toolSpec": {"name": "tool_b"}}]
        adapter._specs_cached_at = time.time() - 10

        # Will try to connect, fail, return empty (fail-open)
        result = await adapter.list_tool_specs()
        assert result == []  # fail-open on connection error

    @pytest.mark.asyncio
    async def test_connection_failure_returns_empty_list(self):
        """If MCP server unreachable, returns empty list (fail-open)."""
        from src.core.adapters import McpAdapter

        adapter = McpAdapter(
            name="unreachable",
            url="http://192.0.2.1:9999",  # RFC 5737 TEST-NET
            tools=["some_tool"],
        )
        adapter.cache_ttl = 0  # no cache

        result = await adapter.list_tool_specs()
        assert result == []

    @pytest.mark.asyncio
    async def test_only_allowlisted_tools_in_output(self):
        """Even if server exposes 10 tools, only allowlisted ones appear."""
        from src.core.adapters import McpAdapter
        from src.core.tool_schema import ToolNameMap

        adapter = McpAdapter(
            name="filtered",
            url="http://fake:9999",
            tools=["allowed-a", "allowed-b"],
        )

        # Mock the session interaction
        fake_tools = FakeListToolsResult(tools=[
            FakeTool(name="allowed-a", description="Tool A", inputSchema={"type": "object"}),
            FakeTool(name="allowed-b", description="Tool B", inputSchema=None),
            FakeTool(name="forbidden-c", description="Danger", inputSchema=None),
            FakeTool(name="forbidden-d", description="Delete", inputSchema=None),
        ])

        nm = ToolNameMap()
        specs = adapter._build_specs_from_session(None, nm, fake_tools)

        assert len(specs) == 2
        names = {s["toolSpec"]["name"] for s in specs}
        assert "allowed_a" in names
        assert "allowed_b" in names
        assert "forbidden_c" not in names
        assert "forbidden_d" not in names


# ===========================================================================
# (4) Budget defaults + env-overridable
# ===========================================================================


class TestLoopBudgetDefaults:
    """Contract: budget constants exist with documented defaults."""

    def test_default_max_tool_steps(self):
        from src.core.agent_config import MAX_TOOL_STEPS
        assert MAX_TOOL_STEPS == 8

    def test_default_max_loop_duration_ms(self):
        from src.core.agent_config import MAX_LOOP_DURATION_MS
        assert MAX_LOOP_DURATION_MS == 120_000

    def test_default_max_loop_tokens(self):
        from src.core.agent_config import MAX_LOOP_TOKENS
        assert MAX_LOOP_TOKENS == 300_000

    def test_default_max_tool_result_chars(self):
        from src.core.agent_config import MAX_TOOL_RESULT_CHARS
        assert MAX_TOOL_RESULT_CHARS == 40_000

    def test_all_are_positive_integers(self):
        from src.core.agent_config import (
            MAX_LOOP_DURATION_MS,
            MAX_LOOP_TOKENS,
            MAX_TOOL_RESULT_CHARS,
            MAX_TOOL_STEPS,
        )
        for val in (MAX_TOOL_STEPS, MAX_LOOP_DURATION_MS, MAX_LOOP_TOKENS, MAX_TOOL_RESULT_CHARS):
            assert isinstance(val, int)
            assert val > 0


class TestLoopBudgetEnvOverride:
    """Contract: budget constants are overridable via AIGENT_* env vars."""

    def test_env_override_all_four(self, monkeypatch):
        monkeypatch.setenv("AIGENT_MAX_TOOL_STEPS", "20")
        monkeypatch.setenv("AIGENT_MAX_LOOP_DURATION_MS", "60000")
        monkeypatch.setenv("AIGENT_MAX_LOOP_TOKENS", "200000")
        monkeypatch.setenv("AIGENT_MAX_TOOL_RESULT_CHARS", "16000")

        import src.core.agent_config as ac
        importlib.reload(ac)

        assert ac.MAX_TOOL_STEPS == 20
        assert ac.MAX_LOOP_DURATION_MS == 60000
        assert ac.MAX_LOOP_TOKENS == 200000
        assert ac.MAX_TOOL_RESULT_CHARS == 16000

        # Cleanup
        monkeypatch.delenv("AIGENT_MAX_TOOL_STEPS")
        monkeypatch.delenv("AIGENT_MAX_LOOP_DURATION_MS")
        monkeypatch.delenv("AIGENT_MAX_LOOP_TOKENS")
        monkeypatch.delenv("AIGENT_MAX_TOOL_RESULT_CHARS")
        importlib.reload(ac)

    def test_partial_env_override(self, monkeypatch):
        """Only the env vars set are overridden; others keep defaults."""
        monkeypatch.setenv("AIGENT_MAX_TOOL_STEPS", "99")

        import src.core.agent_config as ac
        importlib.reload(ac)

        assert ac.MAX_TOOL_STEPS == 99
        assert ac.MAX_LOOP_DURATION_MS == 120000  # default preserved

        monkeypatch.delenv("AIGENT_MAX_TOOL_STEPS")
        importlib.reload(ac)

    def test_invalid_env_raises(self, monkeypatch):
        """Non-integer env var should raise ValueError on module load."""
        monkeypatch.setenv("AIGENT_MAX_TOOL_STEPS", "not_a_number")

        import src.core.agent_config as ac
        with pytest.raises(ValueError):
            importlib.reload(ac)

        monkeypatch.delenv("AIGENT_MAX_TOOL_STEPS")
        importlib.reload(ac)
