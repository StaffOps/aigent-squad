"""Datasource adapters — read-only collectors for agent context."""
from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import boto3
import httpx
from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.config import ConfigException

from src.core.agent_config import DatasourceConfig
from src.core.cache import cache
from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import cache_hits, cache_misses

if TYPE_CHECKING:
    from src.core.tool_schema import ToolNameMap


# Maximum nesting we will unwrap. Groups nest at most a couple of levels in
# practice; the bound exists so a pathological chain cannot spin.
_MAX_UNWRAP_DEPTH = 5


def mcp_error_detail(exc: BaseException, _depth: int = 0) -> str:
    """Describe an MCP failure by its ROOT CAUSE, not by its wrapper.

    The MCP client runs its connection inside an anyio TaskGroup, so a plain
    `str(exc)` yields ``unhandled errors in a TaskGroup (1 sub-exception)`` —
    which tells an operator reading the log absolutely nothing about why the
    server is unreachable. The real cause (``connection refused``, a DNS
    failure, a TLS error, a timeout) is one or two levels down.

    Fail-open behaviour is unchanged: callers still return an error STRING and
    never raise. This only makes that string useful. (BACKLOG F-011.)

    Returns ``"<ExceptionType>: <message>"`` for the innermost exception, and
    appends ``(+N more)`` when a group carried several so nothing is hidden.
    """
    if _depth < _MAX_UNWRAP_DEPTH:
        # ExceptionGroup / BaseExceptionGroup (Python 3.11+) — anyio's wrapper.
        subs = getattr(exc, "exceptions", None)
        if subs:
            detail = mcp_error_detail(subs[0], _depth + 1)
            extra = len(subs) - 1
            return f"{detail} (+{extra} more)" if extra > 0 else detail
        # Plain chained exception (`raise X from Y`).
        cause = getattr(exc, "__cause__", None)
        if cause is not None and cause is not exc:
            return mcp_error_detail(cause, _depth + 1)

    msg = str(exc).strip()
    name = type(exc).__name__
    # Some transport errors stringify to "" — the type alone is still a clue.
    return f"{name}: {msg}" if msg else name


def _aws_error_code(exc: BaseException) -> str | None:
    """botocore ClientError code (e.g. ``AccessDenied``); None for anything else.

    Duck-typed on ``exc.response["Error"]["Code"]`` (the botocore shape) and
    restricted to a code-like token, so a message, ARN or account id can never
    ride along.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    if isinstance(code, str) and _AWS_CODE.fullmatch(code):
        return code
    return None


_AWS_CODE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def transport_error_summary(exc: BaseException, _depth: int = 0) -> str:
    """LLM-safe description of a transport failure: exception CLASS + HTTP status.

    Datasource errors are returned into the LLM context (fail-open), and
    exception messages carry addresses — httpx says ``... for url '<url>'``,
    query strings included (tokens, scoped ids). So the context only ever gets
    the root-cause class name (unwrapped like :func:`mcp_error_detail`) and the
    HTTP status when there is one; the full message goes to the log through
    :func:`log_transport_error` (BACKLOG F-025 b).
    """
    aws_code = _aws_error_code(exc)
    if aws_code is not None:
        return f"{type(exc).__name__} ({aws_code})"
    status = _http_status(exc)
    if _depth < _MAX_UNWRAP_DEPTH and status is None:
        subs = getattr(exc, "exceptions", None)
        if subs:
            summary = transport_error_summary(subs[0], _depth + 1)
            extra = len(subs) - 1
            return f"{summary} (+{extra} more)" if extra > 0 else summary
        cause = getattr(exc, "__cause__", None)
        if cause is not None and cause is not exc:
            return transport_error_summary(cause, _depth + 1)
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if status is not None else name


# Userinfo (``//user:pass@``) anywhere in free text, and a query/fragment tail
# after a URL: both are where credentials live.
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@'\"]*@")
_URL_TAIL = re.compile(r"(https?://[^\s'\"?#]*)[?#][^\s'\"]*")


def scrub_urls(text: str) -> str:
    """``text`` with URL userinfo and query strings/fragments removed."""
    return _URL_TAIL.sub(r"\1", _URL_USERINFO.sub(r"\1", text))


def strip_url_query(url: str) -> str:
    """``url`` without userinfo, query string or fragment (where credentials live)."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:  # malformed port: keep nothing we cannot parse
        port = None
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def log_transport_error(source: str, url: str | None, exc: BaseException) -> None:
    """Operator-side record of a datasource failure: full root cause, no credentials."""
    extra: dict[str, Any] = {
        "datasource": source,
        "error_type": type(exc).__name__,
        "error": scrub_urls(mcp_error_detail(exc)),
    }
    if url:
        extra["url"] = strip_url_query(url)
    logger.warning("datasource transport error (fail-open)", extra=extra)


# MCP protocol (JSON-RPC) error text handed back to the model, capped.
MCP_PROTOCOL_ERROR_MAX_CHARS = 200


def _find_mcp_error(exc: BaseException, _depth: int = 0) -> BaseException | None:
    """The ``McpError`` behind ``exc`` (unwrapping groups/causes), if any."""
    from mcp.shared.exceptions import McpError  # lazy, like the other mcp imports here

    # Kept as a bool: mcp ships no type info, so isinstance() would narrow exc to Any.
    is_mcp_error = isinstance(exc, McpError)
    if is_mcp_error:
        return exc
    if _depth >= _MAX_UNWRAP_DEPTH:
        return None
    subs = getattr(exc, "exceptions", None)
    if subs:
        for sub in subs:
            found = _find_mcp_error(sub, _depth + 1)
            if found is not None:
                return found
        return None
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return _find_mcp_error(cause, _depth + 1)
    return None


def mcp_protocol_error_summary(exc: BaseException) -> str | None:
    """``McpError <code>: <message>`` for a JSON-RPC error, None for anything else.

    A JSON-RPC error (invalid params, unknown tool, ...) is the SERVER answering:
    an application error the model can act on in the agentic loop, so its code
    and message go back, scrubbed of URLs/credentials and capped. Transport
    failures keep the class-only :func:`transport_error_summary`.
    """
    err = _find_mcp_error(exc)
    if err is None:
        return None
    data = getattr(err, "error", None)
    code = getattr(data, "code", None)
    message = getattr(data, "message", None)
    if not isinstance(message, str):
        message = str(err)
    message = " ".join(scrub_urls(message).split())
    if len(message) > MCP_PROTOCOL_ERROR_MAX_CHARS:
        message = message[: MCP_PROTOCOL_ERROR_MAX_CHARS - 3] + "..."
    head = f"McpError {code}" if isinstance(code, int) else "McpError"
    return f"{head}: {message}" if message else head


def tool_error_summary(exc: BaseException) -> str:
    """LLM-facing summary of an MCP tool failure: protocol error detail or transport class."""
    return mcp_protocol_error_summary(exc) or transport_error_summary(exc)



class DatasourceAdapter(ABC):
    """Base class for all datasource adapters.

    `collect()` is a template method: it wraps a deterministic, TTL-bounded
    Redis cache (spec 30) around the concrete `_collect()` each subclass
    implements. Caching is per-agent (TTL + namespace from `agent.yaml`),
    fail-open, and disabled when `cache_ttl <= 0`.
    """

    cache_ttl: int = 0            # 0/negative → caching disabled
    cache_namespace: str = "default"

    @abstractmethod
    async def _collect(self, query: str) -> str:
        """Fetch fresh data for the query. Returns text summary."""

    def _cache_id(self) -> str:
        """Stable per-instance identity for the cache key. Override when the
        adapter's output depends on instance config (services, url, table…)."""
        return self.__class__.__name__

    async def collect(self, query: str) -> str:
        """Cache-wrapped collection. Deterministic sha256 key; fail-open."""
        if self.cache_ttl <= 0:
            return await self._collect(query)
        key = hashlib.sha256(f"{self._cache_id()}|{query}".encode()).hexdigest()
        ns = self.cache_namespace
        # Fail-open at the adapter boundary (invariant: cache loss never breaks
        # collection), regardless of the cache backend's own error handling.
        try:
            hit = cache.get(key, namespace=ns)
        except Exception:
            hit = None
        if hit is not None:
            cache_hits.add(1, {"namespace": ns})
            return str(hit)
        cache_misses.add(1, {"namespace": ns})
        result = await self._collect(query)
        try:
            cache.set(key, result, ttl=self.cache_ttl, namespace=ns)
        except Exception:
            pass
        return result


class Boto3Adapter(DatasourceAdapter):
    """Collects read-only inventory from AWS services."""

    def __init__(self, services: list[str]):
        self.services = services

    def _cache_id(self) -> str:
        return "boto3:" + ",".join(sorted(self.services))

    @staticmethod
    def _client(service: str) -> Any:
        """Build a boto3 client with an explicit region.

        botocore resolves the region from AWS_DEFAULT_REGION (not AWS_REGION),
        so relying on the ambient env is fragile — regional services (ec2, rds,
        ce) raise NoRegionError when only AWS_REGION is set (e.g. injected by the
        EKS IRSA webhook). Pass settings.aws_region explicitly instead.
        """
        return boto3.client(service, region_name=settings.aws_region)

    async def _collect(self, query: str) -> str:
        parts = []
        for svc in self.services:
            try:
                parts.append(self._collect_service(svc))
            except Exception as e:
                log_transport_error(f"boto3:{svc}", None, e)
                parts.append(f"[{svc}] error: {transport_error_summary(e)}")
        return "\n".join(parts)

    def _collect_service(self, svc: str) -> str:
        if svc == "ec2":
            c = self._client("ec2")
            r = c.describe_instances()
            instances = [i for res in r["Reservations"] for i in res["Instances"]]
            running = sum(1 for i in instances if i["State"]["Name"] == "running")
            return f"[ec2] {len(instances)} instances ({running} running)"
        elif svc == "s3":
            c = self._client("s3")
            buckets = c.list_buckets().get("Buckets", [])
            return f"[s3] {len(buckets)} buckets"
        elif svc == "rds":
            c = self._client("rds")
            dbs = c.describe_db_instances()["DBInstances"]
            summary = ", ".join(f"{d['DBInstanceIdentifier']}({d['DBInstanceStatus']})" for d in dbs[:5])
            return f"[rds] {len(dbs)} instances: {summary}"
        elif svc == "ce":
            c = self._client("ce")
            end = datetime.utcnow().strftime("%Y-%m-%d")
            start = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
            r = c.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
            )
            total = sum(float(p["Total"]["UnblendedCost"]["Amount"]) for p in r["ResultsByTime"])
            return f"[ce] last 30d cost: ${total:.2f}"
        elif svc == "iam":
            c = self._client("iam")
            roles = c.list_roles()["Roles"]
            return f"[iam] {len(roles)} roles"
        else:
            return f"[{svc}] unsupported service"


class KubernetesAdapter(DatasourceAdapter):
    """Collects K8s cluster summary."""

    async def _collect(self, query: str) -> str:
        try:
            try:
                k8s_config.load_incluster_config()
            except ConfigException:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            nodes = v1.list_node().items
            pods = v1.list_pod_for_all_namespaces().items
            namespaces = v1.list_namespace().items
            return (
                f"[k8s] {len(nodes)} nodes, {len(pods)} pods, "
                f"{len(namespaces)} namespaces"
            )
        except Exception as e:
            log_transport_error("k8s", None, e)
            return f"[k8s] error: {transport_error_summary(e)}"


class HttpAdapter(DatasourceAdapter):
    """Collects data from an HTTP endpoint."""

    def __init__(self, name: str, url: str, headers: dict[str, str]):
        self.name = name
        # ${VAR} is expanded exactly once, at agent load (spec 46, src/core/agent_env.py).
        # Never expand again here: an env VALUE containing "${OTHER}" must stay literal,
        # or a non-secret setting could pull a secret env var into an outbound URL.
        self.url = url
        self.headers = headers

    def _cache_id(self) -> str:
        return f"http:{self.name}:{self.url}"

    async def _collect(self, query: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(self.url, headers=self.headers)
                r.raise_for_status()
                return f"[http:{self.name}] {r.text[:2000]}"
        except Exception as e:
            log_transport_error(f"http:{self.name}", self.url, e)
            return f"[http:{self.name}] error: {transport_error_summary(e)}"


class AthenaAdapter(DatasourceAdapter):
    """Runs a sample query on Athena."""

    def __init__(self, database: str, table: str, workgroup: str = "primary"):
        self.database = database
        self.table = table
        self.workgroup = workgroup

    def _cache_id(self) -> str:
        return f"athena:{self.database}.{self.table}"

    async def _collect(self, query: str) -> str:
        try:
            c = boto3.client("athena")
            q = f"SELECT * FROM {self.table} LIMIT 5"
            r = c.start_query_execution(
                QueryString=q,
                QueryExecutionContext={"Database": self.database},
                WorkGroup=self.workgroup,
            )
            qid = r["QueryExecutionId"]
            # Poll for completion (max 30s)
            for _ in range(15):
                status = c.get_query_execution(QueryExecutionId=qid)
                state = status["QueryExecution"]["Status"]["State"]
                if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                    break
                time.sleep(2)
            if state != "SUCCEEDED":
                return f"[athena:{self.table}] query {state}"
            results = c.get_query_results(QueryExecutionId=qid)
            rows = results["ResultSet"]["Rows"]
            header = [c["VarCharValue"] for c in rows[0]["Data"]]
            lines = [", ".join(header)]
            for row in rows[1:]:
                lines.append(", ".join(c.get("VarCharValue", "") for c in row["Data"]))
            return f"[athena:{self.table}]\n" + "\n".join(lines)
        except Exception as e:
            log_transport_error(f"athena:{self.table}", None, e)
            return f"[athena:{self.table}] error: {transport_error_summary(e)}"


class McpAdapter(DatasourceAdapter):
    """Collects read-only context from an MCP server.

    Supports two transports:
      - 'sse' (default) — Server-Sent Events, compatible with mcp ≥1.0.
      - 'streamable-http' — HTTP-based streaming, required by servers like
        grafana-mcp and kubectl-mcp that do not expose SSE.

    Security: tools are fail-closed. Only tools explicitly listed in the
    YAML allowlist (`tools`) may be invoked. An empty allowlist collects
    nothing. This keeps the adapter consistent with "read-only is law":
    the operator curates which (read-only) tools an agent may call, and the
    model never picks tools autonomously.

    Agentic interface (Phase 2, spec 37):
      - `list_tool_specs()`: returns Converse-format `toolSpec[]` for the
        allowlisted tools. Cached per cache_ttl.
      - `call_tool(name, args)`: execute ONE allowlisted tool. Fail-closed:
        refuses any name not in allowlist.
    """

    def __init__(
        self,
        name: str,
        url: str,
        tools: list[str],
        headers: dict[str, str] | None = None,
        tool_arguments: dict[str, str] | None = None,
        inject_query_as: str = "",
        transport: str = "streamable-http",
    ):
        self.name = name or "mcp"
        self.url = url  # already expanded once at load (spec 46); never re-expand
        self.tools = tools or []
        self.headers = headers or {}
        self.tool_arguments = tool_arguments or {}
        self.inject_query_as = inject_query_as
        self.transport = transport
        # SR1: immutable allowlist set for O(1) membership checks (Phase-2 carry-over)
        self._allowlist_set: frozenset[str] = frozenset(self.tools)
        # Agentic: tool name map and cached specs (Phase 2)
        self._name_map: "ToolNameMap | None" = None
        self._cached_tool_specs: list[dict[str, Any]] | None = None
        self._specs_cached_at: float = 0.0

    def _cache_id(self) -> str:
        return f"mcp:{self.url}:" + ",".join(sorted(self.tools))

    # ------------------------------------------------------------------
    # Agentic tool-spec builder (Phase 2, spec 37 Decision 2)
    # ------------------------------------------------------------------

    async def list_tool_specs(self) -> list[dict[str, Any]]:
        """Return Converse-format toolSpec[] for this datasource's allowlisted tools.

        Fetches the MCP server's tool catalog (session.list_tools()), intersects
        with the YAML allowlist, normalizes each schema via tool_schema.py, and
        caches the result per cache_ttl.
        """
        # Check cache validity (time-based)
        if self._cached_tool_specs is not None and self.cache_ttl > 0:
            elapsed = time.time() - self._specs_cached_at
            if elapsed < self.cache_ttl:
                return self._cached_tool_specs

        from src.core.tool_schema import ToolNameMap

        name_map = ToolNameMap()
        specs: list[dict[str, Any]] = []

        if not self.tools:
            self._name_map = name_map
            self._cached_tool_specs = []
            self._specs_cached_at = time.time()
            return []

        try:
            from mcp import ClientSession

            if self.transport == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client
                async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        specs = self._build_specs_from_session(session, name_map, await session.list_tools())
            else:
                from mcp.client.sse import sse_client
                async with sse_client(self.url, headers=self.headers) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        specs = self._build_specs_from_session(session, name_map, await session.list_tools())
        except Exception as e:
            # Fail-open: if we can't reach the server, contribute no tools (degraded)
            log_transport_error(f"mcp:{self.name}", self.url, e)
            specs = []

        self._name_map = name_map
        self._cached_tool_specs = specs
        self._specs_cached_at = time.time()
        return specs

    def _build_specs_from_session(self, session: Any, name_map: "ToolNameMap", tools_result: Any) -> list[dict[str, Any]]:
        """Build toolSpec list from session.list_tools() result ∩ allowlist."""
        from src.core.tool_schema import build_tool_spec

        allowlist_set = self._allowlist_set
        specs: list[dict[str, Any]] = []
        for tool in tools_result.tools:
            if tool.name not in allowlist_set:
                continue
            input_schema = None
            if hasattr(tool, "inputSchema") and tool.inputSchema:
                input_schema = tool.inputSchema
            spec = build_tool_spec(
                server_name=tool.name,
                description=getattr(tool, "description", "") or "",
                input_schema=input_schema,
                name_map=name_map,
            )
            specs.append(spec)
        return specs

    # ------------------------------------------------------------------
    # Agentic single-tool execution (Phase 2, spec 37 Decision 2)
    # ------------------------------------------------------------------

    # OOM safety cap: prevents unbounded memory from a malicious MCP server.
    # Semantic truncation (with true-count marker) is done by the agentic loop
    # via _truncate_with_marker — this cap is strictly a memory guard.
    _ADAPTER_SAFETY_CAP: int = 1_000_000

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute ONE tool by its Converse name. Fail-closed on allowlist.

        Args:
            name: The tool name as the model emitted it (Converse-safe, possibly
                  with underscores replacing hyphens).
            args: Arguments dict from the model's toolUse block.

        Returns:
            Full text result from the tool (capped only at _ADAPTER_SAFETY_CAP
            for OOM protection). Semantic truncation with a true-count marker
            is applied downstream by the agentic loop's _truncate_with_marker.

        Raises:
            PermissionError: if name is not in the allowlist (fail-closed).
        """
        # Resolve the server-side name via the name map
        server_name = self._resolve_server_name(name)

        # FAIL-CLOSED: refuse if not allowlisted
        if server_name not in self._allowlist_set:
            raise PermissionError(
                f"[mcp:{self.name}] tool '{name}' (server: '{server_name}') "
                f"not in read-only allowlist. Refusing execution."
            )

        # Static tool_arguments (operator/environment config) win over model-chosen
        # args: they pin scope (e.g. a namespace) and must not be overridable by a
        # prompt-injected tool call.
        merged_args = {**args, **self.tool_arguments}

        try:
            from mcp import ClientSession

            if self.transport == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client
                async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(server_name, merged_args)
                        return self._render(result)[:self._ADAPTER_SAFETY_CAP]
            else:
                from mcp.client.sse import sse_client
                async with sse_client(self.url, headers=self.headers) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(server_name, merged_args)
                        return self._render(result)[:self._ADAPTER_SAFETY_CAP]
        except PermissionError:
            raise  # Re-raise allowlist violations
        except Exception as e:
            log_transport_error(f"mcp:{self.name}:{server_name}", self.url, e)
            return f"[mcp:{self.name}:{server_name}] error: {tool_error_summary(e)}"

    def _resolve_server_name(self, converse_name: str) -> str:
        """Resolve a Converse tool name back to the MCP server's real name.

        Uses the name map if available; falls back to reversing underscore→hyphen
        if the map hasn't been populated yet (e.g. list_tool_specs not called).
        """
        if self._name_map:
            server_name = self._name_map.to_server_name(converse_name)
            if server_name:
                return server_name
        # Fallback: try direct match first (name might be in allowlist as-is)
        if converse_name in self._allowlist_set:
            return converse_name
        # Try hyphen variant
        hyphen_name = converse_name.replace("_", "-")
        if hyphen_name in self._allowlist_set:
            return hyphen_name
        # Nothing matched — return as-is (will fail the allowlist check)
        return converse_name

    async def _collect(self, query: str) -> str:
        if not self.tools:
            # Fail-closed: no allowlisted tools => nothing callable.
            return f"[mcp:{self.name}] no tools allowlisted (skipped)"
        results: list[str] = []
        try:
            # Imported lazily so the rest of the adapters work even if the
            # mcp client extras are unavailable in a given environment.
            from mcp import ClientSession

            if self.transport == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client

                async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        results = await self._invoke_tools(session, query)
            else:
                from mcp.client.sse import sse_client

                async with sse_client(self.url, headers=self.headers) as (read, write):
                    async with ClientSession(read, write) as session:
                        results = await self._invoke_tools(session, query)
        except Exception as e:
            # The transport layer can raise an exception group during stream
            # teardown *after* tools ran successfully.  Don't discard data we
            # already collected — only surface the error when nothing was
            # gathered (fail-open for partial results).
            log_transport_error(f"mcp:{self.name}", self.url, e)
            if not results:
                return f"[mcp:{self.name}] error: {transport_error_summary(e)}"
        return "\n".join(results)

    async def _invoke_tools(self, session: Any, query: str) -> list[str]:
        """Shared session logic: initialize, list available tools, invoke each
        allowlisted tool, collect results. Factored out to avoid duplication
        across transport branches."""
        results: list[str] = []
        await session.initialize()
        available = {t.name for t in (await session.list_tools()).tools}
        for tool_name in self.tools:
            if tool_name not in available:
                results.append(f"[mcp:{self.name}:{tool_name}] not exposed by server")
                continue
            args = dict(self.tool_arguments)
            if self.inject_query_as:
                args[self.inject_query_as] = query
            try:
                res = await session.call_tool(tool_name, args)
                results.append(f"[mcp:{self.name}:{tool_name}] {self._render(res)}")
            except Exception as e:  # one tool failing must not kill the rest
                log_transport_error(f"mcp:{self.name}:{tool_name}", self.url, e)
                results.append(f"[mcp:{self.name}:{tool_name}] error: {tool_error_summary(e)}")
        return results

    @staticmethod
    def _render(result: Any) -> str:
        """Flatten an MCP CallToolResult into text (text content blocks only)."""
        parts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts) if parts else "(no text content)"


def create_adapters(
    datasource_configs: list[DatasourceConfig],
    cache_ttl: int = 0,
    cache_namespace: str = "default",
) -> list[DatasourceAdapter]:
    """Instantiate adapters from config, threading the agent's cache settings
    (TTL + namespace) onto each so `collect()` caches deterministically."""
    adapters: list[DatasourceAdapter] = []
    for cfg in datasource_configs:
        if cfg.type == "boto3":
            adapters.append(Boto3Adapter(services=cfg.services))
        elif cfg.type == "kubernetes":
            adapters.append(KubernetesAdapter())
        elif cfg.type == "http":
            adapters.append(HttpAdapter(name=cfg.name, url=cfg.url, headers=cfg.headers))
        elif cfg.type == "athena":
            adapters.append(AthenaAdapter(database=cfg.database, table=cfg.table, workgroup=cfg.workgroup))
        elif cfg.type == "mcp":
            adapters.append(McpAdapter(
                name=cfg.name,
                url=cfg.url,
                tools=cfg.tools,
                headers=cfg.headers,
                tool_arguments=cfg.tool_arguments,
                inject_query_as=cfg.inject_query_as,
                transport=cfg.transport,
            ))
    for adapter in adapters:
        adapter.cache_ttl = cache_ttl
        adapter.cache_namespace = cache_namespace
    return adapters
