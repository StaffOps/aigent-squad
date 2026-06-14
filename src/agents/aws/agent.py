import boto3
import time
from datetime import datetime, timezone
from typing import List, Optional
from opentelemetry import trace
from src.core.agent_base import Agent
from src.core.state_store import ConversationMessage
from src.core.cache import cache
from src.core.bedrock import bedrock
from src.core.logger import logger, log_request, log_response, log_error

tracer = trace.get_tracer(__name__)

class AWSAgent(Agent):
    """AWS specialist agent"""
    
    def __init__(self):
        super().__init__(
            agent_id="aws",
            name="AWS Agent",
            description="Specializes in AWS resources (EC2, S3, RDS, Lambda, VPC, IAM)"
        )
        self.ec2 = boto3.client('ec2')
        self.ce = boto3.client('ce')
        self.system_prompt = self._load_prompt()
    
    async def process_request(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        chat_history: List[ConversationMessage],
        additional_params: Optional[dict] = None
    ) -> ConversationMessage:
        """Process AWS-related query with conversation history"""
        
        start_time = time.time()
        
        with tracer.start_as_current_span("aws_agent.process_request") as span:
            span.set_attribute("agent_id", self.id)
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            
            log_request(self.id, user_id, session_id, input_text)
            
            try:
                # Validate input
                if not input_text or not input_text.strip():
                    raise ValueError("Input text cannot be empty")
                
                if len(input_text) > 10000:
                    raise ValueError("Input text too long (max 10000 characters)")
                
                # Get AWS inventory
                with tracer.start_as_current_span("aws_agent.get_inventory"):
                    inventory = self._get_inventory()
                
                # Build context with history
                history_context = self._format_history(chat_history)
                context = f"""AWS Inventory:
{inventory}

Conversation History:
{history_context}

Current Query: {input_text}"""
                
                # Call Bedrock
                with tracer.start_as_current_span("aws_agent.bedrock_invoke"):
                    response = bedrock.invoke(
                        messages=[{"role": "user", "content": context}],
                        system_prompt=self.system_prompt,
                        use_cache=True
                    )
                
                duration_ms = (time.time() - start_time) * 1000
                log_response(self.id, user_id, session_id, len(response), duration_ms)
                
                return ConversationMessage(
                    role="assistant",
                    content=response,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_id=self.id
                )
                
            except Exception as e:
                log_error(self.id, e, user_id=user_id, session_id=session_id)
                raise
    
    def _get_inventory(self) -> str:
        """Get AWS inventory with caching"""
        cached = cache.get("inventory", namespace="aws")
        if cached:
            return cached
        
        try:
            instances = self.ec2.describe_instances()
            inventory = {
                "ec2_count": sum(len(r['Instances']) for r in instances['Reservations']),
                "regions": [self.ec2.meta.region_name]
            }
            result = str(inventory)
            cache.set("inventory", result, ttl=300, namespace="aws")
            return result
        except Exception as e:
            logger.error("Error fetching AWS inventory", extra={"error": str(e)})
            return f"Error fetching inventory: {str(e)}"
    
    def _format_history(self, messages: List[ConversationMessage]) -> str:
        """Format conversation history"""
        if not messages:
            return "No previous conversation"
        
        lines = []
        for msg in messages[-5:]:  # Last 5 messages
            lines.append(f"{msg.role}: {msg.content}")
        return "\n".join(lines)
