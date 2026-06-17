"""Slack webhook handler for Agent Squad."""
import os
from fastapi import FastAPI, Request, HTTPException
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from src.supervisor.agent import supervisor
import uvicorn

app = FastAPI(title="Agent Squad Slack API")

slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
signature_verifier = SignatureVerifier(os.getenv("SLACK_SIGNING_SECRET", ""))


@app.post("/slack/events")
async def slack_events(request: Request):
    """Handle Slack events (app_mention)."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not signature_verifier.is_valid(body, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()

    # URL verification challenge
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}

    # Handle app_mention events
    event = data.get("event", {})
    if event.get("type") == "app_mention":
        user_id = event["user"]
        channel_id = event["channel"]
        text = event["text"]
        thread_ts = event.get("thread_ts", event["ts"])
        session_id = f"{channel_id}:{thread_ts}"

        result = await supervisor.process_request(
            user_input=text,
            user_id=user_id,
            session_id=session_id,
        )

        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=result.get("response", "No response"),
        )

    return {"ok": True}


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "slack-api"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
