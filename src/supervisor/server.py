from otel_helper import metrics_app, setup_telemetry

# CRITICAL: setup_telemetry MUST run before any project import that creates
# tracers/meters at module load time (e.g., src.core.metrics).
# Otherwise those modules capture ProxyTracer/ProxyMeter and never export.
setup_telemetry()

from contextlib import asynccontextmanager
from typing import Optional
import boto3
import redis as redis_lib
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from src.core.auth import require_token
from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.health import DependencyChecker
from src.core.internal_auth import require_internal_token
from src.core.logger import logger
from src.core.kb.store import kb_store

from src.supervisor.agent import supervisor
from src.supervisor.alert_handler import AlertmanagerPayload, handle_alert_payload
from src.supervisor.openai_compat import sse_stream_agentic
from src.supervisor.slack_notifier import post_rca_to_slack
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
    # HC5 (spec 38 FU-B): fail loud at BOOT if tier model IDs are misconfigured —
    # here in the lifespan, not as an agent.py import side-effect (cleaner testing + import order).
    from src.core.model_tier import validate_tier_models_at_startup
    validate_tier_models_at_startup()
    await kb_store.connect()
    yield
    await kb_store.close()
    await supervisor.close()


app = FastAPI(title="Supervisor Service", lifespan=lifespan)

# Prometheus scrape endpoint — see src/gateway/main.py's identical mount for
# the full rationale (OTLP+Prometheus dual export, why it's unauthenticated,
# and the known bare-"/metrics"-307s-to-"/metrics/" Starlette Mount quirk).
# Deliberate exception to the /internal/*-only trust boundary (AGENTS.md
# invariant #10) — infra-level scrape target, same class as /healthz+/ready.
app.mount("/metrics", metrics_app())


class QueryRequest(BaseModel):
    user_input: str
    user_id: str
    session_id: str
    mode: Optional[str] = None
    force_agent: Optional[str] = None


@app.post("/internal/process", dependencies=[Depends(require_internal_token)])
async def internal_process(request: QueryRequest):
    """Gateway-only orchestration entrypoint (spec 31).

    The edge gateway forwards here after admission control (auth, rate/budget,
    worker pool). This is a thin wrapper over the existing ``process_request`` —
    the supervisor's orchestration is unchanged. The guardrail (spec 14) runs
    inside ``process_request`` on every call, regardless of caller, so the
    injection-defense trust boundary stays here, not at the gateway.
    """
    try:
        return await supervisor.process_request(
            user_input=request.user_input,
            user_id=request.user_id,
            session_id=request.session_id,
            mode=request.mode or "query",
            force_agent=request.force_agent,
        )
    except GuardrailBlockedError as e:
        raise HTTPException(status_code=403, detail="Request blocked by security guardrail") from e
    except Exception:
        logger.exception("Unhandled error in supervisor request handler")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/internal/agents", dependencies=[Depends(require_internal_token)])
async def internal_agents():
    """Agent names for the gateway's OpenAI /v1/models listing (spec 31)."""
    return {"agents": supervisor.registry.agent_names()}


@app.post("/internal/process/stream", dependencies=[Depends(require_internal_token)])
async def internal_process_stream(request: QueryRequest):
    """Streaming process endpoint (Phase 3.5, S1).

    Returns an SSE stream of OpenAI chat.completion.chunk frames as the agentic
    loop executes — reasoning, tool calls, results, and final answer stream live.
    Falls back to pseudo-streaming (full answer as one delta) for non-agentic paths
    (classifier, investigation, fan-out).
    """
    try:
        step_gen = await supervisor.process_request_streaming(
            user_input=request.user_input,
            user_id=request.user_id,
            session_id=request.session_id,
            mode=request.mode or "query",
            force_agent=request.force_agent,
        )
    except GuardrailBlockedError:
        raise HTTPException(status_code=403, detail="Request blocked by security guardrail")
    except Exception:
        logger.exception("Unhandled error in supervisor streaming handler")
        raise HTTPException(status_code=500, detail="Internal server error")

    if step_gen is None:
        # Non-agentic path returned None — fall back to non-streaming
        try:
            result = await supervisor.process_request(
                user_input=request.user_input,
                user_id=request.user_id,
                session_id=request.session_id,
                mode=request.mode or "query",
                force_agent=request.force_agent,
            )
        except GuardrailBlockedError:
            raise HTTPException(status_code=403, detail="Request blocked by security guardrail")
        except Exception:
            logger.exception("Unhandled error in supervisor streaming fallback")
            raise HTTPException(status_code=500, detail="Internal server error")

        from src.supervisor.openai_compat import sse_stream
        return StreamingResponse(
            sse_stream(result, "aigent-squad"),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Real streaming from the agentic loop
    return StreamingResponse(
        sse_stream_agentic(step_gen, "aigent-squad"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    async def _run_inv(symptom: str, agents=None, fingerprint: str = ""):
        from src.supervisor.investigation import run_investigation
        from src.supervisor.agent import _resolve_tier_model
        from src.core.classifier import ClassifierResult, AgentMatch
        # Finding (2026-07-14 review, E2 follow-up): session_id="" made
        # bedrock.py's `charged_session_id = budget_session_id or session_id`
        # fall through to "" (falsy), so record_usage() was never called at
        # all — alert-triggered investigations spent Bedrock tokens with NO
        # budget cap. Give each unique alert (deduped by fingerprint) its own
        # stable budget bucket instead.
        # Tier routing (spec 38, agentic28): alert-triggered RCA is inherently
        # complex multi-signal work → route through the deep tier like the
        # user-initiated investigation path (confidence cosmetic; complexity drives it).
        inv_cr = ClassifierResult(
            agents=[AgentMatch(agent="investigation", confidence=0.9)],
            reasoning="alertmanager-triggered investigation",
            complexity="complex",
        )
        tier_model_id = _resolve_tier_model(inv_cr)
        return await run_investigation(
            symptom=symptom,
            agents=supervisor.agents,
            user_id="alertmanager",
            session_id=f"alertmanager-{fingerprint}" if fingerprint else "alertmanager-unknown",
            model_id_override=tier_model_id,
        )

    result = await handle_alert_payload(
        payload,
        run_investigation_fn=_run_inv,
        slack_post_fn=post_rca_to_slack,
    )
    return {"ok": True, **result}


# NOTE: the public `/query` and OpenAI `/v1/*` routes moved to the edge gateway
# (spec 31, `src/gateway/`). The supervisor's public surface is now the
# gateway-only `/internal/process` + health + management (kb/alerts). The
# OpenAI shaping lives in `src/supervisor/openai_compat.py` and is imported by
# the gateway, not here.


if __name__ == "__main__":
    # Supervisor is a backend service behind the gateway → port 8001.
    uvicorn.run(app, host="0.0.0.0", port=8001)  # nosec B104 — containerized service must bind all interfaces
