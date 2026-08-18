from otel_helper import metrics_app, setup_telemetry

# CRITICAL (spec 31 / round-table): setup_telemetry MUST run before any project
# import that creates tracers/meters at module load (e.g. src.core.metrics).
# Otherwise those modules capture ProxyMeter and never export.
setup_telemetry()

import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Any, AsyncGenerator, Optional  # noqa: E402

import redis.asyncio as aioredis  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import uvicorn  # noqa: E402

from src.core.config import settings  # noqa: E402
from src.core.rate_limiter import AdmissionGuard, estimate_cost  # noqa: E402
from src.gateway import __version__  # noqa: E402
from src.gateway.auth import AuthResult, get_key_agent_map, require_edge_auth  # noqa: E402
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
# Global admission guards (rate + budget), Redis-coordinated, fail-open.
admission = AdmissionGuard(
    redis_client=_redis,
    rate_per_minute=settings.rate_limit_per_minute,
    daily_budget_usd=settings.daily_budget_usd,
)

# Agent names exposed by the OpenAI bridge. The gateway holds no registry; it
# learns them from the supervisor at startup (best-effort; refreshable).
_agent_names: list[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # G-5: validate GATEWAY_KEY_AGENT_MAP agent names against registry at startup.
    # Best-effort: warn and skip unknown agents (don't crash the gateway).
    import logging as _logging
    _log = _logging.getLogger("aigent_squad.gateway")
    key_agent_map = get_key_agent_map()
    if key_agent_map:
        startup_agents = await _refresh_agents()
        if startup_agents:
            for _masked_key, agent_name in key_agent_map.items():
                if agent_name not in startup_agents:
                    _log.warning(
                        "GATEWAY_KEY_AGENT_MAP: agent '%s' not found in registry "
                        "(available: %s). Key will auth but scoping will be ignored.",
                        agent_name,
                        ", ".join(startup_agents),
                    )
        else:
            _log.warning(
                "GATEWAY_KEY_AGENT_MAP configured but could not fetch agent list from "
                "supervisor at startup — agent name validation deferred."
            )
    yield
    await supervisor_client.aclose()


app = FastAPI(title="AIgent-squad Gateway", version=__version__, lifespan=lifespan)

# Prometheus scrape endpoint (otel-helper v0.2.0+): mounted on the app's own
# port rather than otel-helper's standalone listener, since that listener
# can't bind under multi-worker servers — set OTEL_METRICS_EXPORTER=
# otlp,prometheus and OTEL_HELPER_METRICS_PORT=0 to run both the existing
# OTLP push (traces/logs/metrics via the collector) AND this direct-scrape
# endpoint on the SAME MeterProvider. Unauthenticated by design, matching
# the /healthz, /ready convention below — metrics carry no user data.
#
# Known Starlette Mount quirk (verified live, not a bug here): a bare GET
# /metrics (no trailing slash) 307-redirects to /metrics/ — this is
# Mount's own default routing behavior for the exact mount path, and
# disabling it (redirect_slashes=False) makes /metrics 404 instead
# (Mount then ONLY answers /metrics/), which is worse. Real scrapers
# (Prometheus's Go http.Client, curl -L) follow 307 transparently, method
# preserved — harmless. The ServiceMonitor template targets /metrics/
# directly (trailing slash) to skip the hop in the one place it's
# actually worth avoiding.
app.mount("/metrics", metrics_app())


class QueryRequest(BaseModel):  # type: ignore[misc]
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


def _busy_response(subtype: str, detail: str) -> JSONResponse:
    """503 with a subtype the caller can act on (round-table decision)."""
    return JSONResponse(
        status_code=503,
        content={"error": {"type": subtype, "message": detail}},
        headers={"Retry-After": str(_retry_after())},
    )


async def _check_admission(user_id: str, max_tokens: int = 4096) -> Optional[JSONResponse]:
    """Global rate + budget admission (spec 31 L3). Fail-open.

    Returns ``None`` when admitted, or a ready-to-return JSONResponse (429 rate /
    503 budget) when denied. Carries `X-RateLimit-Remaining` /
    `X-Budget-Remaining-USD` headers either way.
    """
    if not settings.rate_budget_enabled:
        return None

    allowed, remaining = await admission.check_rate(user_id)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": {"type": "rate_limited", "message": "per-user rate limit exceeded"}},
            headers={"X-RateLimit-Remaining": "0", "Retry-After": "60"},
        )

    # Pessimistic pre-call estimate (input unknown at the edge → assume a modest
    # prompt; the supervisor's real token metric is the source of truth post-call).
    est = estimate_cost(input_tokens=1000, max_output_tokens=max_tokens)
    budget_ok, budget_remaining = await admission.check_budget(est)
    if not budget_ok:
        return JSONResponse(
            status_code=503,
            content={"error": {"type": "budget_exhausted", "message": "daily budget exhausted"}},
            headers={"X-Budget-Remaining-USD": f"{budget_remaining:.4f}", "Retry-After": "3600"},
        )
    return None


@app.post("/query", dependencies=[Depends(require_edge_auth)])  # type: ignore[untyped-decorator]
async def query(request: QueryRequest) -> Any:
    """Native entrypoint — admission control, then forward to the supervisor."""
    denied = await _check_admission(request.user_id)
    if denied is not None:
        return denied
    if not worker_pool.has_capacity():
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    if not await supervisor_client.is_supervisor_ready():
        return _busy_response("backend_unavailable", "supervisor backend is not ready")

    job_id = str(uuid.uuid4())

    async def _one_shot() -> Any:
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


@app.get("/v1/models", dependencies=[Depends(require_edge_auth)])  # type: ignore[untyped-decorator]
async def openai_list_models() -> Any:
    names = _agent_names or await _refresh_agents()
    return list_models(names).model_dump()


@app.post("/v1/chat/completions")  # type: ignore[untyped-decorator]
async def openai_chat_completions(
    request: ChatCompletionRequest,
    auth: AuthResult = Depends(require_edge_auth),
    x_session_id: str = Header(default=""),
) -> Any:
    """OpenAI Chat Completions → admission → supervisor (shaping reused, spec 29).

    Agent resolution priority (G-5):
      (a) Explicit forced model (aigent-squad-<agent>) → honor it always.
      (b) Auto-route model + consumer has mapped default agent → use that.
      (c) Else → normal classifier auto-route (None).
    """
    names = _agent_names or await _refresh_agents()
    try:
        force_agent = resolve_target(request.model, names)
    except UnknownModelError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": {"type": "invalid_request_error", "message": str(exc)}},
        )

    # G-5: apply consumer default agent when model is auto-route (force_agent is None)
    if force_agent is None and auth and auth.consumer_default_agent:
        force_agent = auth.consumer_default_agent

    user_id = request.user or "librechat"
    denied = await _check_admission(user_id, max_tokens=request.max_tokens or 4096)
    if denied is not None:
        return denied
    if not worker_pool.has_capacity():
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    if not await supervisor_client.is_supervisor_ready():
        return _busy_response("backend_unavailable", "supervisor backend is not ready")

    user_input = messages_to_user_input(request.messages)
    session_id = x_session_id or f"openai-{user_id}"
    job_id = str(uuid.uuid4())

    # --- Phase 3.5 FIX: check request.stream BEFORE any LLM call ---
    # A streaming request makes exactly ONE agentic invocation (process_stream).
    # Only on failure does it fall back to non-streaming process() + pseudo-stream.
    # This prevents the double-invocation that was 2x-ing token cost + latency.
    if request.stream:
        try:
            resp = await supervisor_client.process_stream(
                user_input=user_input,
                user_id=user_id,
                session_id=session_id,
                force_agent=force_agent,
            )

            async def _proxy_sse() -> Any:
                """Proxy supervisor SSE body verbatim to the client.

                The supervisor already emits properly-framed SSE (data: ...\n\n).
                We forward raw bytes as-is to preserve valid SSE framing.
                """
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await resp.aclose()

            return StreamingResponse(
                _proxy_sse(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except (SupervisorUnavailableError, Exception):
            # Streaming endpoint unavailable — fall back to non-streaming
            # process() + pseudo-stream (single invocation fallback).
            pass

        # Fallback: non-streaming call + pseudo-stream to the client
        try:
            fallback_result = await supervisor_client.process(
                user_input=user_input,
                user_id=user_id,
                session_id=session_id,
                force_agent=force_agent,
            )
        except SupervisorUnavailableError:
            return _busy_response("backend_unavailable", "supervisor backend unavailable")
        except Exception as exc:
            return _forward_error(exc)

        return StreamingResponse(
            sse_stream(fallback_result, request.model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- Non-streaming path: existing _one_shot + worker-pool logic unchanged ---
    async def _one_shot() -> Any:
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

    return build_completion(result if isinstance(result, dict) else {}, request.model).model_dump()


@app.post("/jobs/{job_id}/cancel", status_code=202, dependencies=[Depends(require_edge_auth)])  # type: ignore[untyped-decorator]
async def cancel_job(job_id: str) -> Any:
    """Signal cancellation; the worker stops within ~one poll interval."""
    cancelled = await worker_pool.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="job not found or not running")
    return {"job_id": job_id, "status": "cancelling"}


@app.get("/healthz")  # type: ignore[untyped-decorator]
async def healthz() -> dict[str, str]:
    """Liveness — never checks external deps."""
    return {"status": "ok", "service": "gateway"}


@app.get("/ready")  # type: ignore[untyped-decorator]
async def ready() -> Any:
    """Readiness — Redis reachable + pool functional.

    Intentionally does NOT check the supervisor (round-table): coupling would
    turn a supervisor outage into a gateway-removed-from-LB cascade. Supervisor
    availability is handled per-request via preflight → 503.
    """
    checks: dict[str, dict[str, bool | str]] = {"pool": {"ok": worker_pool.has_capacity() or worker_pool.active_count >= 0}}
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


@app.get("/health", include_in_schema=False)  # type: ignore[untyped-decorator]
async def health_legacy() -> dict[str, str]:
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


def _forward_error(exc: Exception) -> JSONResponse:
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
