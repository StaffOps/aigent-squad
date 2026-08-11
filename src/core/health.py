"""Health check primitives — DependencyChecker with per-dep caching and timeout."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class DepResult:
    ok: bool
    detail: str
    checked_at: float = field(default_factory=time.monotonic)


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
            result = DepResult(ok=False, detail=f"{key}: timeout after {self._timeout}s")
        except Exception as exc:
            result = DepResult(ok=False, detail=f"{key}: {exc}")
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
                return DepResult(ok=False, detail=f"redis: {exc}")

        return await self._run("redis", _check)

    async def check_dynamodb(self, dynamodb_table: Any) -> DepResult:
        async def _check() -> DepResult:
            try:
                await asyncio.to_thread(dynamodb_table.load)
                return DepResult(ok=True, detail="dynamodb: table reachable")
            except Exception as exc:
                return DepResult(ok=False, detail=f"dynamodb: {exc}")

        return await self._run("dynamodb", _check)

    async def check_bedrock_creds(self, sts_client: Any) -> DepResult:
        async def _check() -> DepResult:
            try:
                await asyncio.to_thread(sts_client.get_caller_identity)
                return DepResult(ok=True, detail="bedrock-creds: identity ok")
            except Exception as exc:
                return DepResult(ok=False, detail=f"bedrock-creds: {exc}")

        return await self._run("bedrock-creds", _check)

    async def check_http(self, url: str, client: Any) -> DepResult:
        key = f"http:{url}"

        async def _check() -> DepResult:
            try:
                resp = await client.get(url, timeout=self._timeout)
                if resp.status_code < 500:
                    return DepResult(ok=True, detail=f"http {url}: {resp.status_code}")
                return DepResult(ok=False, detail=f"http {url}: status {resp.status_code}")
            except Exception as exc:
                return DepResult(ok=False, detail=f"http {url}: {exc}")

        return await self._run(key, _check)
