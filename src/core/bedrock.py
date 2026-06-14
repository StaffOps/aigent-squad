import boto3
import json
import time
from typing import List, Dict, Any
from botocore.exceptions import ClientError
from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import token_counter, estimated_cost

class BedrockClient:
    """AWS Bedrock client with prompt caching and error handling"""
    
    def __init__(self):
        self.client = boto3.client('bedrock-runtime', region_name=settings.aws_region)
        self.model_id = settings.bedrock_model_id
        self.max_retries = 3
        self.base_delay = 1.0
    
    def invoke(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_cache: bool = True
    ) -> str:
        """Invoke Bedrock with retry logic"""
        
        system_blocks = [{"type": "text", "text": system_prompt}]
        # Prompt caching disabled for compatibility
        # if use_cache:
        #     system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_blocks,
            "messages": messages
        }
        
        for attempt in range(self.max_retries):
            try:
                logger.debug("Invoking Bedrock", extra={
                    "model_id": self.model_id,
                    "attempt": attempt + 1,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                })
                
                response = self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(body)
                )
                
                result = json.loads(response['body'].read())
                
                input_tokens = result.get('usage', {}).get('input_tokens', 0)
                output_tokens = result.get('usage', {}).get('output_tokens', 0)
                
                logger.info("Bedrock invocation successful", extra={
                    "model_id": self.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens
                })
                
                # Record token and cost metrics
                attrs = {"model": self.model_id}
                token_counter.add(input_tokens, {**attrs, "direction": "input"})
                token_counter.add(output_tokens, {**attrs, "direction": "output"})
                # Approximate pricing: Sonnet $3/M input, $15/M output
                cost = (input_tokens * 3 / 1_000_000) + (output_tokens * 15 / 1_000_000)
                estimated_cost.add(cost, attrs)
                
                return result['content'][0]['text']
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                
                logger.warning("Bedrock invocation failed", extra={
                    "error_code": error_code,
                    "attempt": attempt + 1,
                    "max_retries": self.max_retries
                })
                
                # Retry on throttling or service errors
                if error_code in ['ThrottlingException', 'ServiceUnavailableException', 'InternalServerException']:
                    if attempt < self.max_retries - 1:
                        delay = self.base_delay * (2 ** attempt)  # Exponential backoff
                        logger.info(f"Retrying in {delay}s", extra={"delay": delay})
                        time.sleep(delay)
                        continue
                
                # Don't retry on validation errors
                logger.error("Bedrock invocation error", extra={
                    "error_code": error_code,
                    "error_message": str(e)
                })
                raise
            
            except Exception as e:
                logger.error("Unexpected error invoking Bedrock", extra={
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                raise
        
        raise Exception(f"Failed to invoke Bedrock after {self.max_retries} attempts")

bedrock = BedrockClient()
