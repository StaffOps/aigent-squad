"""Integration test: McpAdapter.call_tool -> _truncate_with_marker end-to-end.

Proves that the true-count marker survives the REAL data path:
  MCP session returns ~20KB text table (263 rows)
  -> McpAdapter.call_tool() returns FULL text (no pre-slice below safety cap)
  -> agentic loop applies _truncate_with_marker(text, 8000)
  -> final text CONTAINS marker with the true row count (~263)

This test would have caught the pre-slice bug (adapter silently truncating
to 8000 WITHOUT a marker, making the loop's _truncate_with_marker a no-op).

Run:
  docker run --rm -v $(pwd):/app -w /app python:3.11-slim sh -c \
    "pip install -e '.[dev]' -q && pytest tests/test_spec37_adapter_truncation_integration.py -v"
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.adapters import McpAdapter
from src.core.agent_config import MAX_TOOL_RESULT_CHARS
from src.core.agentic_loop import _truncate_with_marker


# ---------------------------------------------------------------------------
# Fixtures: generate a realistic ~20KB / 263-row text table
# ---------------------------------------------------------------------------

_NUM_ROWS = 263
_HEADER = "NAMESPACE          NAME                                    READY   STATUS    RESTARTS   AGE"


def _generate_table(num_rows: int = _NUM_ROWS) -> str:
    """Generate a kubectl-style text table with a header and N data rows."""
    lines = [_HEADER]
    for i in range(num_rows):
        ns = f"ns-{i % 10:02d}"
        name = f"pod-{i:04d}-aaaabbbbccccddddeeeeffffgggghhhh"
        lines.append(f"{ns:<19}{name:<40}{'1/1':<8}{'Running':<10}{'0':<11}{'5d'}")
    return "\n".join(lines)


# Pre-generate for use across tests
_LARGE_TABLE = _generate_table()
assert len(_LARGE_TABLE) > MAX_TOOL_RESULT_CHARS, (
    f"Test fixture must exceed MAX_TOOL_RESULT_CHARS ({MAX_TOOL_RESULT_CHARS}); "
    f"got {len(_LARGE_TABLE)} chars"
)


# ---------------------------------------------------------------------------
# Helpers: mock MCP session (same pattern as test_mcp_transport.py)
# ---------------------------------------------------------------------------


def _make_call_result(text: str) -> MagicMock:
    """Simulate an MCP CallToolResult with a single TextContent block."""
    block = MagicMock()
    block.text = text
    result = MagicMock()
    result.content = [block]
    return result


def _make_session_for_call_tool(call_result) -> AsyncMock:
    """Build a mock ClientSession that returns call_result on call_tool()."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.call_tool = AsyncMock(return_value=call_result)
    return session


def _make_session_cm(session: AsyncMock) -> AsyncMock:
    """Wrap a session in an async context manager mock."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_streamable_conn_cm() -> AsyncMock:
    """Build a mock streamablehttp_client context (returns 3-tuple)."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), None))
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _build_adapter(tools: list[str] | None = None) -> McpAdapter:
    """Build a real McpAdapter with allowlist including our test tool."""
    tools = tools or ["pods_list"]
    return McpAdapter(
        name="kube-test",
        url="http://fake-mcp:8080/mcp",
        tools=tools,
        headers={},
        tool_arguments={},
        inject_query_as="",
        transport="streamable-http",
    )


async def _call_tool_through_adapter(adapter: McpAdapter, text: str) -> str:
    """Execute call_tool with mocked MCP infra returning `text`."""
    session = _make_session_for_call_tool(_make_call_result(text))
    session_cm = _make_session_cm(session)
    conn_cm = _make_streamable_conn_cm()

    with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
         patch("mcp.ClientSession", return_value=session_cm):
        return await adapter.call_tool("pods_list", {"ns": "default"})


# ---------------------------------------------------------------------------
# INTEGRATION TESTS
# ---------------------------------------------------------------------------


class TestAdapterDoesNotPreTruncate:
    """McpAdapter.call_tool must return FULL text (not pre-sliced to 8000).

    The safety cap is 1MB — our ~20KB fixture is well below it, so the
    adapter must return it unmodified. Any pre-truncation to 8000 would
    defeat the downstream _truncate_with_marker's ability to count items.
    """

    @pytest.mark.asyncio
    async def test_adapter_returns_full_text_below_safety_cap(self):
        adapter = _build_adapter()
        result = await _call_tool_through_adapter(adapter, _LARGE_TABLE)

        assert len(result) == len(_LARGE_TABLE), (
            f"Adapter pre-truncated! Got {len(result)} chars, expected {len(_LARGE_TABLE)}. "
            "The bare [:MAX_TOOL_RESULT_CHARS] slice was not removed."
        )
        assert result == _LARGE_TABLE


class TestTruncationMarkerSurvivesRealPath:
    """End-to-end: adapter output -> _truncate_with_marker -> marker has true count.

    Simulates what the agentic loop does AFTER receiving call_tool's result.
    The marker must report ~263 items (the real row count minus header).
    """

    @pytest.mark.asyncio
    async def test_marker_reports_true_item_count(self):
        adapter = _build_adapter()
        adapter_result = await _call_tool_through_adapter(adapter, _LARGE_TABLE)

        # Apply the loop's truncation (exactly as both loops do)
        final_text = _truncate_with_marker(adapter_result, MAX_TOOL_RESULT_CHARS)

        # Marker exists at the start
        assert final_text.startswith("[truncated:"), (
            "Truncation marker missing! _truncate_with_marker did not fire — "
            "likely the adapter pre-sliced to <= MAX_TOOL_RESULT_CHARS."
        )

        # Marker reports the TRUE item count (263 data rows, header excluded)
        match = re.search(r"~(\d+) items total", final_text)
        assert match is not None, f"Marker pattern not found in: {final_text[:200]}"
        reported_count = int(match.group(1))
        assert reported_count == _NUM_ROWS, (
            f"Marker reports {reported_count} items but fixture has {_NUM_ROWS} rows. "
            "count_items() or _truncate_with_marker is broken."
        )

    @pytest.mark.asyncio
    async def test_marker_plus_data_is_coherent_length(self):
        adapter = _build_adapter()
        adapter_result = await _call_tool_through_adapter(adapter, _LARGE_TABLE)
        final_text = _truncate_with_marker(adapter_result, MAX_TOOL_RESULT_CHARS)

        # Total length is marker_line + MAX_TOOL_RESULT_CHARS data
        marker_line = final_text.split("\n", 1)[0] + "\n"
        data_after_marker = final_text[len(marker_line):]
        assert len(data_after_marker) == MAX_TOOL_RESULT_CHARS, (
            f"Data portion after marker is {len(data_after_marker)} chars, "
            f"expected {MAX_TOOL_RESULT_CHARS}."
        )


class TestSafetyCapPreventOom:
    """Safety cap (1MB) still clips absurdly large results to prevent OOM."""

    @pytest.mark.asyncio
    async def test_result_exceeding_safety_cap_is_clipped(self):
        adapter = _build_adapter()
        huge_text = "x" * 1_500_000  # 1.5MB exceeds 1MB cap

        result = await _call_tool_through_adapter(adapter, huge_text)

        assert len(result) == McpAdapter._ADAPTER_SAFETY_CAP, (
            f"Safety cap not applied! Got {len(result)}, "
            f"expected {McpAdapter._ADAPTER_SAFETY_CAP}"
        )


class TestSmallResultPassthrough:
    """Results <= MAX_TOOL_RESULT_CHARS pass through both adapter and loop untouched."""

    @pytest.mark.asyncio
    async def test_small_result_no_marker(self):
        adapter = _build_adapter()
        small_text = '[{"name":"pod-1"},{"name":"pod-2"}]'
        assert len(small_text) <= MAX_TOOL_RESULT_CHARS

        adapter_result = await _call_tool_through_adapter(adapter, small_text)

        # Adapter returns full text
        assert adapter_result == small_text

        # Loop's truncation is a passthrough (no marker prepended)
        final = _truncate_with_marker(adapter_result, MAX_TOOL_RESULT_CHARS)
        assert final == small_text
        assert not final.startswith("[truncated:")
