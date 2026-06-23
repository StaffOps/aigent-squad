from otel_helper import setup_telemetry

# CRITICAL (spec 31 / round-table): setup_telemetry MUST run before any project
# import that creates tracers/meters at module load (e.g. src.core.metrics).
# Otherwise those modules capture ProxyMeter and never export.
setup_telemetry()

import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Optional  # noqa: E402

import redis.asyncio as aioredis  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import uvicorn  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.gateway import __version__  # noqa: E402
from src.gateway.auth import require_edge_auth  # noqa: E402
from src.gateway.supervisor_client import (  # noqa: E402
    SupervisorClient,
    SupervisorUnavailableError,
)
from src.gateway.worker_pool import PoolFullError, WorkerPool  # noqa: E402
from src.supervisor.openai_compat import (  # noqa: E402
    ChatCompletionRequest,
    UnknownModelError,
    build_completion,
    list_models,
    messages_to_user_input,
    resolve_target,
    sse_stream,
)

# Redis is used only for job lifecycle + cancellation (fail-open). The gateway
# stays serving if Redis is down (pool is in-memory; lifecycle degrades to logs).
try:
    _redis = aioredis.from_url(
        f"redis://{settings.redis_host}:{settings.redis_port}",
        password=settings.redis_password,
        decode_responses=True,
    )
except Exception:  # pragma: no cover - construction never blocks
    _redis = None

supervisor_client = SupervisorClient()
worker_pool = WorkerPool(
    max_concurrent=settings.gateway_max_concurrent,
    job_timeout=settings.gateway_job_timeout_seconds,
    first_byte_timeout=settings.gateway_first_byte_timeout_seconds,
    idle_timeout=settings.gateway_idle_stream_timeout_seconds,
    cancel_poll_interval=settings.gateway_cancel_poll_seconds,
    redis_client=_redis,
)

# Agent names exposed by the OpenAI bridge. The gateway holds no registry; it
# learns them from the supervisor at startup (best-effort; refreshable).
_agent_names: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await supervisor_client.aclose()


app = FastAPI(title="AIgent-squad Gateway", version=__version__, lifespan=lifespan)


class QueryRequest(BaseModel):
    user_input: str
    user_id: str
    session_id: str
    mode: Optional[str] = None


def _retry_after() -> int:
    """Dynamic Retry-After (seconds) with jitter, based on current saturation."""
    import math
    import random

    base = math.ceil((worker_pool.active_count / max(worker_pool.max_capacity, 1)) * 5)
    return max(1, base) + random.randint(0, 3)  # nosec B311 - jitter, not crypto


def _busy_response(subtype: str, detail: str):
    """503 with a subtype the caller can act on (round-table decision)."""
    return JSONResponse(
        status_code=503,
        content={"error": {"type": subtype, "message": detail}},
        headers={"Retry-After": str(_retry_after())},
    )


@app.post("/query", dependencies=[Depends(require_edge_auth)])
async def query(request: QueryRequest):
    """Native entrypoint — admission control, then forward to the supervisor."""
    if not worker_pool.has_capacity():
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    if not await supervisor_client.is_supervisor_ready():
        return _busy_response("backend_unavailable", "supervisor backend is not ready")

    job_id = str(uuid.uuid4())

    async def _one_shot():
        result = await supervisor_client.process(
            user_input=request.user_input,
            user_id=request.user_id,
            session_id=request.session_id,
            mode=request.mode or "query",
        )
        # Stash the dict result for the caller via a sentinel chunk.
        yield result

    try:
        pooled = await worker_pool.submit(job_id, _one_shot())
        result = None
        async for item in pooled:
            result = item
    except PoolFullError:
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    except SupervisorUnavailableError:
        return _busy_response("backend_unavailable", "supervisor backend unavailable")
    except Exception as exc:
        return _forward_error(exc)
    return result


@app.get("/v1/models", dependencies=[Depends(require_edge_auth)])
async def openai_list_models():
    names = _agent_names or await _refresh_agents()
    return list_models(names).model_dump()


@app.post("/v1/chat/completions", dependencies=[Depends(require_edge_auth)])
async def openai_chat_completions(
    request: ChatCompletionRequest,
    x_session_id: str = Header(default=""),
):
    """OpenAI Chat Completions → admission → supervisor (shaping reused, spec 29)."""
    names = _agent_names or await _refresh_agents()
    try:
        force_agent = resolve_target(request.model, names)
    except UnknownModelError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": {"type": "invalid_request_error", "message": str(exc)}},
        )

    if not worker_pool.has_capacity():
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    if not await supervisor_client.is_supervisor_ready():
        return _busy_response("backend_unavailable", "supervisor backend is not ready")

    user_input = messages_to_user_input(request.messages)
    user_id = request.user or "librechat"
    session_id = x_session_id or f"openai-{user_id}"
    job_id = str(uuid.uuid4())

    async def _one_shot():
        yield await supervisor_client.process(
            user_input=user_input,
            user_id=user_id,
            session_id=session_id,
            force_agent=force_agent,
        )

    try:
        pooled = await worker_pool.submit(job_id, _one_shot())
        result = None
        async for item in pooled:
            result = item
    except PoolFullError:
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    except SupervisorUnavailableError:
        return _busy_response("backend_unavailable", "supervisor backend unavailable")
    except Exception as exc:
        return _forward_error(exc)

    if request.stream:
        return StreamingResponse(
            sse_stream(result, request.model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return build_completion(result, request.model).model_dump()


@app.post("/jobs/{job_id}/cancel", status_code=202, dependencies=[Depends(require_edge_auth)])
async def cancel_job(job_id: str):
    """Signal cancellation; the worker stops within ~one poll interval."""
    cancelled = await worker_pool.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="job not found or not running")
    return {"job_id": job_id, "status": "cancelling"}


@app.get("/healthz")
async def healthz():
    """Liveness — never checks external deps."""
    return {"status": "ok", "service": "gateway"}


@app.get("/ready")
async def ready():
    """Readiness — Redis reachable + pool functional.

    Intentionally does NOT check the supervisor (round-table): coupling would
    turn a supervisor outage into a gateway-removed-from-LB cascade. Supervisor
    availability is handled per-request via preflight → 503.
    """
    checks = {"pool": {"ok": worker_pool.has_capacity() or worker_pool.active_count >= 0}}
    redis_ok = True
    if _redis is not None:
        try:
            await _redis.ping()
        except Exception as exc:  # fail-open: degraded, not down
            redis_ok = False
            checks["redis"] = {"ok": False, "detail": str(exc)}
    if redis_ok:
        checks["redis"] = {"ok": True}
    # Pool is always functional in-process; Redis is fail-open, so readiness is
    # driven by the pool. Redis-down is reported but does not flip readiness.
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})


@app.get("/health", include_in_schema=False)
async def health_legacy():
    return {"status": "ok", "service": "gateway"}


async def _refresh_agents() -> list[str]:
    """Fetch agent names from the supervisor and cache them (for /v1/models).

    Best-effort: on failure the cache stays as-is (the auto `aigent-squad` model
    always works regardless). Refreshed lazily on first /v1 call.
    """
    global _agent_names
    names = await supervisor_client.list_agents()
    if names:
        _agent_names = names
    return _agent_names


def _forward_error(exc: Exception):
    """Map a forwarded supervisor error to an HTTP response.

    A 403 from the supervisor is the guardrail (spec 14) — surface it as-is.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 403:
            return JSONResponse(
                status_code=403,
                content={"error": {"type": "guardrail_blocked", "message": "Request blocked by security guardrail"}},
            )
        return JSONResponse(status_code=status, content={"error": {"type": "backend_error", "message": "supervisor error"}})
    raise exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104 — containerized service must bind all interfaces
