from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from otel_helper import setup_telemetry
from src.core.auth import require_token
from src.supervisor.agent import supervisor
import uvicorn

setup_telemetry()


@asynccontextmanager
async def lifespan(app):
    # startup
    yield
    # shutdown: flush OTel, close connections
    await supervisor.close()


app = FastAPI(title="Supervisor Service", lifespan=lifespan)


class QueryRequest(BaseModel):
    user_input: str
    user_id: str
    session_id: str


@app.post("/query", dependencies=[Depends(require_token)])
async def query(request: QueryRequest):
    """Process user query with intelligent routing"""
    try:
        response = await supervisor.process_request(
            user_input=request.user_input,
            user_id=request.user_id,
            session_id=request.session_id
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "supervisor"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
