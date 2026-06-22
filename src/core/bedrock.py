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
from src.core.metrics import (
    token_counter, estimated_cost, llm_duration, prompt_size_tokens,
)
from src.core.circuit_breaker import CircuitBreaker
from src.core.guardrail import guardrail, GuardrailBlockedError


# Applied to every user-facing generation (specialists, synthesizer, RCA).
# The system prompts are in English by convention; this keeps the *reply* in
# the user's language. Opt out (match_user_language=False) for structured
# output like the classifier (JSON).
_LANGUAGE_DIRECTIVE = (
    "IMPORTANT: Respond in the SAME language as the user's question "
    "(e.g. a Portuguese question gets a Portuguese answer, English gets English). "
    "These instructions are in English only by convention — they do not set your reply language."
)


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

    @staticmethod
    def _user_text(messages: List[Dict[str, Any]]) -> str:
        """Concatenate user-role message text for guardrail INPUT evaluation.

        Only user content is untrusted input; assistant turns are our own prior
        output (already guardrail-checked when produced).
        """
        parts = []
        for m in messages:
            if m.get("role") != "user":
                continue
            content = m.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                # Anthropic block format: [{"type":"text","text":...}, ...]
                parts.extend(
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
        return "\n".join(p for p in parts if p)

    def _invoke_sync(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_cache: bool = True,
        agent_id: str = "unknown",
        match_user_language: bool = True,
        user_id: str = "unknown",
        session_id: str = "",
    ) -> str:
        """Synchronous Bedrock invocation with retry + jitter.

        The guardrail (spec 14, L1) is applied OUTSIDE the retry loop: the input
        is evaluated once before any model call, and the output once after a
        successful call. A ``GuardrailBlockedError`` is fail-closed and is not
        retried.
        """
        if match_user_language:
            system_prompt = f"{system_prompt}\n\n{_LANGUAGE_DIRECTIVE}"
        system_blocks = [{"type": "text", "text": system_prompt}]

        # Layer 1 (INPUT) — evaluate the untrusted user content before spending
        # an invoke. Fail-closed: blocked or unavailable → GuardrailBlockedError.
        guardrail.apply(
            self._user_text(messages),
            source="INPUT",
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )

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

                llm_start = time.time()
                response = self.client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(body)
                )
                llm_elapsed_ms = (time.time() - llm_start) * 1000

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

                # Efficiency metrics (spec 10): LLM latency + prompt size distribution.
                # prompt_size_tokens is a histogram of input tokens (p50/p95 → bloat),
                # distinct from token_counter which is the running spend total.
                llm_duration.record(llm_elapsed_ms, {"agent_id": agent_id})
                prompt_size_tokens.record(input_tokens, {"agent_id": agent_id})

                response_text = result['content'][0]['text']

                # Layer 1 (OUTPUT) — evaluate the model response before it
                # reaches the user (grounding / PII / denied content). Fail-closed.
                guardrail.apply(
                    response_text,
                    source="OUTPUT",
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                )

                return response_text

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
        match_user_language: bool = True,
        user_id: str = "unknown",
        session_id: str = "",
    ) -> str:
        """Async Bedrock invocation with circuit breaker.

        A ``GuardrailBlockedError`` (fail-closed security refusal) is re-raised
        WITHOUT recording a circuit-breaker failure — it is a deliberate policy
        decision, not a Bedrock fault, and must not trip the breaker.
        """
        if not self.circuit_breaker.can_execute():
            raise Exception("Bedrock circuit breaker is OPEN")

        try:
            result = await asyncio.to_thread(
                self._invoke_sync, messages, system_prompt, max_tokens, temperature,
                use_cache, agent_id, match_user_language, user_id, session_id,
            )
            self.circuit_breaker.record_success()
            return result
        except GuardrailBlockedError:
            # Security refusal — not a service fault. Do not touch the breaker.
            raise
        except Exception:
            self.circuit_breaker.record_failure()
            raise


bedrock = BedrockClient()
