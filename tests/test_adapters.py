"""Tests for src/core/adapters.py"""
import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.core.adapters import (
    Boto3Adapter,
    HttpAdapter,
    KubernetesAdapter,
    create_adapters,
)
from src.core.agent_config import DatasourceConfig


# --- create_adapters tests ---


def test_create_adapters_boto3():
    cfg = DatasourceConfig(type="boto3", services=["ec2", "s3"])
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], Boto3Adapter)
    assert adapters[0].services == ["ec2", "s3"]


def test_create_adapters_http():
    cfg = DatasourceConfig(type="http", name="test-api", url="http://example.com", headers={"X-Key": "val"})
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], HttpAdapter)
    assert adapters[0].name == "test-api"


def test_create_adapters_kubernetes():
    cfg = DatasourceConfig(type="kubernetes")
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    assert isinstance(adapters[0], KubernetesAdapter)


def test_create_adapters_unknown_type_skipped():
    cfg = DatasourceConfig(type="unknown_xyz")
    adapters = create_adapters([cfg])
    assert adapters == []


# --- HttpAdapter tests ---


@pytest.mark.asyncio
async def test_http_adapter_collect():
    adapter = HttpAdapter(name="myapi", url="http://test.local/data", headers={})

    mock_response = MagicMock()
    mock_response.text = "response body content"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.core.adapters.httpx.AsyncClient", return_value=mock_client):
        result = await adapter.collect("test query")

    assert "[http:myapi]" in result
    assert "response body content" in result


@pytest.mark.asyncio
async def test_http_adapter_env_interpolation():
    with patch.dict(os.environ, {"MY_HOST": "resolved.host"}):
        adapter = HttpAdapter(name="env-test", url="http://${MY_HOST}/api", headers={})
    assert adapter.url == "http://resolved.host/api"


# --- Boto3Adapter tests ---


@pytest.mark.asyncio
async def test_boto3_adapter_collect_ec2():
    adapter = Boto3Adapter(services=["ec2"])

    mock_client = MagicMock()
    mock_client.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"State": {"Name": "running"}}, {"State": {"Name": "stopped"}}]}
        ]
    }

    with patch("src.core.adapters.boto3.client", return_value=mock_client):
        result = await adapter.collect("list instances")

    assert "[ec2]" in result
    assert "2 instances" in result
    assert "1 running" in result
    mock_client.describe_instances.assert_called_once()


@pytest.mark.asyncio
async def test_boto3_adapter_passes_explicit_region():
    """Regression: clients must be built with an explicit region_name, not rely
    on ambient env (AWS_REGION alone isn't read by botocore → NoRegionError)."""
    adapter = Boto3Adapter(services=["ec2"])

    mock_client = MagicMock()
    mock_client.describe_instances.return_value = {"Reservations": []}

    with patch("src.core.adapters.boto3.client", return_value=mock_client) as mock_ctor, \
         patch("src.core.adapters.settings") as mock_settings:
        mock_settings.aws_region = "us-east-1"
        await adapter.collect("list instances")

    mock_ctor.assert_called_once_with("ec2", region_name="us-east-1")


# --- KubernetesAdapter tests ---


@pytest.mark.asyncio
async def test_kubernetes_adapter_graceful_failure():
    with patch("src.core.adapters.k8s_config.load_incluster_config", side_effect=Exception("no cluster")):
        with patch("src.core.adapters.k8s_config.load_kube_config", side_effect=Exception("no kubeconfig")):
            adapter = KubernetesAdapter()
            result = await adapter.collect("list pods")

    assert "[k8s] error:" in result


# --- McpAdapter tests ---


def test_create_adapters_mcp():
    cfg = DatasourceConfig(
        type="mcp",
        name="k8s-mcp",
        url="http://mcp-k8s:8080/mcp",
        tools=["list_pods"],
    )
    adapters = create_adapters([cfg])
    assert len(adapters) == 1
    from src.core.adapters import McpAdapter
    assert isinstance(adapters[0], McpAdapter)
    assert adapters[0].tools == ["list_pods"]


@pytest.mark.asyncio
async def test_mcp_adapter_fail_closed_empty_allowlist():
    """No allowlisted tools => nothing is called, even if a server exists."""
    from src.core.adapters import McpAdapter

    adapter = McpAdapter(name="empty", url="http://mcp:8080/sse", tools=[])
    with patch("mcp.client.sse.sse_client") as mock_conn:
        result = await adapter.collect("any query")

    assert "no tools allowlisted" in result
    mock_conn.assert_not_called()  # fail-closed: never even connects


@pytest.mark.asyncio
async def test_mcp_adapter_env_interpolation_in_url():
    from src.core.adapters import McpAdapter

    with patch.dict(os.environ, {"MCP_HOST": "k8s-mcp.internal"}):
        adapter = McpAdapter(name="x", url="http://${MCP_HOST}/mcp", tools=["t"])
    assert adapter.url == "http://k8s-mcp.internal/mcp"


@pytest.mark.asyncio
async def test_mcp_adapter_connection_failure_is_fail_open():
    """A broken MCP server must degrade gracefully, not raise."""
    from src.core.adapters import McpAdapter

    adapter = McpAdapter(name="broken", url="http://nope:8080/sse", tools=["list_pods"])
    with patch(
        "mcp.client.sse.sse_client",
        side_effect=Exception("connection refused"),
    ):
        result = await adapter.collect("list pods")

    # The contract under test is FAIL-OPEN: a broken MCP server degrades to an
    # error string instead of raising. That holds.
    assert "[mcp:broken] error:" in result

    # And the message now names the ROOT CAUSE. Before F-011 this read
    # "unhandled errors in a TaskGroup (1 sub-exception)" — the MCP client runs
    # the connection inside an anyio TaskGroup, so str(exc) surfaced the wrapper
    # and an operator reading the log learned nothing.
    #
    # Note the URL points at a host that does not resolve, so the genuine cause
    # here is a DNS/connect error (e.g. "ConnectError: [Errno -2] Name or service
    # not known"). We assert the SHAPE — wrapper gone, a real exception type
    # named — rather than a specific message, which would be brittle across
    # environments. (The `sse_client` patch below does not actually intercept the
    # transport; the real client resolves the host and fails. Left as-is because
    # exercising the real failure path is what makes this test meaningful.)
    assert "TaskGroup" not in result, (
        f"the anyio wrapper must not be what we report, got: {result!r}"
    )
    assert "Error" in result or "error:" in result.split("error:", 1)[1], (
        f"a concrete exception type must be named, got: {result!r}"
    )


def test_mcp_error_detail_unwraps_exception_groups():
    """mcp_error_detail digs through anyio's TaskGroup wrapper (F-011)."""
    from src.core.adapters import mcp_error_detail

    inner = ConnectionRefusedError("connection refused")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])

    detail = mcp_error_detail(group)

    assert "connection refused" in detail
    assert "ConnectionRefusedError" in detail
    assert "TaskGroup" not in detail


def test_mcp_error_detail_reports_siblings_instead_of_hiding_them():
    """With several sub-exceptions, the count is surfaced — nothing silently dropped."""
    from src.core.adapters import mcp_error_detail

    group = ExceptionGroup(
        "boom",
        [TimeoutError("timed out"), ConnectionRefusedError("refused")],
    )

    detail = mcp_error_detail(group)

    assert "timed out" in detail
    assert "+1 more" in detail, f"sibling failures must be visible, got {detail!r}"


def test_mcp_error_detail_handles_nesting_and_causes():
    """Nested groups and `raise X from Y` chains both resolve to the innermost cause."""
    from src.core.adapters import mcp_error_detail

    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [OSError("no route to host")])])
    assert "no route to host" in mcp_error_detail(nested)

    try:
        try:
            raise OSError("dns failure")
        except OSError as root:
            raise RuntimeError("mcp session failed") from root
    except RuntimeError as chained:
        assert "dns failure" in mcp_error_detail(chained)


def test_mcp_error_detail_falls_back_to_the_type_when_message_is_empty():
    """A transport error that stringifies to "" still yields a usable clue."""
    from src.core.adapters import mcp_error_detail

    assert mcp_error_detail(ConnectionResetError()) == "ConnectionResetError"


def test_mcp_adapter_render_extracts_text_blocks():
    from src.core.adapters import McpAdapter

    block_a = MagicMock()
    block_a.text = "pod-a Running"
    block_b = MagicMock()
    block_b.text = "pod-b Pending"
    result_obj = MagicMock()
    result_obj.content = [block_a, block_b]

    rendered = McpAdapter._render(result_obj)
    assert "pod-a Running" in rendered
    assert "pod-b Pending" in rendered


def test_mcp_adapter_render_handles_no_content():
    from src.core.adapters import McpAdapter

    result_obj = MagicMock()
    result_obj.content = []
    assert McpAdapter._render(result_obj) == "(no text content)"


@pytest.mark.asyncio
async def test_mcp_adapter_happy_path_calls_allowlisted_tools():
    """End-to-end loop: allowlisted+exposed tool is called; allowlisted but
    not-exposed tool is reported; static tool_arguments are merged in."""
    from src.core.adapters import McpAdapter

    # tool exposed by the server
    exposed = MagicMock()
    exposed.name = "list_pods"
    tools_result = MagicMock()
    tools_result.tools = [exposed]

    text_block = MagicMock()
    text_block.text = "pod-a Running"
    call_result = MagicMock()
    call_result.content = [text_block]

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=tools_result)
    session.call_tool = AsyncMock(return_value=call_result)

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    conn_cm.__aexit__ = AsyncMock(return_value=False)

    adapter = McpAdapter(
        name="k8s",
        url="http://mcp:8080/sse",
        tools=["list_pods", "ghost_tool"],
        tool_arguments={"namespace": "devops"},
    )

    with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
         patch("mcp.ClientSession", return_value=session_cm):
        result = await adapter.collect("pods?")

    # exposed tool was called with static args only (no implicit query)
    session.call_tool.assert_awaited_once_with("list_pods", {"namespace": "devops"})
    assert "pod-a Running" in result
    # non-exposed allowlisted tool is reported, not called
    assert "ghost_tool] not exposed by server" in result


@pytest.mark.asyncio
async def test_mcp_adapter_inject_query_as_opt_in():
    """When inject_query_as is set, the user query is passed under that key."""
    from src.core.adapters import McpAdapter

    exposed = MagicMock()
    exposed.name = "search"
    tools_result = MagicMock()
    tools_result.tools = [exposed]
    block = MagicMock()
    block.text = "hit"
    call_result = MagicMock()
    call_result.content = [block]

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=tools_result)
    session.call_tool = AsyncMock(return_value=call_result)
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    conn_cm.__aexit__ = AsyncMock(return_value=False)

    adapter = McpAdapter(name="s", url="http://mcp/sse", tools=["search"], inject_query_as="q")
    with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
         patch("mcp.ClientSession", return_value=session_cm):
        await adapter.collect("find me")

    session.call_tool.assert_awaited_once_with("search", {"q": "find me"})


@pytest.mark.asyncio
async def test_mcp_adapter_tool_error_does_not_abort_others():
    """A single tool raising must be captured per-tool (fail-open per tool)."""
    from src.core.adapters import McpAdapter

    t1 = MagicMock()
    t1.name = "a"
    t2 = MagicMock()
    t2.name = "b"
    tools_result = MagicMock()
    tools_result.tools = [t1, t2]

    ok_block = MagicMock()
    ok_block.text = "b-ok"
    ok_result = MagicMock()
    ok_result.content = [ok_block]

    session = AsyncMock()
    session.initialize = AsyncMock()
    session.list_tools = AsyncMock(return_value=tools_result)
    session.call_tool = AsyncMock(side_effect=[Exception("boom"), ok_result])

    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    conn_cm.__aexit__ = AsyncMock(return_value=False)

    adapter = McpAdapter(name="m", url="http://mcp/sse", tools=["a", "b"])
    with patch("mcp.client.sse.sse_client", return_value=conn_cm), \
         patch("mcp.ClientSession", return_value=session_cm):
        result = await adapter.collect("q")

    # Format changed deliberately by F-011: MCP error strings now name the
    # exception TYPE before the message, so a log line identifies the failure
    # class ("ConnectError", "TimeoutError") and not just its text.
    assert "a] error: Exception: boom" in result
    assert "b-ok" in result  # second tool still ran
