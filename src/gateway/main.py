from otel_helper import metrics_app, setup_telemetry

# CRITICAL (spec 31 / round-table): setup_telemetry MUST run before any project
# import that creates tracers/meters at module load (e.g. src.core.metrics).
# Otherwise those modules capture ProxyMeter and never export.
setup_telemetry()

import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Any, AsyncGenerator, Optional  # noqa: E402

import redis.asyncio as aioredis  # noqa: E402
from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import uvicorn  # noqa: E402

from src.core.config import reveal, settings  # noqa: E402
from src.core.metrics import gateway_input_rejections, gateway_timeouts  # noqa: E402
from src.core.rate_limiter import AdmissionGuard, estimate_cost  # noqa: E402
from src.gateway import __version__  # noqa: E402
from src.gateway.auth import AuthResult, get_key_agent_map, require_edge_auth, scoped_user_id  # noqa: E402
from src.gateway.feedback import (  # noqa: E402
    RESPONSE_ID_HEADER,
    FeedbackStore,
    agent_of,
    create_feedback_router,
    new_completion_id,
    new_query_response_id,
)
from src.gateway.supervisor_client import (  # noqa: E402
    SupervisorClient,
    SupervisorUnavailableError,
)
from src.gateway.worker_pool import (  # noqa: E402
    GATEWAY_TIMEOUTS,
    JobTimeout,
    PoolFullError,
    WorkerPool,
    stream_with_deadline,
)
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
        password=reveal(settings.redis_password) or None,
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
    job_record_ttl=settings.gateway_job_record_ttl_seconds,
)
# Global admission guards (rate + budget), Redis-coordinated, fail-open.
admission = AdmissionGuard(
    redis_client=_redis,
    rate_per_minute=settings.rate_limit_per_minute,
    daily_budget_usd=settings.daily_budget_usd,
)

# Response-id ownership records + POST /feedback (spec 47). Fail-open on record.
feedback_store = FeedbackStore(_redis)

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
# Resolved per request so tests (and a future store swap) can replace the global.
app.include_router(create_feedback_router(lambda: feedback_store))


def _too_large(reason: str, message: str) -> JSONResponse:
    """413 for an oversized request (F-009), OpenAI-shaped like every gateway error."""
    gateway_input_rejections.add(1, {"reason": reason})
    return JSONResponse(
        status_code=413,
        content={"error": {"type": "invalid_request_error", "code": "request_too_large", "message": message}},
    )


def _timeout_response() -> JSONResponse:
    """504 when a job hits a gateway timeout (first-byte, idle or overall job)."""
    return JSONResponse(
        status_code=504,
        content={"error": {"type": "gateway_timeout", "message": "the request took too long and was cancelled"}},
    )


class _BodySizeCap:
    """Pure ASGI middleware: cap the request body at GATEWAY_MAX_BODY_BYTES (F-009).

    A declared Content-Length over the cap is rejected before the body is read.
    A body without one (chunked) is counted as it is received; once it passes the
    cap, the app's own response is discarded and a 413 is sent instead. Pure ASGI
    (not BaseHTTPMiddleware) so streamed SSE responses pass through untouched.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = settings.gateway_max_body_bytes
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                await _too_large("body_bytes", "request body exceeds the gateway size limit")(scope, receive, send)
                return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Any:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLargeError()
            return message

        async def guarded_send(message: Any) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                return  # drop the app's error response; the 413 below replaces it
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLargeError:
            pass
        if exceeded and not response_started:
            await _too_large("body_bytes", "request body exceeds the gateway size limit")(scope, receive, send)


class _BodyTooLargeError(Exception):
    """Raised from receive() when a streamed body passes the cap."""


app.add_middleware(_BodySizeCap)


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


@app.post("/query")  # type: ignore[untyped-decorator]
async def query(request: QueryRequest, auth: AuthResult = Depends(require_edge_auth)) -> Any:
    """Native entrypoint — admission control, then forward to the supervisor."""
    # F-024: scope the client-supplied user to the authenticated consumer, so history,
    # rate limits and sessions never cross API keys.
    if len(request.user_input) > settings.gateway_max_input_chars:
        return _too_large("input_chars", "user_input exceeds the gateway character limit")
    user_id = scoped_user_id(auth, request.user_id)
    denied = await _check_admission(user_id)
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
            user_id=user_id,
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
    except GATEWAY_TIMEOUTS:
        return _timeout_response()
    except SupervisorUnavailableError:
        return _busy_response("backend_unavailable", "supervisor backend unavailable")
    except Exception as exc:
        return _forward_error(exc)
    if not isinstance(result, dict):
        return result
    # Spec 47: mint the rateable response id (additive field + header).
    response_id = new_query_response_id()
    await feedback_store.record_response(
        response_id, auth, user_id, request.session_id, agent_of(result)
    )
    return JSONResponse(
        content={**result, "response_id": response_id},
        headers={RESPONSE_ID_HEADER: response_id},
    )


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
    # F-009: size caps before any network call, admission or LLM work. The char
    # cap applies to the turn actually forwarded (the last user message, the same
    # text the supervisor InputScanner checks); history size is bounded by the
    # message count and the body-size cap.
    if len(request.messages) > settings.gateway_max_messages:
        return _too_large("messages", "messages exceeds the gateway message-count limit")
    user_input = messages_to_user_input(request.messages)
    if len(user_input) > settings.gateway_max_input_chars:
        return _too_large("input_chars", "user message exceeds the gateway character limit")

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

    # F-024: scoped to the authenticated consumer; an anonymous OpenAI client ("user"
    # unset) gets a per-key default instead of one session shared by every key.
    user_id = scoped_user_id(auth, request.user or "librechat")
    denied = await _check_admission(user_id, max_tokens=request.max_tokens or 4096)
    if denied is not None:
        return denied
    if not worker_pool.has_capacity():
        return _busy_response("service_overloaded", "server busy — worker pool at capacity")
    if not await supervisor_client.is_supervisor_ready():
        return _busy_response("backend_unavailable", "supervisor backend is not ready")

    session_id = x_session_id or f"openai-{user_id}"
    job_id = str(uuid.uuid4())
    # Spec 47: the rateable id — the OpenAI ``id`` of this answer + a header.
    response_id = new_completion_id()

    # --- Phase 3.5 FIX: check request.stream BEFORE any LLM call ---
    # A streaming request makes exactly ONE agentic invocation (process_stream).
    # Only on failure does it fall back to non-streaming process() + pseudo-stream.
    # This prevents the double-invocation that was 2x-ing token cost + latency.
    if request.stream:
        # The SSE path does not go through the pool, so the overall job budget
        # (GATEWAY_JOB_TIMEOUT_SECONDS) is enforced here across the whole request:
        # opening the stream, proxying it, and the non-streaming fallback (F-009 C1).
        job_deadline = time.monotonic() + settings.gateway_job_timeout_seconds

        def _remaining() -> float:
            return max(job_deadline - time.monotonic(), 0.0)

        try:
            resp = await asyncio.wait_for(
                supervisor_client.process_stream(
                    user_input=user_input,
                    user_id=user_id,
                    session_id=session_id,
                    force_agent=force_agent,
                ),
                timeout=_remaining(),
            )

            async def _proxy_sse() -> Any:
                """Proxy supervisor SSE body verbatim to the client.

                The supervisor already emits properly-framed SSE (data: ...\n\n).
                We forward raw bytes as-is to preserve valid SSE framing. Headers
                are already sent, so a job timeout ends the stream with an SSE
                error event instead of a 504.
                """
                at_frame_boundary = True
                try:
                    async for chunk in stream_with_deadline(resp.aiter_bytes(), _remaining()):
                        at_frame_boundary = chunk.endswith(b"\n\n")
                        yield chunk
                except JobTimeout:
                    error = {"error": {"type": "gateway_timeout", "message": "the request took too long and was cancelled"}}
                    # Chunk boundaries are arbitrary; close a half-sent frame first.
                    prefix = b"" if at_frame_boundary else b"\n\n"
                    yield prefix + f"data: {json.dumps(error)}\n\n".encode()
                finally:
                    await resp.aclose()

            # The supervisor mints its own chunk ids on this path; the header carries
            # the rateable id and the agent is not known here (spec 47 Decision 1).
            await feedback_store.record_response(
                response_id, auth, user_id, session_id, force_agent or "auto"
            )
            return StreamingResponse(
                _proxy_sse(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                         RESPONSE_ID_HEADER: response_id},
            )
        except asyncio.TimeoutError:
            gateway_timeouts.add(1, {"kind": "job"})
            return _timeout_response()
        except (SupervisorUnavailableError, Exception):
            # Streaming endpoint unavailable — fall back to non-streaming
            # process() + pseudo-stream (single invocation fallback).
            pass

        # Fallback: non-streaming call + pseudo-stream to the client
        try:
            fallback_result = await asyncio.wait_for(
                supervisor_client.process(
                    user_input=user_input,
                    user_id=user_id,
                    session_id=session_id,
                    force_agent=force_agent,
                ),
                timeout=_remaining(),
            )
        except asyncio.TimeoutError:
            gateway_timeouts.add(1, {"kind": "job"})
            return _timeout_response()
        except SupervisorUnavailableError:
            return _busy_response("backend_unavailable", "supervisor backend unavailable")
        except Exception as exc:
            return _forward_error(exc)

        fallback_dict: dict[str, Any] = fallback_result if isinstance(fallback_result, dict) else {}
        await feedback_store.record_response(
            response_id, auth, user_id, session_id, agent_of(fallback_dict)
        )
        return StreamingResponse(
            sse_stream(fallback_result, request.model, completion_id=response_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     RESPONSE_ID_HEADER: response_id},
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
    except GATEWAY_TIMEOUTS:
        return _timeout_response()
    except SupervisorUnavailableError:
        return _busy_response("backend_unavailable", "supervisor backend unavailable")
    except Exception as exc:
        return _forward_error(exc)

    result_dict: dict[str, Any] = result if isinstance(result, dict) else {}
    await feedback_store.record_response(
        response_id, auth, user_id, session_id, agent_of(result_dict)
    )
    completion = build_completion(result_dict, request.model)
    completion.id = response_id
    return JSONResponse(content=completion.model_dump(), headers={RESPONSE_ID_HEADER: response_id})


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
