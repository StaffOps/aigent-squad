"""Health check primitives — DependencyChecker with per-dep caching and timeout.

``DepResult.detail`` is served by the unauthenticated ``/ready`` endpoint, so it
never carries raw exception text (hostnames, DSNs, driver messages) nor a probed
URL: only the dependency, a short state and the exception CLASS. The full error
(and, for HTTP checks, the URL) is logged server-side instead (BACKLOG F-025 f).
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from src.core.logger import logger


@dataclass
class DepResult:
    ok: bool
    detail: str
    checked_at: float = field(default_factory=time.monotonic)


def failure(subject: str, exc: BaseException, **log_extra: Any) -> DepResult:
    """Generic, probe-safe failure result; the full error goes to the log only."""
    logger.warning("readiness check failed", extra={
        "dependency": subject, "error_type": type(exc).__name__, "error": str(exc), **log_extra,
    })
    return DepResult(ok=False, detail=f"{subject} unreachable ({type(exc).__name__})")


def _log_url(url: str) -> str:
    # Lazy: adapters pulls in boto3/kubernetes, which the probe module must not need.
    from src.core.adapters import strip_url_query

    return strip_url_query(url)


def _subject(key: str) -> str:
    # Cache keys are "<dependency>" or "<dependency>:<target>" (http:<url>);
    # only the dependency part is probe-safe.
    return key.split(":", 1)[0]


class DependencyChecker:
    """Checks external dependencies with a per-dep TTL cache and per-check timeout."""

    def __init__(self, cache_ttl: float = 5.0, timeout: float = 2.0):
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        self._cache: dict[str, DepResult] = {}

    def _cached(self, key: str) -> DepResult | None:
        result = self._cache.get(key)
        if result and (time.monotonic() - result.checked_at) < self._cache_ttl:
            return result
        return None

    async def _run(self, key: str, coro_fn: Callable[[], Awaitable[DepResult]]) -> DepResult:
        cached = self._cached(key)
        if cached is not None:
            return cached
        try:
            result = await asyncio.wait_for(coro_fn(), timeout=self._timeout)
        except asyncio.TimeoutError:
            if _subject(key) != key:
                logger.warning("readiness check timed out", extra={
                    "dependency": _subject(key), "target": _log_url(key.split(":", 1)[1]),
                })
            result = DepResult(ok=False, detail=f"{_subject(key)}: timeout after {self._timeout}s")
        except Exception as exc:
            result = failure(_subject(key), exc)
        self._cache[key] = result
        return result

    async def check_redis(self, redis_client: Any) -> DepResult:
        async def _check() -> DepResult:
            try:
                # redis-py sync ping wrapped in thread so we don't block the loop
                pong = await asyncio.to_thread(redis_client.ping)
                if pong:
                    return DepResult(ok=True, detail="redis: pong")
                return DepResult(ok=False, detail="redis: no pong")
            except Exception as exc:
                return failure("redis", exc)

        return await self._run("redis", _check)

    async def check_dynamodb(self, dynamodb_table: Any) -> DepResult:
        async def _check() -> DepResult:
            try:
                await asyncio.to_thread(dynamodb_table.load)
                return DepResult(ok=True, detail="dynamodb: table reachable")
            except Exception as exc:
                return failure("dynamodb", exc)

        return await self._run("dynamodb", _check)

    async def check_history(self, store: Any) -> DepResult:
        """History store readiness (spec 46): backends expose ``async ping()``."""
        backend = getattr(store, "backend", "unknown")

        async def _check() -> DepResult:
            try:
                if await store.ping():
                    return DepResult(ok=True, detail=f"history: {backend} reachable")
                return DepResult(ok=False, detail=f"history: {backend} unreachable")
            except Exception as exc:
                return failure(f"history: {backend}", exc)

        return await self._run("history", _check)

    async def check_bedrock_creds(self, sts_client: Any) -> DepResult:
        async def _check() -> DepResult:
            try:
                await asyncio.to_thread(sts_client.get_caller_identity)
                return DepResult(ok=True, detail="bedrock-creds: identity ok")
            except Exception as exc:
                return failure("bedrock-creds", exc)

        return await self._run("bedrock-creds", _check)

    async def check_http(self, url: str, client: Any) -> DepResult:
        key = f"http:{url}"

        async def _check() -> DepResult:
            try:
                resp = await client.get(url, timeout=self._timeout)
                if resp.status_code < 500:
                    return DepResult(ok=True, detail=f"http: {resp.status_code}")
                logger.warning("readiness check failed", extra={
                    "dependency": "http", "url": _log_url(url), "status": resp.status_code,
                })
                return DepResult(ok=False, detail=f"http: status {resp.status_code}")
            except Exception as exc:
                return failure("http", exc, url=_log_url(url))

        return await self._run(key, _check)
