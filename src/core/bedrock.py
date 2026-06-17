import asyncio
import boto3
import json
import random
import time
from typing import List, Dict, Any
from botocore.config import Config
from botocore.exceptions import ClientError
from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import token_counter, estimated_cost
from src.core.circuit_breaker import CircuitBreaker


class BedrockClient:
    """AWS Bedrock client with async invoke, circuit breaker, and retry with jitter"""

    def __init__(self):
        self.client = boto3.client(
            'bedrock-runtime',
            region_name=settings.aws_region,
            config=Config(retries={'mode': 'adaptive', 'max_attempts': 3})
        )
        self.model_id = settings.bedrock_model_id
        self.max_retries = 3
        self.base_delay = 1.0
        self.circuit_breaker = CircuitBreaker("bedrock", failure_threshold=5, recovery_timeout=30.0)

    def _invoke_sync(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_cache: bool = True,
        agent_id: str = "unknown",
    ) -> str:
        """Synchronous Bedrock invocation with retry + jitter"""
        system_blocks = [{"type": "text", "text": system_prompt}]

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

                attrs = {"model": self.model_id, "agent_id": agent_id}
                token_counter.add(input_tokens, {**attrs, "direction": "input"})
                token_counter.add(output_tokens, {**attrs, "direction": "output"})
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

                if error_code in ['ThrottlingException', 'ServiceUnavailableException', 'InternalServerException']:
                    if attempt < self.max_retries - 1:
                        delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.info(f"Retrying in {delay:.2f}s", extra={"delay": delay})
                        time.sleep(delay)
                        continue

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

    async def invoke(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_cache: bool = True,
        agent_id: str = "unknown",
    ) -> str:
        """Async Bedrock invocation with circuit breaker"""
        if not self.circuit_breaker.can_execute():
            raise Exception("Bedrock circuit breaker is OPEN")

        try:
            result = await asyncio.to_thread(
                self._invoke_sync, messages, system_prompt, max_tokens, temperature, use_cache, agent_id
            )
            self.circuit_breaker.record_success()
            return result
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise


bedrock = BedrockClient()
