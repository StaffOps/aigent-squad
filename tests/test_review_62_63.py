"""Independent-review fixes for PR #62 (F-027/F-028) and PR #63 (F-025 review).

Contract under test:
1. MCP circuit breaker: a transport failure of the REAL McpAdapter (refused,
   TLS, closed connection, read timeout) counts against the breaker; a genuine
   JSON-RPC application error does not, and reaches the model actionable and
   secret-free.
2. DynamoDB ``fetch_all_chats`` returns the newest unexpired items ACROSS agents
   (the sort key orders by agent name); ``fetch_chat`` does not match an agent
   whose name merely starts with the requested one.
3. Postgres TLS: asyncpg builds the context (client cert, CRL, protocol floor
   kept); the CA bundle is resolved, validated and handed over via the DSN;
   ``system`` resolves to the system bundle; bad CA = TLS misconfiguration.
4. ``scrub_urls`` covers any scheme, '@'/'/' in passwords, scheme-less DSNs,
   and leaves an e-mail in a query alone (it is stripped, not mangled).
5. Kubernetes ``ApiException`` keeps its HTTP status in the LLM summary.
6. The Postgres store's background purge is cancelled and awaited on shutdown.
9. ``/ready`` HTTP details are labelled by a caller-chosen name, never a URL.
"""
from __future__ import annotations

import asyncio
import shutil
import ssl
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import certifi
import pytest
from kubernetes.client.exceptions import ApiException

from src.core import history_stores
from src.core.adapters import (
    McpAdapter,
    McpToolError,
    classify_mcp_failure,
    mcp_protocol_error_summary,
    scrub_urls,
    strip_url_query,
    transport_error_summary,
)
from src.core.agentic_loop import _McpSessionPool, _server_breakers
from src.core.config import settings
from src.core.health import DependencyChecker
from src.core.history_stores import PostgresHistoryStore, PostgresTLSConfigError
from src.core.output_filter import redact_secrets
from src.core.state_store import DynamoDBHistoryStore


def _mcp_error(code: int, message: str) -> BaseException:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    return McpError(ErrorData(code=code, message=message))


# ---------------------------------------------------------------------------
# 1. MCP breaker with the real adapter
# ---------------------------------------------------------------------------

def _patched_transport(*, connect_error: BaseException | None = None,
                       call_error: BaseException | None = None) -> Any:
    """Patch the streamable-http transport + ClientSession the real adapter uses."""
    session = MagicMock()
    session.initialize = AsyncMock()
    session.call_tool = AsyncMock(side_effect=call_error)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    conn_cm = MagicMock()
    if connect_error is not None:
        conn_cm.__aenter__ = AsyncMock(side_effect=connect_error)
    else:
        conn_cm.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
    conn_cm.__aexit__ = AsyncMock(return_value=False)
    return (
        patch("mcp.client.streamable_http.streamablehttp_client", return_value=conn_cm),
        patch("mcp.ClientSession", return_value=session_cm),
        patch("src.core.adapters.logger"),
    )


async def _pool_call(url: str, **transport: Any) -> tuple[str, int]:
    adapter = McpAdapter(name="k", url=url, tools=["get_pods"])
    pool = _McpSessionPool([adapter])
    p1, p2, p3 = _patched_transport(**transport)
    with p1, p2, p3:
        out = await pool.call_tool("k", "get_pods", {})
    return out, _server_breakers[url].failure_count


@pytest.fixture
def breaker_url(request: pytest.FixtureRequest) -> Any:
    url = f"http://mcp-{request.node.name[:40]}:8080"
    _server_breakers.pop(url, None)
    yield url
    _server_breakers.pop(url, None)


@pytest.mark.asyncio
async def test_real_adapter_connection_refused_counts_and_opens_the_breaker(breaker_url):
    for expected in (1, 2):
        out, failures = await _pool_call(breaker_url, connect_error=ConnectionRefusedError("refused"))
        assert out == "[mcp:k:get_pods] error: ConnectionRefusedError"
        assert failures == expected
    await _pool_call(breaker_url, connect_error=ssl.SSLError("certificate verify failed"))
    assert not _server_breakers[breaker_url].can_execute()  # threshold 3 -> OPEN
    out, _ = await _pool_call(breaker_url)
    assert out.endswith("circuit breaker OPEN")


@pytest.mark.asyncio
async def test_transport_failure_inside_a_taskgroup_counts(breaker_url):
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("x")])
    out, failures = await _pool_call(breaker_url, connect_error=group)
    assert out == "[mcp:k:get_pods] error: ConnectionError"
    assert failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code,message", [(-32000, "Connection closed"), (408, "Timed out while waiting")])
async def test_client_synthesised_mcp_errors_are_transport_failures(breaker_url, code, message):
    out, failures = await _pool_call(breaker_url, call_error=_mcp_error(code, message))
    assert out == "[mcp:k:get_pods] error: McpError"
    assert failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-32602, -32601, -32603, 1234])
async def test_jsonrpc_application_errors_are_not_breaker_failures(breaker_url, code):
    out, failures = await _pool_call(breaker_url, call_error=_mcp_error(code, "Invalid params: 'ns' is required"))
    assert out == f"[mcp:k:get_pods] error: McpError {code}: Invalid params: 'ns' is required"
    assert failures == 0


@pytest.mark.asyncio
async def test_application_error_resets_a_partially_failed_breaker(breaker_url):
    await _pool_call(breaker_url, connect_error=ConnectionRefusedError())
    _, failures = await _pool_call(breaker_url, call_error=_mcp_error(-32602, "bad"))
    assert failures == 0  # the server answered: healthy


@pytest.mark.asyncio
async def test_adapter_returns_a_typed_error_string():
    adapter = McpAdapter(name="k", url="http://mcp-typed:8080", tools=["get_pods"])
    p1, p2, p3 = _patched_transport(call_error=_mcp_error(-32602, "nope"))
    with p1, p2, p3:
        out = await adapter.call_tool("get_pods", {})
    assert isinstance(out, McpToolError) and out.kind == "protocol_error"
    assert out == "[mcp:k:get_pods] error: McpError -32602: nope"
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_successful_call_is_plain_text_and_a_breaker_success(breaker_url):
    adapter = McpAdapter(name="k", url=breaker_url, tools=["get_pods"])
    adapter._render = MagicMock(return_value="pod-1 Running")  # type: ignore[method-assign]
    pool = _McpSessionPool([adapter])
    _server_breakers.pop(breaker_url, None)
    p1, p2, p3 = _patched_transport()
    with p1, p2, p3:
        out = await pool.call_tool("k", "get_pods", {})
    assert out == "pod-1 Running" and not isinstance(out, McpToolError)
    assert _server_breakers[breaker_url].failure_count == 0


def test_classify_mcp_failure():
    assert classify_mcp_failure(_mcp_error(-32602, "x")) == ("protocol_error", "McpError -32602: x")
    assert classify_mcp_failure(_mcp_error(-32000, "Connection closed")) == ("transport_error", "McpError")
    assert classify_mcp_failure(OSError("x")) == ("transport_error", "OSError")
    assert mcp_protocol_error_summary(_mcp_error(32600, "Session terminated")) is None


def test_protocol_error_message_is_secret_redacted():
    err = _mcp_error(-32602, "bad auth_token=abcdefghijklmnopqrstuvwx for AKIAABCDEFGHIJKLMNOP")
    summary = mcp_protocol_error_summary(err)
    assert summary is not None
    assert "abcdefghijklmnopqrstuvwx" not in summary and "AKIAABCDEFGHIJKLMNOP" not in summary
    assert "[REDACTED:aws_access_key]" in summary and "[REDACTED:generic_secret]" in summary


def test_redact_secrets_keeps_non_luhn_numbers():
    assert redact_secrets("epoch 1732425600000 ok") == "epoch 1732425600000 ok"
    assert redact_secrets("card 4111 1111 1111 1111") == "card [REDACTED:credit_card]"


# ---------------------------------------------------------------------------
# 2. DynamoDB history ordering
# ---------------------------------------------------------------------------

class FakeTable:
    """DynamoDB query semantics that matter here: key order = sk order, Limit
    applied BEFORE the filter, LastEvaluatedKey pagination, small page size."""

    def __init__(self, items: list[dict[str, Any]], page_size: int = 2) -> None:
        self.items = items
        self.page_size = page_size
        self.calls: list[dict[str, Any]] = []

    def put_item(self, Item: dict[str, Any]) -> None:
        self.items.append(Item)

    def query(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        values = kw["ExpressionAttributeValues"]
        assert kw["FilterExpression"] == "#ttl > :now"
        assert kw["ExpressionAttributeNames"] == {"#ttl": "ttl"}
        rows = [i for i in self.items if i["pk"] == values[":pk"]]
        if ":agent" in values:
            assert kw["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :agent)"
            rows = [i for i in rows if i["sk"].startswith(values[":agent"])]
        rows.sort(key=lambda i: i["sk"], reverse=not kw.get("ScanIndexForward", True))
        start = kw.get("ExclusiveStartKey")
        if start is not None:
            idx = next(n for n, i in enumerate(rows) if i["sk"] == start["sk"])
            rows = rows[idx + 1:]
        evaluated = rows[: min(kw.get("Limit", self.page_size), self.page_size)]
        out: dict[str, Any] = {"Items": [i for i in evaluated if i["ttl"] > values[":now"]]}
        if len(rows) > len(evaluated):
            out["LastEvaluatedKey"] = {"pk": evaluated[-1]["pk"], "sk": evaluated[-1]["sk"]}
        return out


def _row(agent: str, minute: int, *, expired: bool = False, pk: str = "u1#s1") -> dict[str, Any]:
    ts = f"2026-11-24T10:{minute:02d}:00+00:00"
    return {
        "pk": pk, "sk": f"{agent}#{ts}", "role": "user", "content": f"{agent}-{minute}",
        "timestamp": ts, "agent_id": agent,
        "ttl": int(time.time()) + (-60 if expired else 3600),
    }


@pytest.fixture
def make_store():
    def _make(table: FakeTable) -> DynamoDBHistoryStore:
        with patch("src.core.state_store.boto3"):
            store = DynamoDBHistoryStore()
        store.table = table
        return store
    return _make


@pytest.mark.asyncio
async def test_fetch_all_chats_is_ordered_by_time_across_agents(make_store):
    # sk order puts "aws" first; the newest messages belong to "security".
    items = [_row("aws", 1), _row("aws", 2), _row("kubernetes", 3), _row("security", 4), _row("security", 5)]
    store = make_store(FakeTable(items, page_size=2))
    msgs = await store.fetch_all_chats("u1", "s1", max_messages=3)
    assert [m.content for m in msgs] == ["kubernetes-3", "security-4", "security-5"]


@pytest.mark.asyncio
async def test_expired_first_agent_does_not_hide_a_fresh_one(make_store):
    """One expired + one fresh item across agents: the old Limit read returned []."""
    store = make_store(FakeTable([_row("zeta", 1), _row("aws", 0, expired=True)], page_size=1))
    msgs = await store.fetch_all_chats("u1", "s1", max_messages=1)
    assert [m.content for m in msgs] == ["zeta-1"]


@pytest.mark.asyncio
async def test_fetch_all_chats_reads_only_its_session(make_store):
    items = [_row("aws", 1), _row("aws", 9, pk="u1#other")]
    store = make_store(FakeTable(items))
    assert [m.content for m in await store.fetch_all_chats("u1", "s1")] == ["aws-1"]


@pytest.mark.asyncio
async def test_fetch_all_chats_falls_back_to_sk_time_and_is_bounded(make_store, monkeypatch):
    rows = [_row("aws", n) for n in range(6)]
    for r in rows:
        r["timestamp"] = "not-a-date"  # legacy/odd value: the sk write time orders
    table = FakeTable(rows, page_size=1)
    monkeypatch.setattr("src.core.state_store._MAX_SESSION_PAGES", 4)
    with patch("src.core.state_store.logger") as log:
        msgs = await make_store(table).fetch_all_chats("u1", "s1", max_messages=2)
    assert len(table.calls) == 4  # capped
    assert [m.content for m in msgs] == ["aws-2", "aws-3"]
    assert log.warning.called  # truncation is visible


@pytest.mark.asyncio
async def test_fetch_chat_prefix_does_not_match_a_longer_agent_name(make_store):
    items = [_row("aws", 1), _row("aws-cost", 2), _row("aws", 3)]
    store = make_store(FakeTable(items))
    msgs = await store.fetch_chat("u1", "s1", "aws", max_messages=5)
    assert [m.content for m in msgs] == ["aws-1", "aws-3"]


@pytest.mark.asyncio
async def test_fetch_chat_skips_expired_and_paginates(make_store):
    items = [_row("aws", 1), _row("aws", 2, expired=True), _row("aws", 3, expired=True), _row("aws", 4)]
    store = make_store(FakeTable(items, page_size=2))
    msgs = await store.fetch_chat("u1", "s1", "aws", max_messages=2)
    assert [m.content for m in msgs] == ["aws-1", "aws-4"]


@pytest.mark.asyncio
async def test_dynamodb_calls_run_off_the_event_loop(make_store):
    store = make_store(FakeTable([_row("aws", 1)]))
    with patch("src.core.state_store.asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
        await store.fetch_chat("u1", "s1", "aws")
        await store.fetch_all_chats("u1", "s1")
        await store.save_chat_message("u1", "s1", "aws", MagicMock(role="user", content="c", timestamp="t"))
    assert to_thread.call_count == 3
    assert len(store.table.items) == 2  # the write landed


@pytest.mark.asyncio
async def test_dynamodb_aclose_is_a_noop(make_store):
    await make_store(FakeTable([])).aclose()


# ---------------------------------------------------------------------------
# 3. Postgres TLS
# ---------------------------------------------------------------------------

@pytest.fixture
def tls_env(monkeypatch, tmp_path):
    for var in ("PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY", "PGSSLCRL", "PGSSLMINPROTOCOLVERSION"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(settings, "postgres_sslrootcert", None)
    return tmp_path


def _asyncpg_params(kwargs: dict[str, Any]) -> Any:
    """What asyncpg 0.29 itself builds from our kwargs (no network)."""
    from asyncpg.connect_utils import _parse_connect_dsn_and_args

    _, params = _parse_connect_dsn_and_args(
        dsn=kwargs.get("dsn"), host="db.example", port=5432, user="u", password="p",
        passfile=None, database="d", ssl=kwargs.get("ssl"), direct_tls=False,
        server_settings=None, target_session_attrs=None,
    )
    return params


@pytest.mark.parametrize("mode,check_hostname", [("verify-ca", False), ("verify-full", True)])
def test_verify_modes_hand_the_ca_to_asyncpg(mode, check_hostname, monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", mode)
    monkeypatch.setattr(settings, "postgres_sslrootcert", certifi.where())
    kwargs = history_stores._pg_ssl_kwargs()
    assert kwargs["ssl"] == mode and "sslrootcert=" in kwargs["dsn"]
    ctx = _asyncpg_params(kwargs).ssl
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is check_hostname
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ctx.cert_store_stats()["x509_ca"] > 0


def test_client_cert_crl_and_protocol_floor_are_applied_by_asyncpg(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    monkeypatch.setattr(settings, "postgres_sslrootcert", certifi.where())
    monkeypatch.setenv("PGSSLMINPROTOCOLVERSION", "TLSv1.3")
    ctx = _asyncpg_params(history_stores._pg_ssl_kwargs()).ssl
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
    # The client certificate is loaded by asyncpg (a missing one fails there).
    monkeypatch.setenv("PGSSLCERT", str(tls_env / "missing-client.crt"))
    with pytest.raises(FileNotFoundError):
        _asyncpg_params(history_stores._pg_ssl_kwargs())
    monkeypatch.delenv("PGSSLCERT")
    monkeypatch.setenv("PGSSLCRL", certifi.where())  # any PEM proves it is loaded
    ctx = _asyncpg_params(history_stores._pg_ssl_kwargs()).ssl
    assert ctx.verify_flags & ssl.VERIFY_CRL_CHECK_CHAIN


def test_require_verifies_when_a_ca_is_configured(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "require")
    assert history_stores._pg_ssl_kwargs() == {"ssl": "require"}
    monkeypatch.setattr(settings, "postgres_sslrootcert", certifi.where())
    kwargs = history_stores._pg_ssl_kwargs()
    assert _asyncpg_params(kwargs).ssl.verify_mode == ssl.CERT_REQUIRED


def test_home_root_crt_is_used_and_validated(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-ca")
    home_ca = tls_env / ".postgresql" / "root.crt"
    home_ca.parent.mkdir()
    shutil.copy(certifi.where(), home_ca)
    assert history_stores._pg_ca_file() == str(home_ca)
    home_ca.write_text("not a certificate")
    with pytest.raises(PostgresTLSConfigError, match="cannot be loaded"):
        history_stores._pg_ssl_kwargs()


def test_invalid_explicit_ca_is_a_tls_config_error(monkeypatch, tls_env):
    bad = tls_env / "bad.pem"
    bad.write_text("garbage")
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    monkeypatch.setattr(settings, "postgres_sslrootcert", str(bad))
    with pytest.raises(PostgresTLSConfigError, match="cannot be loaded"):
        history_stores._pg_ssl_kwargs()


def test_sslrootcert_system_resolves_to_the_system_bundle(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "system")
    paths = MagicMock(cafile=None, openssl_cafile=certifi.where())
    with patch("src.core.history_stores.ssl.get_default_verify_paths", return_value=paths):
        assert history_stores._pg_ca_file() == certifi.where()
    paths = MagicMock(cafile=None, openssl_cafile=str(tls_env / "none.pem"))
    with patch("src.core.history_stores.ssl.get_default_verify_paths", return_value=paths), \
         pytest.raises(PostgresTLSConfigError, match="system"):
        history_stores._pg_ssl_kwargs()


@pytest.mark.parametrize("exc", [
    PostgresTLSConfigError("x"), ssl.SSLError("pem"), FileNotFoundError("client.crt"), PermissionError("key"),
])
def test_tls_misconfig_classification(exc):
    assert history_stores._is_tls_misconfig(exc)


def test_client_configuration_error_and_outages_are_classified():
    from asyncpg.exceptions import ClientConfigurationError

    assert history_stores._is_tls_misconfig(ClientConfigurationError("root cert"))
    assert not history_stores._is_tls_misconfig(ConnectionRefusedError())
    assert not history_stores._is_tls_misconfig(OSError("dns"))


@pytest.mark.asyncio
async def test_asyncpg_tls_failure_logs_an_error_not_unavailable(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "require")
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", AsyncMock(side_effect=ssl.SSLError("bad key"))), \
         patch("src.core.history_stores._record_error"), \
         patch("src.core.history_stores.logger") as log:
        assert await store.ping() is False
    assert log.error.called and not log.warning.called


@pytest.mark.asyncio
async def test_dsn_reaches_create_pool(monkeypatch, tls_env):
    monkeypatch.setattr(settings, "postgres_sslmode", "verify-full")
    monkeypatch.setattr(settings, "postgres_sslrootcert", certifi.where())
    create_pool = AsyncMock(side_effect=OSError("down"))
    with patch("asyncpg.create_pool", create_pool), patch("src.core.history_stores.logger"), \
         patch("src.core.history_stores._record_error"):
        await PostgresHistoryStore().ping()
    kwargs = create_pool.call_args.kwargs
    assert kwargs["ssl"] == "verify-full" and kwargs["dsn"].startswith("postgresql://?sslrootcert=")
    assert kwargs["host"] == settings.postgres_host  # explicit args still win over the DSN


# ---------------------------------------------------------------------------
# 4. scrub_urls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,leak,expected", [
    ("redis://:p@ss@cache:6379/0", "ss@", "redis://cache:6379/0"),
    ("redis://:pa/ss@cache:6379", "pa/ss", "redis://cache:6379"),
    ("postgresql://db:5432/x?password=hunter2&sslmode=require", "hunter2", "postgresql://db:5432/x"),
    ("wss://stream.example/ws?token=abc#frag", "abc", "wss://stream.example/ws"),
    ("amqp://user:s3cr3t@mq/vhost", "s3cr3t", "amqp://mq/vhost"),
    ("connect to admin:hunter2@db.internal:5432 failed", "hunter2", "connect to db.internal:5432 failed"),
    ("dsn user:p@ss@10.0.0.1:5432", "ss@", "dsn 10.0.0.1:5432"),
])
def test_scrub_urls_leaks_nothing(text, leak, expected):
    out = scrub_urls(text)
    assert leak not in out
    assert out == expected


def test_scrub_urls_does_not_mangle_an_email_in_a_query():
    assert scrub_urls("see https://host.example/p?email=a@evil.com now") == "see https://host.example/p now"


@pytest.mark.parametrize("text", [
    "https://registry.example/@scope/pkg",
    "https://host.example:8443/@scope/pkg",
    "contact ops@example.com for access",
    "McpError -32602: Invalid params",
    "time 10:30 at host",
])
def test_scrub_urls_leaves_clean_text_alone(text):
    assert scrub_urls(text) == text


def test_strip_url_query_handles_awkward_passwords():
    assert strip_url_query("redis://:p@ss@cache:6379/0?x=1") == "redis://cache:6379/0"
    assert strip_url_query("http://[::1]:9000/x?y=1") == "http://[::1]:9000/x"


# ---------------------------------------------------------------------------
# 5. Kubernetes ApiException status
# ---------------------------------------------------------------------------

def test_k8s_api_exception_keeps_its_status():
    assert transport_error_summary(ApiException(status=403, reason="Forbidden")) == "ApiException (HTTP 403)"
    assert transport_error_summary(ApiException(status=404, reason="Not Found")) == "ApiException (HTTP 404)"


def test_k8s_status_is_bounded_and_class_restricted():
    weird = ApiException(status=0, reason="x")
    assert transport_error_summary(weird) == "ApiException"
    other = OSError("x")
    other.status = 500  # type: ignore[attr-defined]
    assert transport_error_summary(other) == "OSError"


@pytest.mark.asyncio
async def test_k8s_adapter_reports_the_status():
    from src.core.adapters import KubernetesAdapter

    with patch("src.core.adapters.k8s_config.load_incluster_config"), \
         patch("src.core.adapters.k8s_client.CoreV1Api") as api, \
         patch("src.core.adapters.logger"):
        api.return_value.list_node.side_effect = ApiException(status=403, reason="Forbidden")
        out = await KubernetesAdapter()._collect("q")
    assert out == "[k8s] error: ApiException (HTTP 403)"


# ---------------------------------------------------------------------------
# 6. aclose
# ---------------------------------------------------------------------------

class _Pool:
    def __init__(self, conn: Any) -> None:
        self.conn, self.closed = conn, 0

    def acquire(self) -> "_Pool":
        return self

    async def __aenter__(self) -> Any:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed += 1


class _HangingConn:
    async def execute(self, sql: str, *args: Any) -> str:
        if "DELETE FROM aigent_history" in sql:
            await asyncio.Event().wait()  # never finishes on its own
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return "aigent_history" if "to_regclass" in sql else 1


@pytest.mark.asyncio
async def test_aclose_cancels_and_awaits_the_purge_and_closes_the_pool():
    pool = _Pool(_HangingConn())
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", AsyncMock(return_value=pool)), \
         patch("src.core.history_stores._record_error") as rec:
        assert await store.ping() is True
        task = store._purge_task
        assert task is not None and not task.done()
        await asyncio.wait_for(store.aclose(), timeout=1.0)
    assert task.cancelled()
    assert store._purge_task is None and store._pool is None and pool.closed == 1
    assert rec.call_count == 0  # a shutdown cancel is not an error
    await store.aclose()  # idempotent
    assert pool.closed == 1


@pytest.mark.asyncio
async def test_aclose_leaves_an_injected_pool_open():
    pool = _Pool(_HangingConn())
    store = PostgresHistoryStore(pool=pool)
    await store.aclose()
    assert pool.closed == 0


@pytest.mark.asyncio
async def test_aclose_survives_a_failing_pool_close():
    pool = MagicMock(close=AsyncMock(side_effect=OSError("x")))
    store = PostgresHistoryStore()
    store._pool = pool
    with patch("src.core.history_stores.logger") as log:
        await store.aclose()
    assert log.warning.called


@pytest.mark.asyncio
async def test_redis_store_aclose_is_a_noop():
    await history_stores.RedisHistoryStore(client=MagicMock()).aclose()


def test_supervisor_lifespan_closes_the_history_store():
    from fastapi.testclient import TestClient

    from src.supervisor import server

    with patch.object(server._history_store, "aclose", AsyncMock()) as aclose, \
         patch.object(server.kb_store, "connect", AsyncMock()), \
         patch.object(server.kb_store, "close", AsyncMock()), \
         patch("src.core.model_tier.validate_tier_models_at_startup"):
        with TestClient(server.app):
            aclose.assert_not_awaited()
    aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. /ready http labels
# ---------------------------------------------------------------------------

URL = "http://user:pw@svc.internal:9000/health?token=s3cr3t"


@pytest.mark.asyncio
async def test_http_check_is_labelled_by_name_not_url():
    checker = DependencyChecker(cache_ttl=60)
    ok = MagicMock(get=AsyncMock(return_value=MagicMock(status_code=200)))
    bad = MagicMock(get=AsyncMock(return_value=MagicMock(status_code=503)))
    a = await checker.check_http(URL, ok, name="grafana-mcp")
    with patch("src.core.health.logger"):
        b = await checker.check_http("http://other:1/h", bad, name="kubectl-mcp")
    assert a.detail == "http:grafana-mcp: 200"
    assert b.detail == "http:kubectl-mcp: status 503"  # separate cache entries


@pytest.mark.asyncio
async def test_unsafe_http_label_falls_back_and_timeout_uses_the_label():
    async def _slow(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(1)

    checker = DependencyChecker(cache_ttl=0, timeout=0.01)
    with patch("src.core.health.logger") as log:
        named = await checker.check_http(URL, MagicMock(get=_slow), name="svc")
        unsafe = await checker.check_http(URL, MagicMock(get=_slow), name="http://x?t=1")
    assert named.detail == "http:svc: timeout after 0.01s"
    assert unsafe.detail == "http: timeout after 0.01s"
    assert "s3cr3t" not in str(log.warning.call_args_list)
