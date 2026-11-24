"""Independent-review fixes for BACKLOG F-025 (after PR #59).

Contract under test:
1. POSTGRES_SSLMODE=verify-* verifies against a CA bundle (POSTGRES_SSLROOTCERT,
   PGSSLROOTCERT, ~/.postgresql/root.crt); with no CA it fails loudly (error log +
   connect metric) and stays fail-open. `require` and weaker pass through.
2. boto3 / Kubernetes / Athena errors reach the LLM as class (+ AWS error code) only.
3. MCP JSON-RPC errors reach the LLM as code + scrubbed, capped message.
4. URL userinfo never reaches a log line.
5. The startup purge runs in the background, outside the connect lock.
6. Two agents with the identical name fail discovery.
7. /ready http details carry no URL, timeout path included.
"""
from __future__ import annotations

import asyncio
import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import certifi
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.core import history_stores
from src.core.adapters import (
    AthenaAdapter,
    Boto3Adapter,
    KubernetesAdapter,
    McpAdapter,
    MCP_PROTOCOL_ERROR_MAX_CHARS,
    log_transport_error,
    mcp_protocol_error_summary,
    scrub_urls,
    strip_url_query,
    tool_error_summary,
)
from src.core.agentic_loop import _McpSessionPool, _server_breakers
from src.core.config import settings
from src.core.health import DependencyChecker
from src.core.history_stores import PostgresHistoryStore, PostgresTLSConfigError
from src.core.registry import AgentRegistry

ARN_MSG = (
    "An error occurred (AccessDenied) when calling the DescribeInstances operation: "
    "User: arn:aws:iam::123456789012:role/secret-role is not authorized"
)


def _client_error(code: str = "AccessDenied") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": ARN_MSG}}, "DescribeInstances")


def _mcp_error(code: int, message: str) -> BaseException:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    data = ErrorData(code=code, message=message)
    try:
        err = McpError(data)
    except TypeError:  # older mcp: no custom __init__
        err = McpError(message)
    err.error = data
    return err


# ---------------------------------------------------------------------------
# 4. userinfo scrubbing
# ---------------------------------------------------------------------------

def test_strip_url_query_drops_userinfo_keeps_host_port_path():
    url = "https://user:t0ken@mcp.example:8443/sse?key=abc#frag"
    assert strip_url_query(url) == "https://mcp.example:8443/sse"
    assert strip_url_query("http://only-user@host/p") == "http://host/p"
    assert strip_url_query("http://[::1]:9000/x?y=1") == "http://[::1]:9000/x"


def test_scrub_urls_in_free_text():
    text = "failed for url 'https://admin:hunter2@db.example/api?token=zz' then ws://u:p@h/x"
    out = scrub_urls(text)
    assert "hunter2" not in out and "admin" not in out and "token=zz" not in out
    assert "u:p@" not in out
    assert "https://db.example/api" in out and "ws://h/x" in out


def test_log_transport_error_never_logs_credentials():
    url = "https://user:t0ken@mcp.example/sse?k=v"
    with patch("src.core.adapters.logger") as log:
        log_transport_error("mcp:x", url, OSError(f"connect failed for {url}"))
    extra = log.warning.call_args.kwargs["extra"]
    assert extra["url"] == "https://mcp.example/sse"
    assert "t0ken" not in str(extra) and "user:" not in str(extra)


def test_log_transport_error_without_url():
    with patch("src.core.adapters.logger") as log:
        log_transport_error("k8s", None, OSError("x"))
    assert "url" not in log.warning.call_args.kwargs["extra"]


# ---------------------------------------------------------------------------
# 2. boto3 / k8s / athena redaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boto3_client_error_exposes_code_only():
    adapter = Boto3Adapter(["ec2"])
    with patch.object(Boto3Adapter, "_collect_service", side_effect=_client_error()), \
         patch("src.core.adapters.logger") as log:
        out = await adapter._collect("q")
    assert out == "[ec2] error: ClientError (AccessDenied)"
    assert "123456789012" in str(log.warning.call_args.kwargs["extra"])  # operator still sees it


@pytest.mark.asyncio
async def test_boto3_non_client_error_is_class_only():
    adapter = Boto3Adapter(["rds"])
    exc = EndpointConnectionError(endpoint_url="https://rds.internal.example/?x=1")
    with patch.object(Boto3Adapter, "_collect_service", side_effect=exc), patch("src.core.adapters.logger"):
        out = await adapter._collect("q")
    assert out == "[rds] error: EndpointConnectionError"


def test_aws_code_is_token_restricted():
    weird = ClientError({"Error": {"Code": "arn:aws:iam::123456789012:x y", "Message": "m"}}, "Op")
    assert tool_error_summary(weird) == "ClientError"


@pytest.mark.asyncio
async def test_k8s_error_is_class_only():
    with patch("src.core.adapters.k8s_config.load_incluster_config",
               side_effect=OSError("cannot reach https://10.0.0.1:443/api?token=x")), \
         patch("src.core.adapters.logger"):
        out = await KubernetesAdapter()._collect("q")
    assert out == "[k8s] error: OSError"


@pytest.mark.asyncio
async def test_athena_error_is_class_and_code_only():
    client = MagicMock()
    client.start_query_execution.side_effect = _client_error("InvalidRequestException")
    with patch("src.core.adapters.boto3.client", return_value=client), patch("src.core.adapters.logger"):
        out = await AthenaAdapter("db", "tbl")._collect("q")
    assert out == "[athena:tbl] error: ClientError (InvalidRequestException)"
    assert "arn:" not in out


# ---------------------------------------------------------------------------
# 3. MCP protocol errors
# ---------------------------------------------------------------------------

def test_mcp_protocol_error_summary_code_and_scrubbed_message():
    err = _mcp_error(-32602, "Invalid params: 'namespace' is required (see https://u:p@docs.example/x?t=1)")
    summary = mcp_protocol_error_summary(err)
    assert summary is not None and summary.startswith("McpError -32602: Invalid params: 'namespace' is required")
    assert "u:p@" not in summary and "t=1" not in summary


def test_mcp_protocol_error_is_found_inside_groups_and_capped():
    err = _mcp_error(-32601, "Unknown tool " + "x" * 1000)
    group = ExceptionGroup("unhandled errors in a TaskGroup", [err])
    summary = mcp_protocol_error_summary(group)
    assert summary is not None and summary.startswith("McpError -32601: Unknown tool")
    assert len(summary) <= len("McpError -32601: ") + MCP_PROTOCOL_ERROR_MAX_CHARS


def test_transport_errors_stay_class_only():
    assert mcp_protocol_error_summary(ConnectionRefusedError("refused at http://h")) is None
    assert tool_error_summary(ConnectionRefusedError("refused at http://h")) == "ConnectionRefusedError"


@pytest.mark.asyncio
async def test_mcp_adapter_call_tool_returns_protocol_error_detail():
    session = AsyncMock()
    session.initialize = AsyncMock()
    session.call_tool = AsyncMock(side_effect=_mcp_error(-32602, "Invalid params: unknown field 'nmspace'"))
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
    conn_cm.__aexit__ = AsyncMock(return_value=False)

    adapter = McpAdapter(name="k", url="http://mcp:8080", tools=["get_pods"])
    with patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm), \
         patch("mcp.ClientSession", return_value=session_cm), patch("src.core.adapters.logger"):
        out = await adapter.call_tool("get_pods", {"nmspace": "x"})
    assert out == "[mcp:k:get_pods] error: McpError -32602: Invalid params: unknown field 'nmspace'"


@pytest.mark.asyncio
async def test_agentic_pool_protocol_error_is_actionable_and_not_a_breaker_failure():
    url = "http://mcp-protocol-test:8080"
    _server_breakers.pop(url, None)
    adapter = McpAdapter(name="k", url=url, tools=["get_pods"])
    adapter.call_tool = AsyncMock(side_effect=_mcp_error(-32602, "Invalid params"))  # type: ignore[method-assign]
    pool = _McpSessionPool([adapter])
    with patch("src.core.adapters.logger"):
        out = await pool.call_tool("k", "get_pods", {})
    assert out == "[mcp:k:get_pods] error: McpError -32602: Invalid params"
    assert _server_breakers[url].failure_count == 0  # the server answered

    adapter.call_tool = AsyncMock(side_effect=OSError("refused http://mcp-protocol-test:8080"))  # type: ignore[method-assign]
    with patch("src.core.adapters.logger"):
        out = await pool.call_tool("k", "get_pods", {})
    assert out == "[mcp:k:get_pods] error: OSError"
    _server_breakers.pop(url, None)


# ---------------------------------------------------------------------------
# 1. Postgres TLS
# ---------------------------------------------------------------------------

@pytest.fixture
def no_ca_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(settings, "postgres_sslrootcert", None)
    return tmp_path


@pytest.mark.parametrize("mode", [None, "disable", "prefer", "require"])
def test_non_verify_modes_pass_through(mode, monkeypatch, no_ca_env):
    monkeypatch.setattr(settings, "postgres_sslmode", mode)
    assert history_stores._pg_ssl_kwargs() == ({"ssl": mode} if mode else {})


@pytest.mark.parametrize("mode,check_hostname", [("verify-ca", False), ("verify-full", True)])
def test_verify_modes_build_a_verifying_context(mode, check_hostname, monkeypatch, no_ca_env):
    monkeypatch.setattr(settings, "postgres_sslmode", mode)
    monkeypatch.setattr(settings, "postgres_sslrootcert", certifi.where())
    ctx = history_stores._pg_ssl_kwargs()["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ctx.cert_store_stats()["x509_ca"] > 0


def test_ca_resolution_order(monkeypatch, no_ca_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    assert history_stores._pg_ca_file() is None
    home_ca = no_ca_env / ".postgresql" / "root.crt"
    home_ca.parent.mkdir()
    home_ca.write_text("x")
    assert history_stores._pg_ca_file() == str(home_ca)
    monkeypatch.setenv("PGSSLROOTCERT", certifi.where())
    assert history_stores._pg_ca_file() == certifi.where()
    monkeypatch.setattr(settings, "postgres_sslrootcert", str(home_ca))
    assert history_stores._pg_ca_file() == str(home_ca)


def test_explicit_missing_ca_is_an_error(monkeypatch, no_ca_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-ca")
    monkeypatch.setattr(settings, "postgres_sslrootcert", str(no_ca_env / "missing.pem"))
    with pytest.raises(PostgresTLSConfigError, match="not a readable file"):
        history_stores._pg_ssl_kwargs()


@pytest.mark.asyncio
async def test_verify_without_ca_fails_loudly_and_open(monkeypatch, no_ca_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    create_pool = AsyncMock()
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", create_pool), \
         patch("src.core.history_stores._record_error") as rec, \
         patch("src.core.history_stores.logger") as log:
        assert await store.ping() is False
        assert await store.fetch_chat("u", "s", "aws") == []  # fail-open
    create_pool.assert_not_awaited()
    assert ("postgres", "connect") in [c.args for c in rec.call_args_list]
    assert "POSTGRES_SSLROOTCERT" in log.error.call_args.kwargs["extra"]["error"]


def test_sslrootcert_setting_parsing(monkeypatch):
    from src.core.config import Settings

    monkeypatch.setenv("POSTGRES_SSLROOTCERT", "")
    assert Settings().postgres_sslrootcert is None
    monkeypatch.setenv("POSTGRES_SSLROOTCERT", "/etc/ssl/rds/global-bundle.pem")
    assert Settings().postgres_sslrootcert == "/etc/ssl/rds/global-bundle.pem"


# ---------------------------------------------------------------------------
# 5. startup purge in the background, outside the lock
# ---------------------------------------------------------------------------

class _BlockingConn:
    def __init__(self, gate: asyncio.Event, error: Exception | None = None) -> None:
        self.gate, self.error, self.purges = gate, error, 0

    async def execute(self, sql: str, *args: Any) -> str:
        if "DELETE FROM aigent_history" in sql:
            await self.gate.wait()
            if self.error is not None:
                raise self.error
            self.purges += 1
            return "DELETE 0"
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return "aigent_history" if "to_regclass" in sql else 1


class _PoolOf:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def acquire(self) -> "_PoolOf":
        return self

    async def __aenter__(self) -> Any:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_startup_purge_does_not_block_connect_or_hold_the_lock():
    gate = asyncio.Event()
    conn = _BlockingConn(gate)
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", AsyncMock(return_value=_PoolOf(conn))):
        assert await asyncio.wait_for(store.ping(), timeout=1.0) is True
    task = store._purge_task
    assert task is not None and not task.done()  # still purging in the background
    assert store._lock is not None and not store._lock.locked()
    gate.set()
    await task
    assert conn.purges == 1


@pytest.mark.asyncio
async def test_background_purge_failure_is_logged_and_counted():
    gate = asyncio.Event()
    gate.set()
    conn = _BlockingConn(gate, error=OSError("lock timeout"))
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", AsyncMock(return_value=_PoolOf(conn))), \
         patch("src.core.history_stores._record_error") as rec, \
         patch("src.core.history_stores.logger") as log:
        assert await store.ping() is True
        task = store._purge_task
        assert task is not None
        await task
    assert ("postgres", "purge") in [c.args for c in rec.call_args_list]
    assert log.warning.called


class _Escape(BaseException):
    """A BaseException asyncio stores on the task (unlike KeyboardInterrupt)."""


@pytest.mark.asyncio
async def test_unexpected_purge_crash_is_reported_by_the_done_callback():
    store = PostgresHistoryStore()

    async def _crash(pool: Any) -> None:
        raise _Escape  # not caught by _purge_on_ready's `except Exception`

    with patch.object(store, "_purge_on_ready", _crash), \
         patch("src.core.history_stores._record_error") as rec, \
         patch("src.core.history_stores.logger"):
        store._start_startup_purge(MagicMock())
        task = store._purge_task
        assert task is not None
        with pytest.raises(_Escape):
            await task
        await asyncio.sleep(0)  # let the done callback run
    assert ("postgres", "purge") in [c.args for c in rec.call_args_list]
    assert store._purge_task is None


# ---------------------------------------------------------------------------
# 6. identical names
# ---------------------------------------------------------------------------

def test_two_agents_with_the_identical_name_fail_discovery(tmp_path, make_agent_dir):
    yaml = "name: dup\ndescription: d\ndomain: t\ncapabilities: [x]\ndatasources: []\n"
    make_agent_dir("dir1", yaml_override=yaml)
    make_agent_dir("dir2", yaml_override=yaml)
    with pytest.raises(RuntimeError, match="Two agents named 'dup' share the switch AGENT_DUP_ENABLED"):
        AgentRegistry(agents_dir=str(tmp_path)).discover()


# ---------------------------------------------------------------------------
# 7. /ready http details: no URL
# ---------------------------------------------------------------------------

URL = "http://user:pw@svc.internal:9000/health?token=s3cr3t"


@pytest.mark.asyncio
async def test_http_check_details_carry_no_url():
    checker = DependencyChecker(cache_ttl=0)
    ok = await checker.check_http(URL, MagicMock(get=AsyncMock(return_value=MagicMock(status_code=200))))
    with patch("src.core.health.logger") as log:
        bad = await checker.check_http(URL, MagicMock(get=AsyncMock(return_value=MagicMock(status_code=503))))
    assert ok.detail == "http: 200" and bad.detail == "http: status 503"
    logged = str(log.warning.call_args.kwargs["extra"])
    assert "svc.internal" in logged and "pw" not in logged and "s3cr3t" not in logged


@pytest.mark.asyncio
async def test_http_check_timeout_detail_carries_no_url():
    async def _slow(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(1)

    checker = DependencyChecker(cache_ttl=0, timeout=0.01)
    with patch("src.core.health.logger") as log:
        result = await checker.check_http(URL, MagicMock(get=_slow))
    assert result.detail == "http: timeout after 0.01s"
    assert "s3cr3t" not in str(log.warning.call_args.kwargs["extra"])
