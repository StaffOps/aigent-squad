from typing import Dict
import httpx
import time
from datetime import datetime
from opentelemetry import trace
from src.core.classifier import classifier, ClassifierResult
from src.core.state_store import storage, ConversationMessage
from src.core.logger import logger, log_request, log_response, log_error

tracer = trace.get_tracer(__name__)

class SupervisorAgent:
    """Supervisor that routes to specialist agents via HTTP using intelligent classifier"""
    
    # Microservices endpoints
    AGENT_URLS = {
        "aws": "http://aws-agent:8001/process",
        "kubernetes": "http://kubernetes-agent:8002/process",
        "finops": "http://finops-agent:8003/process",
        "devops": "http://devops-agent:8004/process",
        "observability": "http://observability-agent:8005/process",
    }
    
    def __init__(self):
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0, read=25.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            transport=httpx.AsyncHTTPTransport(retries=2)
        )
    
    async def process_request(
        self,
        user_input: str,
        user_id: str,
        session_id: str
    ) -> Dict:
        """Process user request with intelligent routing"""
        
        start_time = time.time()
        
        with tracer.start_as_current_span("supervisor.process_request") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            span.set_attribute("input_length", len(user_input))
            
            log_request("supervisor", user_id, session_id, user_input)
            
            try:
                # 1. Get global conversation history for classifier
                chat_history = await storage.fetch_all_chats(user_id, session_id)
                
                # 2. Classify intent
                with tracer.start_as_current_span("classifier.classify"):
                    classification: ClassifierResult = await classifier.classify(
                        user_input,
                        chat_history
                    )
                
                logger.info("Intent classified", extra={
                    "selected_agent": classification.selected_agent,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning
                })
                
                if classification.selected_agent == "unknown":
                    return {
                        "agent": "supervisor",
                        "response": "I'm not sure how to help with that. Could you please rephrase your question?",
                        "confidence": classification.confidence
                    }
                
                # 3. Save user message
                user_message = ConversationMessage(
                    role="user",
                    content=user_input,
                    timestamp=datetime.utcnow().isoformat(),
                    agent_id=classification.selected_agent
                )
                await storage.save_chat_message(
                    user_id,
                    session_id,
                    classification.selected_agent,
                    user_message
                )
                
                # 4. Get agent-specific history
                agent_history = await storage.fetch_chat(
                    user_id,
                    session_id,
                    classification.selected_agent
                )
                
                # 5. Call specialist agent
                agent_url = self.AGENT_URLS[classification.selected_agent]
                
                with tracer.start_as_current_span("agent.http_call") as agent_span:
                    agent_span.set_attribute("agent_id", classification.selected_agent)
                    agent_span.set_attribute("agent_url", agent_url)
                    
                    try:
                        response = await self.http_client.post(
                            agent_url,
                            json={
                                "input_text": user_input,
                                "user_id": user_id,
                                "session_id": session_id,
                                "chat_history": [
                                    {"role": msg.role, "content": msg.content, "timestamp": msg.timestamp}
                                    for msg in agent_history
                                ]
                            },
                            timeout=25.0
                        )
                        response.raise_for_status()
                        
                    except httpx.TimeoutException as e:
                        logger.error("Agent timeout", extra={
                            "agent_id": classification.selected_agent,
                            "timeout": 25.0
                        })
                        return {
                            "agent": classification.selected_agent,
                            "response": f"Request timeout. The {classification.selected_agent} agent took too long to respond.",
                            "confidence": 0.0,
                            "error": "timeout"
                        }
                    
                    except httpx.HTTPStatusError as e:
                        logger.error("Agent HTTP error", extra={
                            "agent_id": classification.selected_agent,
                            "status_code": e.response.status_code
                        })
                        return {
                            "agent": classification.selected_agent,
                            "response": f"Error communicating with {classification.selected_agent} agent.",
                            "confidence": 0.0,
                            "error": f"http_{e.response.status_code}"
                        }
                
                agent_response = response.json()
                response_text = agent_response["content"]
                
                # 6. Save assistant message
                assistant_message = ConversationMessage(
                    role="assistant",
                    content=response_text,
                    timestamp=datetime.utcnow().isoformat(),
                    agent_id=classification.selected_agent
                )
                await storage.save_chat_message(
                    user_id,
                    session_id,
                    classification.selected_agent,
                    assistant_message
                )
                
                duration_ms = (time.time() - start_time) * 1000
                log_response("supervisor", user_id, session_id, len(response_text), duration_ms)
                
                return {
                    "agent": classification.selected_agent,
                    "response": response_text,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning
                }
                
            except Exception as e:
                log_error("supervisor", e, user_id=user_id, session_id=session_id)
                return {
                    "agent": "supervisor",
                    "response": "An unexpected error occurred. Please try again.",
                    "confidence": 0.0,
                    "error": str(e)
                }
    
    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()

# Singleton
supervisor = SupervisorAgent()
