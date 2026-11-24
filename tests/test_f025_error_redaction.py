"""BACKLOG F-025 (b)+(f): no addresses or raw exception text where they don't belong.

(b) Datasource transport errors land in the LLM context: only the exception class
    and HTTP status may appear there; the URL is logged without its query string.
(f) /ready is unauthenticated: dependency details carry no raw exception text;
    the full error is logged server-side.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from src.core.adapters import (
    HttpAdapter,
    McpAdapter,
    log_transport_error,
    strip_url_query,
    transport_error_summary,
)
from src.core.health import DependencyChecker

SECRET_URL = "http://datasource.internal:9000/api/v1/query?token=s3cr3t&scope=team"


def _logged(mock_logger: MagicMock) -> list[dict]:
    return [c.kwargs.get("extra", {}) for c in mock_logger.warning.call_args_list]


# ---------------------------------------------------------------------------
# (b) summary helper
# ---------------------------------------------------------------------------

def test_summary_is_class_name_only():
    exc = httpx.ConnectError(f"All connection attempts failed for url '{SECRET_URL}'")
    assert transport_error_summary(exc) == "ConnectError"


def test_summary_adds_http_status():
    request = httpx.Request("GET", SECRET_URL)
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError(f"Server error for url '{SECRET_URL}'", request=request, response=response)
    assert transport_error_summary(exc) == "HTTPStatusError (HTTP 503)"


def test_summary_unwraps_groups_and_causes():
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("x"), OSError("y")])
    assert transport_error_summary(group) == "ConnectionRefusedError (+1 more)"
    try:
        try:
            raise TimeoutError(SECRET_URL)
        except TimeoutError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as chained:
        assert transport_error_summary(chained) == "TimeoutError"


def test_summary_unwrap_is_bounded():
    exc: BaseException = ValueError("leaf")
    for _ in range(20):
        exc = ExceptionGroup("g", [exc])  # type: ignore[list-item]  # nesting on purpose
    assert "leaf" not in transport_error_summary(exc)


def test_strip_url_query_and_log_scrub():
    assert strip_url_query(SECRET_URL) == "http://datasource.internal:9000/api/v1/query"
    with patch("src.core.adapters.logger") as log:
        log_transport_error("http:x", SECRET_URL, httpx.ConnectError(f"failed for url '{SECRET_URL}'"))
    (extra,) = _logged(log)
    assert extra["url"] == "http://datasource.internal:9000/api/v1/query"
    assert extra["error_type"] == "ConnectError"
    assert "ConnectError" in extra["error"] and "s3cr3t" not in str(extra)


# ---------------------------------------------------------------------------
# (b) adapters return the summary, never the URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_http_adapter_status_error_hides_url():
    respx.get(SECRET_URL).mock(return_value=httpx.Response(502))
    adapter = HttpAdapter(name="api", url=SECRET_URL, headers={})
    with patch("src.core.adapters.logger") as log:
        result = await adapter._collect("q")
    assert result == "[http:api] error: HTTPStatusError (HTTP 502)"
    (extra,) = _logged(log)
    assert extra["url"] == "http://datasource.internal:9000/api/v1/query"
    assert "s3cr3t" not in str(extra)


@pytest.mark.asyncio
async def test_http_adapter_connect_error_is_fail_open_and_hides_url():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError(f"refused for url '{SECRET_URL}'"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    adapter = HttpAdapter(name="api", url=SECRET_URL, headers={})
    with patch("src.core.adapters.httpx.AsyncClient", return_value=client), \
         patch("src.core.adapters.logger"):
        result = await adapter._collect("q")
    assert result == "[http:api] error: ConnectError"
    assert "datasource.internal" not in result


@pytest.mark.asyncio
async def test_mcp_collect_connection_error_hides_url():
    adapter = McpAdapter(name="m", url=SECRET_URL, tools=["t"], transport="streamable-http")
    request = httpx.Request("POST", SECRET_URL)
    err = httpx.HTTPStatusError(f"Client error for url '{SECRET_URL}'", request=request,
                                response=httpx.Response(401, request=request))
    with patch("mcp.client.streamable_http.streamablehttp_client",
               side_effect=ExceptionGroup("unhandled errors in a TaskGroup", [err])), \
         patch("src.core.adapters.logger") as log:
        result = await adapter._collect("q")
    assert result == "[mcp:m] error: HTTPStatusError (HTTP 401)"
    assert "s3cr3t" not in str(_logged(log))


@pytest.mark.asyncio
async def test_mcp_call_tool_error_hides_url():
    adapter = McpAdapter(name="m", url=SECRET_URL, tools=["get_pods"], transport="sse")
    with patch("mcp.client.sse.sse_client",
               side_effect=httpx.ConnectError(f"failed for url '{SECRET_URL}'")), \
         patch("src.core.adapters.logger") as log:
        result = await adapter.call_tool("get_pods", {})
    assert result == "[mcp:m:get_pods] error: ConnectError"
    (extra,) = _logged(log)
    assert extra["datasource"] == "mcp:m:get_pods" and "s3cr3t" not in str(extra)


@pytest.mark.asyncio
async def test_mcp_list_tool_specs_failure_is_logged_and_fail_open():
    adapter = McpAdapter(name="m", url=SECRET_URL, tools=["get_pods"], transport="sse")
    with patch("mcp.client.sse.sse_client", side_effect=OSError("down")), \
         patch("src.core.adapters.logger") as log:
        assert await adapter.list_tool_specs() == []
    assert _logged(log)[0]["url"] == "http://datasource.internal:9000/api/v1/query"


# ---------------------------------------------------------------------------
# (f) readiness details are generic
# ---------------------------------------------------------------------------

LEAKY = ConnectionError("Error 111 connecting to cache.internal:6379. Connection refused.")


@pytest.mark.asyncio
async def test_check_redis_detail_is_generic_and_error_is_logged():
    redis_client = MagicMock()
    redis_client.ping.side_effect = LEAKY
    with patch("src.core.health.logger") as log:
        result = await DependencyChecker(cache_ttl=0).check_redis(redis_client)
    assert result.ok is False and result.detail == "redis unreachable (ConnectionError)"
    (extra,) = _logged(log)
    assert extra["dependency"] == "redis" and "cache.internal" in extra["error"]


@pytest.mark.asyncio
async def test_check_dynamodb_and_bedrock_creds_are_generic():
    table = MagicMock()
    table.load.side_effect = LEAKY
    sts = MagicMock()
    sts.get_caller_identity.side_effect = LEAKY
    checker = DependencyChecker(cache_ttl=0)
    with patch("src.core.health.logger"):
        assert (await checker.check_dynamodb(table)).detail == "dynamodb unreachable (ConnectionError)"
        assert (await checker.check_bedrock_creds(sts)).detail == "bedrock-creds unreachable (ConnectionError)"


@pytest.mark.asyncio
async def test_check_history_and_http_are_generic():
    store = MagicMock(backend="postgres", ping=AsyncMock(side_effect=LEAKY))
    client = MagicMock(get=AsyncMock(side_effect=LEAKY))
    checker = DependencyChecker(cache_ttl=0)
    with patch("src.core.health.logger"):
        history = await checker.check_history(store)
        http = await checker.check_http("http://svc/health", client)
    assert history.detail == "history: postgres unreachable (ConnectionError)"
    assert "cache.internal" not in http.detail and http.detail.endswith("unreachable (ConnectionError)")


@pytest.mark.asyncio
async def test_unexpected_check_failure_is_generic():
    async def _boom():
        raise LEAKY

    with patch("src.core.health.logger"):
        result = await DependencyChecker(cache_ttl=0)._run("redis", _boom)
    assert result.detail == "redis unreachable (ConnectionError)"


@pytest.mark.asyncio
async def test_timeout_detail_unchanged():
    async def _slow():
        await asyncio.sleep(1)

    result = await DependencyChecker(cache_ttl=0, timeout=0.01)._run("redis", _slow)
    assert result.detail == "redis: timeout after 0.01s"


def test_ready_endpoint_never_serves_raw_exception_text():
    """End to end through /ready with the REAL checker and a failing Redis."""
    from fastapi.testclient import TestClient

    import src.supervisor.server as srv

    failing_redis = MagicMock()
    failing_redis.ping.side_effect = LEAKY
    sup = MagicMock(agents={"aws": MagicMock()})
    sup.registry = MagicMock(degraded_datasources=[])
    with patch.object(srv, "_checker", DependencyChecker(cache_ttl=0)), \
         patch.object(srv, "_health_redis", failing_redis), \
         patch.object(srv, "supervisor", sup), \
         patch.object(srv.settings, "history_backend", "redis"), \
         patch("src.core.health.logger"):
        response = TestClient(srv.app, raise_server_exceptions=False).get("/ready")
    assert response.status_code == 503
    body = response.text
    assert "cache.internal" not in body and "refused" not in body
    assert response.json()["checks"]["redis"]["detail"] == "redis unreachable (ConnectionError)"
