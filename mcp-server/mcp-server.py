#!/usr/bin/env python3
"""
MCP Server for Agent Squad
Exposes Agent Squad Supervisor as HTTP API for Kiro CLI
"""
import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Agent Squad MCP Server")

# HTTP client for Supervisor
SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor:8000/query")
_INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

class QueryRequest(BaseModel):
    question: str
    user_id: str = "kiro-user"

class QueryResponse(BaseModel):
    agent: str
    response: str
    confidence: float
    reasoning: str = ""

@app.get("/health")
async def health():
    """Health check"""
    return {"status": "healthy"}

@app.post("/query", response_model=QueryResponse)
async def query_agent_squad(request: QueryRequest):
    """Query Agent Squad"""
    
    session_id = f"kiro-{request.user_id}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                SUPERVISOR_URL,
                headers={"X-Internal-Token": _INTERNAL_TOKEN},
                json={
                    "user_input": request.question,
                    "user_id": request.user_id,
                    "session_id": session_id
                }
            )
            response.raise_for_status()
            
            result = response.json()
            
            return QueryResponse(
                agent=result.get("agent", "unknown"),
                response=result.get("response", "No response"),
                confidence=result.get("confidence", 0.0),
                reasoning=result.get("reasoning", "")
            )
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Supervisor error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8006)
