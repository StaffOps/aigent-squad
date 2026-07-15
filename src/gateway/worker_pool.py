"""Gateway worker pool — bounded local concurrency + backpressure (spec 31, L2).

Pattern adapted from a sibling internal project (`staffops-chaitops`
`agent-api/app/worker_pool.py`, reuse authorized within the org — see
`steering/licensing-clean-room.md` for the third-party-vs-internal reuse
distinction). Differences from the reference:
  - A two-timeout model (first-byte + idle-stream) on top of the overall job
    timeout, tuned for a Bedrock-backed workload (round-table 2026-06-22).
  - Job lifecycle is fail-open: a Redis outage degrades to log-only, never
    blocks serving (a metric flags the degraded mode).

Concurrency model (spec 31 Decision 1): the semaphore here is LOCAL (per
replica) — it protects this pod's resources. Account-wide limits (budget/TPS)
are enforced separately at admission via Redis (spec 25 guards). The local pool
never depends on Redis, so pod self-protection survives a Redis outage.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from src.core.logger import logger
from src.core.metrics import (
    gateway_pool_depth,
    gateway_pool_rejections,
    gateway_queue_wait,
    gateway_redis_fallback_active,
)


class PoolFullError(Exception):
    """Raised when the pool is at capacity — maps to HTTP 503 (backpressure)."""


class FirstByteTimeout(Exception):
    """The backend produced no output within first_byte_timeout."""


class IdleStreamTimeout(Exception):
    """The backend stalled mid-stream (no data within idle_timeout)."""


class WorkerPool:
    """Bounded-concurrency pool for forwarded gateway jobs.

    - ``asyncio.Semaphore`` gates concurrent execution (immediate-reject, no queue)
    - cancellation via a Redis key (``cancel:<job_id>``) polled by a side task
    - three timeouts: first-byte, idle-stream, and an overall job backstop
    """

    def __init__(
        self,
        max_concurrent: int,
        job_timeout: float,
        first_byte_timeout: float,
        idle_timeout: float,
        cancel_poll_interval: float = 0.5,
        redis_client=None,
    ):
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._job_timeout = job_timeout
        self._first_byte_timeout = first_byte_timeout
        self._idle_timeout = idle_timeout
        self._cancel_poll = cancel_poll_interval
        self._redis = redis_client
        self._active: dict[str, asyncio.Task] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def max_capacity(self) -> int:
        """Configured max concurrent jobs (public — for saturation math)."""
        return self._max

    @property
    def available(self) -> int:
        return self._max - len(self._active)

    def has_capacity(self) -> bool:
        """Non-blocking capacity check (used for preflight + readiness)."""
        return self.available > 0

    async def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True if the job is currently active."""
        if job_id not in self._active:
            return False
        await self._set_cancel_flag(job_id)
        return True

    async def submit(
        self,
        job_id: str,
        stream: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        """Admit a job and return a pool-managed streaming generator.

        Raises ``PoolFullError`` immediately (no queueing) when at capacity.
        """
        if not self.has_capacity():
            gateway_pool_rejections.add(1)
            raise PoolFullError("worker pool at capacity")
        return self._run(job_id, stream)

    async def _run(
        self,
        job_id: str,
        stream: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        queue_start = time.time()
        await self._sem.acquire()
        gateway_queue_wait.record((time.time() - queue_start) * 1000)
        gateway_pool_depth.add(1)

        self._active[job_id] = asyncio.current_task()  # type: ignore[assignment]
        poll_task: Optional[asyncio.Task] = asyncio.create_task(self._poll_cancel(job_id))
        first_byte_seen = False
        try:
            while True:
                # First chunk uses the first-byte budget; later chunks the idle budget.
                budget = self._idle_timeout if first_byte_seen else self._first_byte_timeout
                try:
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=budget)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    if first_byte_seen:
                        await self._update_status(job_id, "idle_timeout")
                        raise IdleStreamTimeout(job_id) from exc
                    await self._update_status(job_id, "first_byte_timeout")
                    raise FirstByteTimeout(job_id) from exc
                first_byte_seen = True
                yield chunk
            await self._update_status(job_id, "completed")
        except asyncio.CancelledError:
            await self._update_status(job_id, "cancelled")
            raise
        except (FirstByteTimeout, IdleStreamTimeout):
            raise
        except Exception:
            await self._update_status(job_id, "failed")
            raise
        finally:
            self._active.pop(job_id, None)
            if poll_task and not poll_task.done():
                poll_task.cancel()
            gateway_pool_depth.add(-1)
            self._sem.release()
            await self._clear_cancel_flag(job_id)
            try:
                await stream.aclose()
            except Exception:  # best-effort cleanup; never shadow the real error
                pass

    async def _poll_cancel(self, job_id: str) -> None:
        """Poll the Redis cancel key; cancel the job task when set."""
        if self._redis is None:
            return
        try:
            while job_id in self._active:
                try:
                    if await self._redis.get(f"cancel:{job_id}"):
                        task = self._active.get(job_id)
                        if task:
                            task.cancel()
                        return
                except Exception:
                    return  # Redis hiccup — cancellation is best-effort
                await asyncio.sleep(self._cancel_poll)
        except asyncio.CancelledError:
            pass

    async def _set_cancel_flag(self, job_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(f"cancel:{job_id}", "1", ex=60)
        except Exception:
            logger.warning("cancel flag set failed", extra={"job_id": job_id})

    async def _clear_cancel_flag(self, job_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(f"cancel:{job_id}")
        except Exception:
            pass

    async def _update_status(self, job_id: str, status: str) -> None:
        """Persist job status to Redis; fail-open to log-only on Redis outage."""
        record = {
            "job_id": job_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._redis is None:
            return
        try:
            await self._redis.set(f"job:{job_id}", json.dumps(record))
        except Exception:
            gateway_redis_fallback_active.add(1)
            logger.warning("job status redis fallback", extra={**record})
