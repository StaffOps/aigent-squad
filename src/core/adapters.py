"""Datasource adapters — read-only collectors for agent context."""
from __future__ import annotations

import hashlib
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

import boto3
import httpx
from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.config import ConfigException

from src.core.agent_config import DatasourceConfig
from src.core.cache import cache
from src.core.metrics import cache_hits, cache_misses


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
            return hit
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

    async def _collect(self, query: str) -> str:
        parts = []
        for svc in self.services:
            try:
                parts.append(self._collect_service(svc))
            except Exception as e:
                parts.append(f"[{svc}] error: {e}")
        return "\n".join(parts)

    def _collect_service(self, svc: str) -> str:
        if svc == "ec2":
            c = boto3.client("ec2")
            r = c.describe_instances()
            instances = [i for res in r["Reservations"] for i in res["Instances"]]
            running = sum(1 for i in instances if i["State"]["Name"] == "running")
            return f"[ec2] {len(instances)} instances ({running} running)"
        elif svc == "s3":
            c = boto3.client("s3")
            buckets = c.list_buckets().get("Buckets", [])
            return f"[s3] {len(buckets)} buckets"
        elif svc == "rds":
            c = boto3.client("rds")
            dbs = c.describe_db_instances()["DBInstances"]
            summary = ", ".join(f"{d['DBInstanceIdentifier']}({d['DBInstanceStatus']})" for d in dbs[:5])
            return f"[rds] {len(dbs)} instances: {summary}"
        elif svc == "ce":
            c = boto3.client("ce")
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
            c = boto3.client("iam")
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
            return f"[k8s] error: {e}"


class HttpAdapter(DatasourceAdapter):
    """Collects data from an HTTP endpoint."""

    def __init__(self, name: str, url: str, headers: dict[str, str]):
        self.name = name
        self.url = self._interpolate_env(url)
        self.headers = headers

    @staticmethod
    def _interpolate_env(url: str) -> str:
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), url)

    def _cache_id(self) -> str:
        return f"http:{self.name}:{self.url}"

    async def _collect(self, query: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(self.url, headers=self.headers)
                r.raise_for_status()
                return f"[http:{self.name}] {r.text[:2000]}"
        except Exception as e:
            return f"[http:{self.name}] error: {e}"


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
            return f"[athena:{self.table}] error: {e}"


class McpAdapter(DatasourceAdapter):
    """Collects read-only context from an MCP server (SSE transport).

    Security: tools are fail-closed. Only tools explicitly listed in the
    YAML allowlist (`tools`) may be invoked. An empty allowlist collects
    nothing. This keeps the adapter consistent with "read-only is law":
    the operator curates which (read-only) tools an agent may call, and the
    model never picks tools autonomously.
    """

    def __init__(
        self,
        name: str,
        url: str,
        tools: list[str],
        headers: dict[str, str] | None = None,
        tool_arguments: dict[str, str] | None = None,
        inject_query_as: str = "",
    ):
        self.name = name or "mcp"
        self.url = HttpAdapter._interpolate_env(url)
        self.tools = tools or []
        self.headers = headers or {}
        self.tool_arguments = tool_arguments or {}
        self.inject_query_as = inject_query_as

    def _cache_id(self) -> str:
        return f"mcp:{self.url}:" + ",".join(sorted(self.tools))

    async def _collect(self, query: str) -> str:
        if not self.tools:
            # Fail-closed: no allowlisted tools => nothing callable.
            return f"[mcp:{self.name}] no tools allowlisted (skipped)"
        results: list[str] = []
        try:
            # Imported lazily so the rest of the adapters work even if the
            # mcp client extras are unavailable in a given environment.
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(self.url, headers=self.headers) as (read, write):
                async with ClientSession(read, write) as session:
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
                            results.append(f"[mcp:{self.name}:{tool_name}] error: {e}")
        except Exception as e:
            # The SSE transport (mcp 1.0.0 + anyio) can raise an exception
            # group during stream teardown *after* tools ran successfully.
            # Don't discard data we already collected — only surface the
            # error when nothing was gathered.
            if not results:
                return f"[mcp:{self.name}] error: {e}"
        return "\n".join(results)

    @staticmethod
    def _render(result) -> str:
        """Flatten an MCP CallToolResult into text (text content blocks only)."""
        parts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        return ("\n".join(parts))[:4000] if parts else "(no text content)"


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
            ))
    for adapter in adapters:
        adapter.cache_ttl = cache_ttl
        adapter.cache_namespace = cache_namespace
    return adapters
