"""Health check primitives — DependencyChecker with per-dep caching and timeout.

``DepResult.detail`` is served by the unauthenticated ``/ready`` endpoint, so it
never carries raw exception text (hostnames, DSNs, driver messages) nor a probed
URL: only the dependency, a short state and the exception CLASS. The full error
(and, for HTTP checks, the URL) is logged server-side instead (BACKLOG F-025 f).
"""
import asyncio
import re
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


# Probe-safe label for an HTTP check: a short token chosen by the caller (the
# check/datasource name), never derived from the URL.
_LABEL = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _http_label(name: str | None) -> str:
    if name and _LABEL.fullmatch(name):
        return f"http:{name}"
    return "http"


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

    async def _run(
        self,
        key: str,
        coro_fn: Callable[[], Awaitable[DepResult]],
        subject: str | None = None,
        log_target: str | None = None,
    ) -> DepResult:
        """Run one check. ``subject`` is the probe-safe label (default: ``key``);
        ``log_target`` (a URL) goes to the log only, scrubbed."""
        subject = subject or key
        cached = self._cached(key)
        if cached is not None:
            return cached
        try:
            result = await asyncio.wait_for(coro_fn(), timeout=self._timeout)
        except asyncio.TimeoutError:
            if log_target is not None:
                logger.warning("readiness check timed out", extra={
                    "dependency": subject, "target": _log_url(log_target),
                })
            result = DepResult(ok=False, detail=f"{subject}: timeout after {self._timeout}s")
        except Exception as exc:
            result = failure(subject, exc)
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

    async def check_http(self, url: str, client: Any, name: str | None = None) -> DepResult:
        """HTTP dependency check. ``name`` labels the result (``http:<name>``) so
        operators can tell probes apart; the URL never reaches ``detail``.
        A ``name`` that is not a short ``[A-Za-z0-9._-]`` token falls back to ``http``.
        """
        label = _http_label(name)
        key = f"{label}|{url}"

        async def _check() -> DepResult:
            try:
                resp = await client.get(url, timeout=self._timeout)
                if resp.status_code < 500:
                    return DepResult(ok=True, detail=f"{label}: {resp.status_code}")
                logger.warning("readiness check failed", extra={
                    "dependency": label, "url": _log_url(url), "status": resp.status_code,
                })
                return DepResult(ok=False, detail=f"{label}: status {resp.status_code}")
            except Exception as exc:
                return failure(label, exc, url=_log_url(url))

        return await self._run(key, _check, subject=label, log_target=url)
