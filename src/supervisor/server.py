from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from otel_helper import setup_telemetry
from src.core.auth import require_token
from src.core.kb.store import kb_store
from src.supervisor.agent import supervisor
from src.supervisor.alert_handler import AlertmanagerPayload, handle_alert_payload
from src.supervisor.slack_notifier import post_rca_to_slack
import uvicorn

setup_telemetry()


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
