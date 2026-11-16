"""AWS Bedrock provider (spec 28 — ``BedrockClient`` = the Bedrock transport).

Since spec 28 the cross-cutting policy (circuit breaker, semaphore, retry with
jitter, app-level guardrail, token/cost/budget metrics, model resolution) lives
in ``src.core.llm.service.LLMService``. This module keeps only what is
Bedrock-specific — boto3 client, request bodies (Messages API via
``invoke_model`` and the Converse API), Anthropic prompt caching
(``cache_control``), Bedrock server-side guardrail wiring with guardContent
input tagging, response parsing and ``ClientError`` classification. Behavior is
unchanged from the pre-spec-28 ``BedrockClient``.
"""
import json
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.core.config import settings
from src.core.llm.service import LLMService
from src.core.llm.types import ErrorClass, LLMResult
from src.core.logger import logger
from src.core.metrics import bedrock_throttles

# ClientError codes the retry loop backs off on (unchanged since spec 11).
_THROTTLE_CODE = "ThrottlingException"
_SERVER_CODES = ("ServiceUnavailableException", "InternalServerException")


class BedrockClient(LLMService):
    """AWS Bedrock client with async invoke, circuit breaker, and retry with jitter"""

    provider_name = "bedrock"
    display_name = "Bedrock"

    def __init__(self) -> None:
        self.client = boto3.client(
            'bedrock-runtime',
            region_name=settings.aws_region,
            config=Config(
                retries={'mode': 'adaptive', 'max_attempts': 3},
                read_timeout=settings.bedrock_read_timeout_seconds,
                connect_timeout=settings.bedrock_connect_timeout_seconds,
            )
        )
        self.model_id = settings.bedrock_model_id
        self._cache_supported: Optional[bool] = None  # lazy-probed on first cached call
        super().__init__("bedrock")

    # ── Messages API transport (invoke_model) ─────────────────────────────

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
        use_cache: bool,
    ) -> LLMResult:
        # System block with optional prompt caching (spec 11). Rebuilt per
        # attempt, so once caching is found unsupported it stays off.
        system_block: Dict[str, Any] = {"type": "text", "text": system_prompt}
        if use_cache and settings.bedrock_prompt_cache_enabled and self._is_cache_supported():
            system_block["cache_control"] = {"type": "ephemeral"}

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": [system_block],
            "messages": messages
        }
        response = self.client.invoke_model(
            modelId=model_id,
            body=json.dumps(body)
        )
        result = json.loads(response['body'].read())

        usage = result.get('usage', {})
        return LLMResult(
            model=model_id,
            input_tokens=usage.get('input_tokens', 0),
            output_tokens=usage.get('output_tokens', 0),
            cache_read_tokens=usage.get('cache_read_input_tokens', 0),
            cache_write_tokens=usage.get('cache_creation_input_tokens', 0),
            content=[{"type": "text", "text": result['content'][0]['text']}],
        )

    def _recover_in_place(self, exc: BaseException, model_id: str) -> bool:
        """Prompt caching validation: if the model/region rejects the
        cache_control field, disable caching and retry on the next attempt."""
        if (
            isinstance(exc, ClientError)
            and exc.response['Error']['Code'] == 'ValidationException'
            and 'cache_control' in str(exc)
        ):
            logger.warning(
                "Prompt caching not supported by model/region — disabling",
                extra={"model_id": model_id},
            )
            self._cache_supported = False
            return True
        return False

    def _classify_error(self, exc: BaseException, model_id: str) -> Optional[ErrorClass]:
        """Map a boto3 ``ClientError`` to an error class (others: not transport)."""
        if not isinstance(exc, ClientError):
            return None
        error_code = exc.response['Error']['Code']
        if error_code == _THROTTLE_CODE:
            # Count EVERY throttle (incl. the final one before exhaustion) per model.
            bedrock_throttles.add(1, {"model": model_id})
            return ErrorClass.THROTTLED
        if error_code in _SERVER_CODES:
            return ErrorClass.SERVER_ERROR
        return ErrorClass.CLIENT_ERROR

    def _error_code(self, exc: BaseException) -> Optional[str]:
        """boto3 ``ClientError`` code for the retry/failure logs (not a metric label)."""
        if isinstance(exc, ClientError):
            return str(exc.response['Error']['Code'])
        return None

    def _is_cache_supported(self) -> bool:
        """Check whether prompt caching is supported (lazy, cached result).

        Returns True by default (optimistic). The first ValidationException
        mentioning cache_control will set this to False for the process lifetime.
        """
        if getattr(self, "_cache_supported", None) is None:
            # Optimistic: assume supported until proven otherwise.
            self._cache_supported = True
        return bool(self._cache_supported)

    # ── Converse API transport (spec 37) ──────────────────────────────────

    def _converse_transport(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
        tool_config: Optional[Dict[str, Any]],
        apply_provider_guardrail: bool,
    ) -> LLMResult:
        # When the Bedrock guardrailConfig is wired, INPUT TAGGING via
        # guardContent blocks scopes evaluation to ONLY the latest user message
        # text (AWS docs, guardrails-use-converse-api: once a guardContent block
        # is present, the guardrail evaluates ONLY guardContent blocks). This
        # prevents PROMPT_ATTACK false-positives on system prompts, tool schemas
        # and prior history. When apply_provider_guardrail=False (intermediate
        # agentic-loop turns), app-level B3 redaction handles safety instead.
        use_guardrail = (
            apply_provider_guardrail
            and settings.guardrail_enabled
            and settings.guardrail_id
        )
        converse_messages = (
            self._tag_latest_user_message_for_guardrail(messages)
            if use_guardrail
            else messages
        )

        params: Dict[str, Any] = {
            "modelId": model_id,
            "messages": converse_messages,
            "system": [{"text": system_prompt}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if tool_config is not None:
            params["toolConfig"] = tool_config
        if use_guardrail:
            params["guardrailConfig"] = {
                "guardrailIdentifier": settings.guardrail_id,
                "guardrailVersion": settings.guardrail_version,
            }

        response = self.client.converse(**params)

        output_message = response.get("output", {}).get("message", {})
        usage = response.get("usage", {})
        return LLMResult(
            model=model_id,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            stop_reason=response.get("stopReason", "end_turn"),
            content=self._parse_converse_content(output_message.get("content", [])),
        )

    @staticmethod
    def _tag_latest_user_message_for_guardrail(
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply Bedrock input tagging to scope guardrail to the latest user text.

        Wraps text content blocks of the MOST RECENT user message in
        ``guardContent`` blocks. Per AWS docs (guardrails-use-converse-api):
        once any guardContent block is present, the guardrail evaluates ONLY
        guardContent blocks — system prompt, tool schemas, toolResult blocks,
        prior history, and assistant turns are all skipped by content-policy
        filters (PROMPT_ATTACK, PII entities).

        Only plain ``{"text": "..."}`` blocks in the latest user message are
        wrapped. Blocks of other types (``toolResult``, etc.) and older user
        messages remain untouched — the model still sees everything, but the
        guardrail scopes its evaluation to the tagged content only.

        Returns:
            A NEW list of messages (shallow copy; only the latest user message
            is rebuilt to avoid mutating the caller's data).
        """
        last_user_idx: Optional[int] = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            # No user message found — return as-is (defensive).
            return messages

        result = list(messages)
        original_msg = messages[last_user_idx]
        tagged_content: List[Dict[str, Any]] = []

        for block in original_msg.get("content", []):
            if isinstance(block, dict) and "text" in block and "toolResult" not in block:
                tagged_content.append({
                    "guardContent": {"text": {"text": block["text"]}}
                })
            else:
                # toolResult and other block types pass through untagged.
                tagged_content.append(block)

        result[last_user_idx] = {
            "role": "user",
            "content": tagged_content,
        }
        return result

    @staticmethod
    def _parse_converse_content(raw_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize Converse response content blocks into a uniform structure.

        Converse API returns blocks like:
            [{"text": "..."}, {"toolUse": {"toolUseId": "...", "name": "...", "input": {...}}}]

        We normalize to:
            [{"type": "text", "text": "..."}, {"type": "tool_use", "toolUseId": "...", "name": "...", "input": {...}}]
        """
        result: List[Dict[str, Any]] = []
        for block in raw_blocks:
            if "text" in block:
                result.append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                tu = block["toolUse"]
                result.append({
                    "type": "tool_use",
                    "toolUseId": tu.get("toolUseId", ""),
                    "name": tu.get("name", ""),
                    "input": tu.get("input", {}),
                })
            else:
                # Unknown block type — preserve as-is with a generic type.
                result.append({"type": "unknown", "raw": block})
        return result


bedrock = BedrockClient()
