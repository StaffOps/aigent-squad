from otel_helper import setup_telemetry

# CRITICAL: setup_telemetry MUST run before any project import that creates
# tracers/meters at module load time (e.g., src.core.metrics).
# Otherwise those modules capture ProxyTracer/ProxyMeter and never export.
setup_telemetry()

from contextlib import asynccontextmanager
from typing import Optional
import boto3
import redis as redis_lib
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from src.core.auth import require_token
from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.health import DependencyChecker
from src.core.kb.store import kb_store

from src.supervisor.agent import supervisor
from src.supervisor.alert_handler import AlertmanagerPayload, handle_alert_payload
from src.supervisor.slack_notifier import post_rca_to_slack
from src.supervisor.openai_compat import (
    ChatCompletionRequest,
    UnknownModelError,
    build_completion,
    list_models,
    messages_to_user_input,
    resolve_target,
    sse_stream,
)
import uvicorn

_checker = DependencyChecker(cache_ttl=5.0, timeout=2.0)

# Singletons used only for health checks — not the main clients
_health_redis = redis_lib.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    ssl=settings.redis_ssl,
    password=settings.redis_password,
    socket_connect_timeout=2,
)
_health_dynamodb_table = boto3.resource(
    "dynamodb",
    region_name=settings.aws_region,
    endpoint_url=settings.dynamodb_endpoint,
).Table(settings.dynamodb_sessions_table)


@asynccontextmanager
async def lifespan(app):
    await kb_store.connect()
    yield
    await kb_store.close()
    await supervisor.close()


app = FastAPI(title="Supervisor Service", lifespan=lifespan)


class QueryRequest(BaseModel):
    user_input: str
    user_id: str
    session_id: str
    mode: Optional[str] = None


@app.post("/query", dependencies=[Depends(require_token)])
async def query(request: QueryRequest):
    """Process user query with intelligent routing"""
    try:
        response = await supervisor.process_request(
            user_input=request.user_input,
            user_id=request.user_id,
            session_id=request.session_id,
            mode=request.mode or "query",
        )
        return response
    except GuardrailBlockedError as e:
        # Fail-closed (spec 14): security guardrail refused the request.
        # 403 — not 500 — so the caller knows this was a policy decision.
        raise HTTPException(status_code=403, detail="Request blocked by security guardrail") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/healthz")
async def healthz():
    """Liveness probe — process is alive. Never checks external deps."""
    return {"status": "ok", "service": "supervisor"}


@app.get("/ready")
async def ready():
    """Readiness probe — checks Redis, DynamoDB, and ≥1 in-process agent loaded."""
    results = {}

    redis_result = await _checker.check_redis(_health_redis)
    results["redis"] = {"ok": redis_result.ok, "detail": redis_result.detail}

    dynamo_result = await _checker.check_dynamodb(_health_dynamodb_table)
    results["dynamodb"] = {"ok": dynamo_result.ok, "detail": dynamo_result.detail}

    agents_ok = len(supervisor.agents) > 0
    results["agents"] = {"ok": agents_ok, "detail": f"{len(supervisor.agents)} agent(s) loaded"}

    all_ok = redis_result.ok and dynamo_result.ok and agents_ok
    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if all_ok else "not_ready", "checks": results},
    )


@app.get("/health")
async def health():
    """Legacy alias for /healthz — kept for backwards compatibility."""
    return {"status": "healthy", "service": "supervisor"}


@app.get("/kb/pending", dependencies=[Depends(require_token)])
async def list_kb_pending():
    items = await kb_store.list_pending_review()
    return {"items": [{"id": i.id, "type": i.type, "title": i.title, "content": i.content, "confidence": i.confidence_score} for i in items]}


@app.post("/kb/{item_id}/approve", dependencies=[Depends(require_token)])
async def approve_kb_item(item_id: str):
    ok = await kb_store.update_status(item_id, "active")
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found or update failed")
    return {"ok": True, "id": item_id, "status": "active"}


@app.post("/kb/{item_id}/reject", dependencies=[Depends(require_token)])
async def reject_kb_item(item_id: str):
    ok = await kb_store.update_status(item_id, "rejected")
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found or update failed")
    return {"ok": True, "id": item_id, "status": "rejected"}


@app.post("/alerts/incoming", dependencies=[Depends(require_token)])
async def alerts_incoming(payload: AlertmanagerPayload):
    """Receive Alertmanager webhook (v2). Triggers investigation per unique firing alert."""
    async def _run_inv(symptom: str, agents=None):
        from src.supervisor.investigation import run_investigation
        return await run_investigation(
            symptom=symptom,
            agents=supervisor.agents,
            user_id="alertmanager",
            session_id="",
        )

    result = await handle_alert_payload(
        payload,
        run_investigation_fn=_run_inv,
        slack_post_fn=post_rca_to_slack,
    )
    return {"ok": True, **result}


# ─── OpenAI-compatible bridge (/v1/*) — spec 29 ─────────────────────
# Lets LibreChat (or any OpenAI client) consume the squad directly.


@app.get("/v1/models", dependencies=[Depends(require_token)])
async def openai_list_models():
    return list_models(supervisor.registry.agent_names()).model_dump()


@app.post("/v1/chat/completions", dependencies=[Depends(require_token)])
async def openai_chat_completions(
    request: ChatCompletionRequest,
    x_session_id: str = Header(default=""),
):
    """OpenAI Chat Completions → supervisor. Auto-routes or forces an agent."""
    try:
        force_agent = resolve_target(request.model, supervisor.registry.agent_names())
    except UnknownModelError as e:
        return JSONResponse(
            status_code=404,
            content={"error": {"type": "invalid_request_error", "message": str(e)}},
        )

    user_input = messages_to_user_input(request.messages)
    user_id = request.user or "librechat"
    session_id = x_session_id or f"openai-{user_id}"

    try:
        result = await supervisor.process_request(
            user_input=user_input,
            user_id=user_id,
            session_id=session_id,
            force_agent=force_agent,
        )
    except GuardrailBlockedError:
        # Fail-closed (spec 14) — OpenAI-shaped 403 so LibreChat surfaces it.
        return JSONResponse(
            status_code=403,
            content={"error": {"type": "guardrail_blocked", "message": "Request blocked by security guardrail"}},
        )

    if request.stream:
        return StreamingResponse(
            sse_stream(result, request.model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return build_completion(result, request.model).model_dump()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104 — containerized service must bind all interfaces
