from fastapi import FastAPI, Request, HTTPException
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from src.supervisor.agent import SupervisorAgent
from src.core.config import settings
from src.core.state_store import StateStore
from langchain_core.messages import HumanMessage
import uvicorn

app = FastAPI(title="Agent Squad API")

slack_client = WebClient(token=settings.slack_bot_token)
signature_verifier = SignatureVerifier(settings.slack_signing_secret)
supervisor = SupervisorAgent()
state_store = StateStore()

@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack events"""
    
    # Verify Slack signature
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    
    if not signature_verifier.is_valid(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")
    
    data = await request.json()
    
    # Handle URL verification
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}
    
    # Handle app_mention events
    if data.get("event", {}).get("type") == "app_mention":
        event = data["event"]
        user_id = event["user"]
        channel_id = event["channel"]
        text = event["text"]
        thread_ts = event.get("thread_ts", event["ts"])
        
        # Generate session_id from thread
        session_id = f"{channel_id}:{thread_ts}"
        
        # Get or create session state
        session_state = state_store.get_session(session_id) or {
            "messages": [],
            "session_id": session_id,
            "user_id": user_id
        }
        
        # Add user message
        session_state["messages"].append(HumanMessage(content=text))
        
        # Process with supervisor
        result = supervisor.graph.invoke(session_state)
        
        # Save updated state
        state_store.save_session(session_id, result)
        
        # Send response to Slack
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=result["final_response"]
        )
    
    return {"ok": True}

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
