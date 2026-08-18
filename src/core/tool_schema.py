"""MCP inputSchema → Bedrock Converse toolSpec normalizer (B7).

Transforms MCP tool definitions (name + JSON Schema inputSchema) into the
Bedrock Converse API `toolSpec` format. Handles:
  - Inline resolution of $ref / $defs
  - Simplification of unsupported `anyOf`/`oneOf` unions
  - Capping recursive nesting depth
  - Bidirectional hyphen ↔ underscore tool-name mapping
  - Safe fallback when inputSchema is missing or empty

All functions are PURE and unit-testable — no I/O, no side effects.
"""
from __future__ import annotations

import copy
import re
from typing import Any

# Converse tool name regex: must match ^[a-zA-Z0-9_-]+$
_CONVERSE_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# Maximum schema nesting depth before we collapse to a generic object.
MAX_NESTING_DEPTH = 4


# ---------------------------------------------------------------------------
# Name mapping — bidirectional hyphen ↔ underscore
# ---------------------------------------------------------------------------


class ToolNameMap:
    """Bidirectional mapping between Converse-safe names and MCP server names.

    Some MCP servers use hyphens in tool names (e.g. "get-pods"), but certain
    Converse clients mis-handle hyphens. We normalize hyphens → underscores for
    the Converse side and keep an internal map so call results route back.
    """

    def __init__(self) -> None:
        # converse_name -> server_name
        self._to_server: dict[str, str] = {}
        # server_name -> converse_name
        self._to_converse: dict[str, str] = {}

    def register(self, server_name: str) -> str:
        """Register a server tool name and return its Converse-safe equivalent."""
        converse_name = server_name.replace("-", "_")
        # Handle collisions: if different server names map to the same converse
        # name, append a disambiguator (extremely rare in practice).
        if converse_name in self._to_server and self._to_server[converse_name] != server_name:
            suffix = 1
            while f"{converse_name}_{suffix}" in self._to_server:
                suffix += 1
            converse_name = f"{converse_name}_{suffix}"

        self._to_server[converse_name] = server_name
        self._to_converse[server_name] = converse_name
        return converse_name

    def to_server_name(self, converse_name: str) -> str | None:
        """Given a Converse name (from model output), return the real MCP name."""
        return self._to_server.get(converse_name)

    def to_converse_name(self, server_name: str) -> str | None:
        """Given a server name, return the registered Converse name."""
        return self._to_converse.get(server_name)

    def has_converse(self, converse_name: str) -> bool:
        return converse_name in self._to_server


# ---------------------------------------------------------------------------
# Schema normalization
# ---------------------------------------------------------------------------


def _resolve_refs(schema: Any, defs: dict[str, Any], _ref_depth: int = 0) -> Any:
    """Inline $ref references using the provided $defs mapping.

    S3 carry-over: caps $ref resolution depth at MAX_NESTING_DEPTH to prevent
    infinite loops from circular $ref definitions. Only $ref hops count toward
    the cap — normal property traversal does not.
    """
    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        # S3: circular $ref protection — cap ref resolution depth
        if _ref_depth >= MAX_NESTING_DEPTH:
            return {"type": "object"}

        ref_path = schema["$ref"]
        # Only handle local #/$defs/Name or #/definitions/Name
        for prefix in ("#/$defs/", "#/definitions/"):
            if ref_path.startswith(prefix):
                def_name = ref_path[len(prefix):]
                if def_name in defs:
                    # Deep-copy to avoid mutation; recursively resolve nested refs
                    resolved = copy.deepcopy(defs[def_name])
                    return _resolve_refs(resolved, defs, _ref_depth + 1)
        # Unresolvable ref → collapse to generic object
        return {"type": "object"}

    # Recurse into properties, items, etc. (does NOT increment ref depth)
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("$defs", "definitions"):
            continue  # Strip $defs from output (already inlined)
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _resolve_refs(v, defs, _ref_depth) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _resolve_refs(value, defs, _ref_depth)
        elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
            result[key] = [_resolve_refs(item, defs, _ref_depth) for item in value]
        else:
            result[key] = value
    return result


def _simplify_unions(schema: Any) -> Any:
    """Simplify anyOf/oneOf unions unsupported by Converse.

    Strategy: if anyOf/oneOf has exactly one non-null type, unwrap it.
    If it has multiple real types, collapse to a generic object (Converse
    doesn't support discriminated unions in toolSpec).
    """
    if not isinstance(schema, dict):
        return schema

    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            variants = schema[union_key]
            # Filter out null-type variants
            non_null = [v for v in variants if v.get("type") != "null"]
            if len(non_null) == 1:
                # Unwrap the single real type, preserving description if present
                desc = schema.get("description")
                merged = _simplify_unions(non_null[0])
                if desc and "description" not in merged:
                    merged["description"] = desc
                return merged
            elif len(non_null) == 0:
                return {"type": "object"}
            else:
                # Multiple real types — collapse to object with description
                result: dict[str, Any] = {"type": "object"}
                if "description" in schema:
                    result["description"] = schema["description"]
                return result

    # Recurse into sub-schemas
    result = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _simplify_unions(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _simplify_unions(value)
        else:
            result[key] = value
    return result


def _cap_depth(schema: Any, current_depth: int = 0) -> Any:
    """Cap nesting depth at MAX_NESTING_DEPTH. Beyond that, collapse to generic object."""
    if not isinstance(schema, dict):
        return schema

    if current_depth >= MAX_NESTING_DEPTH:
        result: dict[str, Any] = {"type": "object"}
        if "description" in schema:
            result["description"] = schema["description"]
        return result

    output = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            output[key] = {
                k: _cap_depth(v, current_depth + 1) for k, v in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            output[key] = _cap_depth(value, current_depth + 1)
        else:
            output[key] = value
    return output


def _strip_unsupported_keys(schema: Any) -> Any:
    """Remove JSON Schema keywords not supported by Converse toolSpec.

    Converse accepts: type, properties, required, items, description, enum,
    default, minimum, maximum, minLength, maxLength, pattern, additionalProperties.
    """
    if not isinstance(schema, dict):
        return schema

    # Keys that Converse toolSpec JSON schema supports
    allowed = {
        "type", "properties", "required", "items", "description", "enum",
        "default", "minimum", "maximum", "minLength", "maxLength", "pattern",
        "additionalProperties", "title",
    }
    result = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _strip_unsupported_keys(v) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            result[key] = _strip_unsupported_keys(value)
        else:
            result[key] = value
    return result


def normalize_input_schema(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize an MCP tool's inputSchema into a Converse-compatible JSON schema.

    Returns a schema dict suitable for `toolSpec.inputSchema.json`.
    If input_schema is None/empty, returns a permissive generic-object schema.
    """
    if not input_schema:
        # Fallback: accept any JSON object (no required properties)
        return {"type": "object", "properties": {}}

    schema = copy.deepcopy(input_schema)

    # Extract $defs / definitions for ref resolution
    defs: dict[str, Any] = {}
    defs.update(schema.pop("$defs", {}))
    defs.update(schema.pop("definitions", {}))

    # Pipeline: resolve refs → simplify unions → cap depth → strip unsupported
    schema = _resolve_refs(schema, defs)
    schema = _simplify_unions(schema)
    schema = _cap_depth(schema)
    schema = _strip_unsupported_keys(schema)

    # Ensure top-level has type: object (Converse requirement)
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}

    return schema


# ---------------------------------------------------------------------------
# Tool-spec builder
# ---------------------------------------------------------------------------


def build_tool_spec(
    server_name: str,
    description: str,
    input_schema: dict[str, Any] | None,
    name_map: ToolNameMap,
) -> dict[str, Any]:
    """Build a single Converse API toolSpec from an MCP tool definition.

    Returns a dict matching the Converse `toolSpec` structure:
    {
      "toolSpec": {
        "name": "<converse-safe-name>",
        "description": "<tool description>",
        "inputSchema": {"json": <normalized-schema>}
      }
    }
    """
    converse_name = name_map.register(server_name)
    normalized = normalize_input_schema(input_schema)

    return {
        "toolSpec": {
            "name": converse_name,
            "description": (description or f"Tool: {server_name}")[:1024],
            "inputSchema": {"json": normalized},
        }
    }
