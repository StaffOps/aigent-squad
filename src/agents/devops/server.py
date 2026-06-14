from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from src.agents.devops.agent import DevOpsAgent
from src.core.logger import logger
import uvicorn

HTTPXClientInstrumentor().instrument()

app = FastAPI(title="DevOps Agent Service")
agent = DevOpsAgent()

FastAPIInstrumentor.instrument_app(app)

tracer = trace.get_tracer(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: str


class ProcessRequest(BaseModel):
    input_text: str
    user_id: str
    session_id: str
    chat_history: List[ChatMessage] = []
    additional_params: Optional[dict] = None


@app.post("/process")
async def process(request: ProcessRequest):
    """Process DevOps-related query with conversation history"""
    with tracer.start_as_current_span("devops_server.process") as span:
        span.set_attribute("user_id", request.user_id)
        span.set_attribute("session_id", request.session_id)
        try:
            from src.core.state_store import ConversationMessage
            history = [
                ConversationMessage(
                    role=msg.role, content=msg.content,
                    timestamp=msg.timestamp, agent_id="devops"
                )
                for msg in request.chat_history
            ]
            response = await agent.process_request(
                input_text=request.input_text,
                user_id=request.user_id,
                session_id=request.session_id,
                chat_history=history,
                additional_params=request.additional_params,
            )
            return {
                "role": response.role,
                "content": response.content,
                "timestamp": response.timestamp,
                "agent_id": response.agent_id,
            }
        except ValueError as e:
            logger.warning("Validation error", extra={"error": str(e)})
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Server error", extra={"error": str(e)}, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health():
    return {"status": "healthy", "agent": "devops"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8004)
