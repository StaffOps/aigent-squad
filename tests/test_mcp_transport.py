"""Tests for McpAdapter streamable-http transport support.

Tests the CONTRACT of MCP transport selection, NOT internal implementation:
- transport defaults to 'streamable-http' when unspecified (flipped from 'sse' in 2026-07-18).
- transport='streamable-http' uses streamablehttp_client (3-tuple unpack).
- transport='sse' uses sse_client (2-tuple unpack) — explicit opt-in preserved.
- Empty allowlist => 'no tools allowlisted' skip, no client connect.
- Fail-open: tool error yields inline error string, not a crash.
- Fail-open: transport teardown exception after results does not discard results.
- Allowlist filters tools not exposed by the server.
- create_adapters passes transport field through.
- Invalid transport value rejected by Literal at parse time.

All MCP client/session interactions are mocked — no real network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.adapters import McpAdapter, create_adapters
from src.core.agent_config import DatasourceConfig


# ---------------------------------------------------------------------------
# Helpers: mock builders
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> MagicMock:
    """Create a mock tool object with a .name attribute."""
    t = MagicMock()
    t.name = name
    return t


def _make_tools_result(names: list[str]) -> MagicMock:
    tools_result = MagicMock()
    tools_result.tools = [_make_tool(n) for n in names]
    return tools_result


def _make_call_result(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    result = MagicMock()
    result.content = [block]
    return result


def _make_session(exposed_tools: list[str], call_results=None, call_side_effect=None) -> AsyncMock:
    """Build a mock ClientSession with the given tools and call behavior."""
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=_make_tools_result(exposed_tools))
    if call_side_effect is not None:
        session.call_tool = AsyncMock(side_effect=call_side_effect)
    elif call_results is not None:
        session.call_tool = AsyncMock(side_effect=call_results)
    else:
        session.call_tool = AsyncMock(return_value=_make_call_result("default-result"))
    return session


def _make_session_cm(session: AsyncMock) -> AsyncMock:
    """Wrap a session in an async context manager mock."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# 1. DatasourceConfig transport field defaults
# ---------------------------------------------------------------------------


class TestDatasourceConfigTransportField:
    """Contract: transport defaults to 'streamable-http' (flipped 2026-07-18)."""

    def test_transport_defaults_to_streamable_http(self):
        """New default: unspecified transport resolves to 'streamable-http'."""
        cfg = DatasourceConfig(type="mcp", name="test", url="http://x")
        assert cfg.transport == "streamable-http"

    def test_transport_accepts_streamable_http_explicit(self):
        cfg = DatasourceConfig(
            type="mcp", name="test", url="http://x", transport="streamable-http"
        )
        assert cfg.transport == "streamable-http"

    def test_transport_explicit_sse_opt_in(self):
        """SSE remains available as explicit opt-in."""
        cfg = DatasourceConfig(type="mcp", name="test", url="http://x", transport="sse")
        assert cfg.transport == "sse"

    def test_invalid_transport_rejected_by_literal(self):
        """Invalid transport value rejected at parse time by pydantic Literal."""
        with pytest.raises(ValidationError) as exc_info:
            DatasourceConfig(type="mcp", name="test", url="http://x", transport="websocket")
        assert "transport" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. create_adapters passes transport through
# ---------------------------------------------------------------------------


class TestCreateAdaptersTransport:
    """Contract: create_adapters threads transport= from config into McpAdapter."""

    def test_create_adapters_default_transport_is_streamable_http(self):
        """Config without explicit transport => McpAdapter gets 'streamable-http'."""
        cfg = DatasourceConfig(type="mcp", name="x", url="http://x", tools=["t"])
        adapters = create_adapters([cfg])
        assert len(adapters) == 1
        assert isinstance(adapters[0], McpAdapter)
        assert adapters[0].transport == "streamable-http"

    def test_create_adapters_explicit_sse_transport(self):
        """Explicit sse in config => McpAdapter gets 'sse'."""
        cfg = DatasourceConfig(
            type="mcp", name="x", url="http://x", tools=["t"], transport="sse"
        )
        adapters = create_adapters([cfg])
        assert adapters[0].transport == "sse"

    def test_create_adapters_explicit_streamable_http_transport(self):
        cfg = DatasourceConfig(
            type="mcp", name="x", url="http://x", tools=["t"], transport="streamable-http"
        )
        adapters = create_adapters([cfg])
        assert adapters[0].transport == "streamable-http"


# ---------------------------------------------------------------------------
# 3. Empty allowlist — fail-closed, no connect
# ---------------------------------------------------------------------------


class TestEmptyAllowlist:
    """Contract: empty tools => 'no tools allowlisted' skip, no client connect."""

    @pytest.mark.asyncio
    async def test_sse_empty_allowlist_no_connect(self):
        adapter = McpAdapter(name="x", url="http://x", tools=[], transport="sse")
        with patch("mcp.client.sse.sse_client") as mock_sse:
            result = await adapter.collect("query")
        assert "no tools allowlisted" in result
        mock_sse.assert_not_called()

    @pytest.mark.asyncio
    async def test_streamable_http_empty_allowlist_no_connect(self):
        adapter = McpAdapter(name="x", url="http://x", tools=[], transport="streamable-http")
        with patch("mcp.client.streamable_http.streamablehttp_client") as mock_sh:
            result = await adapter.collect("query")
        assert "no tools allowlisted" in result
        mock_sh.assert_not_called()


# ---------------------------------------------------------------------------
# 4. SSE transport (2-tuple unpack) — explicit opt-in happy path
# ---------------------------------------------------------------------------


class TestSseTransport:
    """Contract: transport='sse' uses sse_client returning (read, write) 2-tuple."""

    @pytest.mark.asyncio
    async def test_sse_happy_path_invokes_tool(self):
        session = _make_session(
            exposed_tools=["get_pods"],
            call_results=[_make_call_result("pod-a Running")],
        )
        session_cm = _make_session_cm(session)

        # SSE client returns a 2-tuple (read, write)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="k8s", url="http://mcp/sse", tools=["get_pods"], transport="sse"
        )

        with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("query")

        session.call_tool.assert_awaited_once_with("get_pods", {})
        assert "pod-a Running" in result

    @pytest.mark.asyncio
    async def test_sse_explicit_opt_in_uses_sse_client_not_streamable(self):
        """Explicit transport='sse' MUST use sse_client, not streamablehttp_client."""
        session = _make_session(exposed_tools=["t"], call_results=[_make_call_result("ok")])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(name="x", url="http://x", tools=["t"], transport="sse")

        with patch("mcp.client.sse.sse_client", return_value=conn_cm) as mock_sse, \
             patch("mcp.client.streamable_http.streamablehttp_client") as mock_sh, \
             patch("mcp.ClientSession", return_value=session_cm):
            await adapter.collect("q")

        mock_sse.assert_called_once()
        mock_sh.assert_not_called()


# ---------------------------------------------------------------------------
# 5. streamable-http transport (3-tuple unpack) — happy path
# ---------------------------------------------------------------------------


class TestStreamableHttpTransport:
    """Contract: transport='streamable-http' uses streamablehttp_client with
    (read, write, _) 3-tuple unpack."""

    @pytest.mark.asyncio
    async def test_streamable_http_happy_path_invokes_tool(self):
        session = _make_session(
            exposed_tools=["search_dashboards"],
            call_results=[_make_call_result("dashboard-1")],
        )
        session_cm = _make_session_cm(session)

        # streamablehttp_client returns a 3-tuple (read, write, get_session_id)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="grafana",
            url="http://grafana-mcp:8080/mcp",
            tools=["search_dashboards"],
            transport="streamable-http",
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("find dashboards")

        session.call_tool.assert_awaited_once_with("search_dashboards", {})
        assert "dashboard-1" in result

    @pytest.mark.asyncio
    async def test_streamable_http_does_not_use_sse_client(self):
        """When transport='streamable-http', sse_client MUST NOT be called."""
        session = _make_session(exposed_tools=["t"], call_results=[_make_call_result("ok")])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(name="x", url="http://x", tools=["t"], transport="streamable-http")

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.client.sse.sse_client") as mock_sse, \
             patch("mcp.ClientSession", return_value=session_cm):
            await adapter.collect("q")

        mock_sse.assert_not_called()

    @pytest.mark.asyncio
    async def test_streamable_http_with_headers(self):
        """Headers are passed to streamablehttp_client."""
        session = _make_session(exposed_tools=["t"], call_results=[_make_call_result("ok")])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="x",
            url="http://mcp/mcp",
            tools=["t"],
            headers={"Authorization": "Bearer tok"},
            transport="streamable-http",
        )

        with patch(
            "mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm
        ) as mock_sh, \
             patch("mcp.ClientSession", return_value=session_cm):
            await adapter.collect("q")

        mock_sh.assert_called_once_with("http://mcp/mcp", headers={"Authorization": "Bearer tok"})

    @pytest.mark.asyncio
    async def test_streamable_http_with_tool_arguments_and_inject_query(self):
        """Static tool_arguments merged; inject_query_as passes user query."""
        session = _make_session(
            exposed_tools=["query_prom"],
            call_results=[_make_call_result("up=1")],
        )
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="vm",
            url="http://vm-mcp/mcp",
            tools=["query_prom"],
            tool_arguments={"datasourceUid": "prom-1"},
            inject_query_as="expr",
            transport="streamable-http",
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            await adapter.collect("up{job='api'}")

        session.call_tool.assert_awaited_once_with(
            "query_prom", {"datasourceUid": "prom-1", "expr": "up{job='api'}"}
        )

    @pytest.mark.asyncio
    async def test_config_driven_default_uses_streamable_http_client(self):
        """End-to-end: DatasourceConfig(no transport) -> create_adapters -> streamablehttp_client."""
        session = _make_session(exposed_tools=["t"], call_results=[_make_call_result("data")])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        cfg = DatasourceConfig(type="mcp", name="x", url="http://x/mcp", tools=["t"])
        adapters = create_adapters([cfg])
        adapter = adapters[0]

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm) as mock_sh, \
             patch("mcp.client.sse.sse_client") as mock_sse, \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        mock_sh.assert_called_once()
        mock_sse.assert_not_called()
        assert "data" in result


# ---------------------------------------------------------------------------
# 6. Allowlist filters tools not exposed by the server
# ---------------------------------------------------------------------------


class TestAllowlistFiltering:
    """Contract: only tools both allowlisted AND exposed by server are called."""

    @pytest.mark.asyncio
    async def test_allowlisted_but_not_exposed_reported_sse(self):
        session = _make_session(exposed_tools=["real_tool"])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="s", url="http://x", tools=["real_tool", "ghost"], transport="sse"
        )

        with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "ghost] not exposed by server" in result

    @pytest.mark.asyncio
    async def test_allowlisted_but_not_exposed_reported_streamable_http(self):
        session = _make_session(exposed_tools=["real_tool"])
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="s", url="http://x", tools=["real_tool", "ghost"], transport="streamable-http"
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "ghost] not exposed by server" in result

    @pytest.mark.asyncio
    async def test_only_allowlisted_tools_invoked(self):
        """Even if server exposes extra tools, only allowlisted ones are called."""
        session = _make_session(
            exposed_tools=["allowed", "not_in_allowlist"],
            call_results=[_make_call_result("ok")],
        )
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="s", url="http://x", tools=["allowed"], transport="streamable-http"
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        # Only 'allowed' should have been called
        session.call_tool.assert_awaited_once_with("allowed", {})
        assert "not_in_allowlist" not in result


# ---------------------------------------------------------------------------
# 7. Fail-open: tool error yields inline error string, not crash
# ---------------------------------------------------------------------------


class TestFailOpenToolError:
    """Contract: a tool error yields an inline error string; does not crash."""

    @pytest.mark.asyncio
    async def test_tool_error_inline_string_sse(self):
        session = _make_session(
            exposed_tools=["bad_tool"],
            call_side_effect=RuntimeError("tool exploded"),
        )
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(name="m", url="http://x", tools=["bad_tool"], transport="sse")

        with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "bad_tool] error:" in result
        assert "tool exploded" in result

    @pytest.mark.asyncio
    async def test_tool_error_inline_string_streamable_http(self):
        session = _make_session(
            exposed_tools=["bad_tool"],
            call_side_effect=RuntimeError("tool exploded"),
        )
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="m", url="http://x", tools=["bad_tool"], transport="streamable-http"
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "bad_tool] error:" in result
        assert "tool exploded" in result

    @pytest.mark.asyncio
    async def test_one_tool_error_does_not_abort_others_streamable(self):
        """Tool isolation: failing tool A doesn't prevent tool B."""
        session = _make_session(
            exposed_tools=["a", "b"],
            call_side_effect=[RuntimeError("a-fail"), _make_call_result("b-ok")],
        )
        session_cm = _make_session_cm(session)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        adapter = McpAdapter(
            name="m", url="http://x", tools=["a", "b"], transport="streamable-http"
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "a] error: a-fail" in result
        assert "b-ok" in result


# ---------------------------------------------------------------------------
# 8. Fail-open: transport teardown exception after results does not discard
# ---------------------------------------------------------------------------


class TestFailOpenTransportTeardown:
    """Contract: if tools ran successfully but the transport raises during
    teardown (__aexit__), already-collected results are preserved."""

    @pytest.mark.asyncio
    async def test_teardown_exception_preserves_results_sse(self):
        session = _make_session(
            exposed_tools=["t"],
            call_results=[_make_call_result("collected-data")],
        )
        session_cm = _make_session_cm(session)

        # Simulate transport raising during __aexit__ (stream teardown)
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(side_effect=Exception("stream closed"))

        adapter = McpAdapter(name="m", url="http://x", tools=["t"], transport="sse")

        with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        # Results collected before teardown failure MUST be preserved
        assert "collected-data" in result

    @pytest.mark.asyncio
    async def test_teardown_exception_preserves_results_streamable_http(self):
        session = _make_session(
            exposed_tools=["t"],
            call_results=[_make_call_result("collected-data")],
        )
        session_cm = _make_session_cm(session)

        # Simulate transport raising during __aexit__
        conn_cm = AsyncMock()
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        conn_cm.__aexit__ = AsyncMock(side_effect=Exception("stream closed"))

        adapter = McpAdapter(
            name="m", url="http://x", tools=["t"], transport="streamable-http"
        )

        with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
             patch("mcp.ClientSession", return_value=session_cm):
            result = await adapter.collect("q")

        assert "collected-data" in result


# ---------------------------------------------------------------------------
# 9. Connection-level failure (before any tool runs) — both transports
# ---------------------------------------------------------------------------


class TestConnectionFailure:
    """Contract: connection-level failure is fail-open error string."""

    @pytest.mark.asyncio
    async def test_sse_connection_refused(self):
        adapter = McpAdapter(name="x", url="http://nope", tools=["t"], transport="sse")
        with patch("mcp.client.sse.sse_client", side_effect=ConnectionError("refused")):
            result = await adapter.collect("q")
        assert "[mcp:x] error:" in result
        assert "refused" in result

    @pytest.mark.asyncio
    async def test_streamable_http_connection_refused(self):
        adapter = McpAdapter(
            name="x", url="http://nope", tools=["t"], transport="streamable-http"
        )
        with patch(
            "mcp.client.streamable_http.streamablehttp_client",
            side_effect=ConnectionError("refused"),
        ):
            result = await adapter.collect("q")
        assert "[mcp:x] error:" in result
        assert "refused" in result


# ---------------------------------------------------------------------------
# 10. McpAdapter constructor stores transport correctly
# ---------------------------------------------------------------------------


class TestMcpAdapterConstructor:
    """Contract: McpAdapter stores transport for later use.

    NOTE: McpAdapter.__init__ has its own default of 'sse' (for direct
    construction). The config-driven path (DatasourceConfig -> create_adapters)
    always passes transport= explicitly, so the config default of
    'streamable-http' takes effect. See BUG REPORT below.
    """

    def test_constructor_direct_default_is_streamable_http(self):
        """McpAdapter() without transport arg defaults to 'streamable-http' (aligned with DatasourceConfig)."""
        adapter = McpAdapter(name="x", url="http://x", tools=["t"])
        assert adapter.transport == "streamable-http"

    def test_explicit_streamable_http(self):
        adapter = McpAdapter(name="x", url="http://x", tools=["t"], transport="streamable-http")
        assert adapter.transport == "streamable-http"

    def test_explicit_sse(self):
        adapter = McpAdapter(name="x", url="http://x", tools=["t"], transport="sse")
        assert adapter.transport == "sse"


# ---------------------------------------------------------------------------
# 11. End-to-end config-to-adapter default: config omits transport ->
#     DatasourceConfig default 'streamable-http' -> create_adapters ->
#     McpAdapter.transport == 'streamable-http'
# ---------------------------------------------------------------------------


class TestEndToEndDefaultTransport:
    """The COMPLETE contract: YAML without transport field results in
    streamable-http being used at runtime."""

    def test_yaml_without_transport_produces_streamable_http_adapter(self):
        """Simulates loading a YAML datasource block without transport key."""
        raw = {"type": "mcp", "name": "kube", "url": "http://k/mcp", "tools": ["get_pods"]}
        cfg = DatasourceConfig(**raw)
        assert cfg.transport == "streamable-http"

        adapters = create_adapters([cfg])
        assert adapters[0].transport == "streamable-http"

    def test_yaml_with_explicit_sse_produces_sse_adapter(self):
        """Simulates loading a YAML datasource block with transport: sse."""
        raw = {"type": "mcp", "name": "legacy", "url": "http://l/sse",
               "tools": ["t"], "transport": "sse"}
        cfg = DatasourceConfig(**raw)
        assert cfg.transport == "sse"

        adapters = create_adapters([cfg])
        assert adapters[0].transport == "sse"
