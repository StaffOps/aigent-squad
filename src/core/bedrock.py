import asyncio
import boto3
import json
import random
import time
from typing import List, Dict, Any, Optional
from botocore.config import Config
from botocore.exceptions import ClientError
from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import (
    token_counter, estimated_cost, llm_duration, prompt_size_tokens,
)
from src.core.model_tier import resolve_model, compute_cost
from src.core.token_budget import budget_tracker
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
            config=Config(
                retries={'mode': 'adaptive', 'max_attempts': 3},
                read_timeout=settings.bedrock_read_timeout_seconds,
                connect_timeout=settings.bedrock_connect_timeout_seconds,
            )
        )
        self.model_id = settings.bedrock_model_id
        self.max_retries = 3
        self.base_delay = 1.0
        self.circuit_breaker = CircuitBreaker("bedrock", failure_threshold=5, recovery_timeout=30.0)
        self._cache_supported: Optional[bool] = None  # lazy-probed on first cached call

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
        role: str = "agent",
        budget_session_id: Optional[str] = None,
        skip_input_guardrail: bool = False,
    ) -> str:
        """Synchronous Bedrock invocation with retry + jitter.

        The guardrail (spec 14, L1) is applied OUTSIDE the retry loop: the input
        is evaluated once before any model call, and the output once after a
        successful call. A ``GuardrailBlockedError`` is fail-closed and is not
        retried.

        Args:
            role: Logical tier ("classifier", "agent", "synthesis") used to
                resolve the model ID from config (spec 11).
            budget_session_id: session key charged for token-budget accounting.
                Defaults to ``session_id``. Callers that fan out sub-calls under
                a derived session_id (e.g. RCA evidence collection, isolated for
                history/audit — spec 14 finding E2) pass the parent's real
                session_id here so spend still counts against the cap that
                ``check_budget`` enforces at the entrypoint.
            skip_input_guardrail: When True, the app-level INPUT guardrail
                (PROMPT_ATTACK filter) is NOT applied on the assembled messages.
                G-6 fix: the ingress (supervisor.process_request) already guards
                the genuine end-user question ONCE — the per-stage assembled
                framing (classifier agent-catalog, agent instructions/context)
                is trusted content that false-positives the PROMPT_ATTACK filter
                (verbs like "manage/delete/execute" in the squad's own prompts).
                OUTPUT guardrail remains active regardless of this flag.
        """
        # Resolve model by role (spec 11 — config-driven tiering).
        model_id = resolve_model(role)

        if match_user_language:
            system_prompt = f"{system_prompt}\n\n{_LANGUAGE_DIRECTIVE}"

        # Build system block with optional prompt caching (spec 11).
        system_block: Dict[str, Any] = {"type": "text", "text": system_prompt}
        if use_cache and settings.bedrock_prompt_cache_enabled and self._is_cache_supported():
            system_block["cache_control"] = {"type": "ephemeral"}
        system_blocks = [system_block]

        # Layer 1 (INPUT) — evaluate the untrusted user content before spending
        # an invoke. Fail-closed: blocked or unavailable → GuardrailBlockedError.
        # G-6 fix: when skip_input_guardrail=True, the genuine user question was
        # already guarded at ingress (supervisor.process_request). Scanning the
        # assembled per-stage framing here would false-positive because our OWN
        # classifier catalog / agent instructions contain verbs like
        # "manage/delete/execute" that trip PROMPT_ATTACK (content filter, MEDIUM).
        if not skip_input_guardrail:
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
                    "model_id": model_id,
                    "role": role,
                    "attempt": attempt + 1,
                    "max_tokens": max_tokens,
                    "temperature": temperature
                })

                llm_start = time.time()
                response = self.client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(body)
                )
                llm_elapsed_ms = (time.time() - llm_start) * 1000

                result = json.loads(response['body'].read())

                usage = result.get('usage', {})
                input_tokens = usage.get('input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0)
                cache_read_tokens = usage.get('cache_read_input_tokens', 0)
                cache_creation_tokens = usage.get('cache_creation_input_tokens', 0)

                logger.info("Bedrock invocation successful", extra={
                    "model_id": model_id,
                    "role": role,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                })

                # Emit metrics with bounded cardinality labels (model family, agent_id, direction).
                attrs = {"model": model_id, "agent_id": agent_id}
                token_counter.add(input_tokens, {**attrs, "direction": "input"})
                token_counter.add(output_tokens, {**attrs, "direction": "output"})

                # Record session token usage for the budget hard cap (spec 11 T4).
                # Post-call: this response is returned; the NEXT call over budget
                # is refused by check_budget at the supervisor entrypoint.
                # budget_session_id (finding E2) decouples this from session_id so
                # fan-out sub-calls under a derived session key still book against
                # the parent session's cap.
                charged_session_id = budget_session_id or session_id
                if charged_session_id:
                    budget_tracker.record_usage(charged_session_id, input_tokens, output_tokens)

                # Per-model cost (spec 11 — replaces hardcoded Sonnet pricing).
                cost = compute_cost(
                    model_id, input_tokens, output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_creation_tokens,
                )
                estimated_cost.add(cost, attrs)

                # Efficiency metrics (spec 10): LLM latency + prompt size distribution.
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

                # Prompt caching validation: if the model/region rejects the
                # cache_control field, disable caching and retry transparently.
                if error_code == 'ValidationException' and 'cache_control' in str(e):
                    logger.warning(
                        "Prompt caching not supported by model/region — disabling",
                        extra={"model_id": model_id},
                    )
                    self._cache_supported = False
                    # Remove cache_control and retry this attempt (not counted).
                    system_blocks[0].pop("cache_control", None)
                    body["system"] = system_blocks
                    continue

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

    def _is_cache_supported(self) -> bool:
        """Check whether prompt caching is supported (lazy, cached result).

        Returns True by default (optimistic). The first ValidationException
        mentioning cache_control will set this to False for the process lifetime.
        """
        if self._cache_supported is None:
            # Optimistic: assume supported until proven otherwise.
            self._cache_supported = True
        return self._cache_supported

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
        role: str = "agent",
        budget_session_id: Optional[str] = None,
        skip_input_guardrail: bool = False,
    ) -> str:
        """Async Bedrock invocation with circuit breaker.

        A ``GuardrailBlockedError`` (fail-closed security refusal) is re-raised
        WITHOUT recording a circuit-breaker failure — it is a deliberate policy
        decision, not a Bedrock fault, and must not trip the breaker.

        Args:
            role: Logical tier ("classifier", "agent", "synthesis") for model
                resolution (spec 11).
            budget_session_id: see ``_invoke_sync`` (spec 14 finding E2).
            skip_input_guardrail: G-6 fix — skip per-stage INPUT scan when
                ingress already guarded the genuine user question.
        """
        if not self.circuit_breaker.can_execute():
            raise Exception("Bedrock circuit breaker is OPEN")

        try:
            result = await asyncio.to_thread(
                self._invoke_sync, messages, system_prompt, max_tokens, temperature,
                use_cache, agent_id, match_user_language, user_id, session_id, role,
                budget_session_id, skip_input_guardrail,
            )
            self.circuit_breaker.record_success()
            return result
        except GuardrailBlockedError:
            # Security refusal — not a service fault. Do not touch the breaker.
            raise
        except Exception:
            self.circuit_breaker.record_failure()
            raise


    # ── Converse API engine (spec 37, Phase 1) ─────────────────────────────

    def _converse_sync(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tool_config: Optional[Dict[str, Any]] = None,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
        role: str = "agent",
        budget_session_id: Optional[str] = None,
        apply_bedrock_guardrail: bool = True,
        skip_input_guardrail: bool = False,
    ) -> Dict[str, Any]:
        """Synchronous Bedrock Converse API invocation with retry + jitter.

        Unlike ``_invoke_sync`` (Messages API via invoke_model), this method
        uses the Bedrock **Converse API** which natively supports tool-use
        (toolConfig / toolUse / toolResult). It is a full engine with its own
        retry loop, response parser, per-call token accounting, guardrail
        integration, and budget tracking (spec 37, Decisão 6 — round-table B6).

        When ``apply_bedrock_guardrail`` is True (default), the Bedrock
        guardrailConfig is wired so Bedrock evaluates the turn server-side.
        When False, the Bedrock-level guardrail is SKIPPED for this call —
        used by the agentic loop for INTERMEDIATE tool-result-bearing turns
        where app-level redaction (B3 _guardrail_tool_result) handles safety
        instead. This prevents Bedrock from hard-blocking legit read-only data
        (K8s pod lists, namespace names) that trip PII/attack filters mid-loop.
        See spec 37 design: guardrail on INPUT + OUTPUT, redaction on intermediate.

        Args:
            messages: Converse-format messages (role + content blocks).
                Each message: {"role": "user"|"assistant", "content": [blocks]}.
                User content blocks: [{"text": "..."}] or [{"toolResult": {...}}].
                Assistant content blocks: [{"text": "..."}] or [{"toolUse": {...}}].
            system_prompt: System instructions (plain text).
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.
            tool_config: Optional Converse toolConfig dict, e.g.
                {"tools": [{"toolSpec": {"name": ..., "description": ...,
                "inputSchema": {"json": {...}}}}]}.
                When None, the call behaves as a plain converse (no tool-use).
            agent_id: Logical agent identifier for metrics/logging.
            user_id: User identifier for guardrail audit.
            session_id: Session identifier for guardrail audit.
            role: Logical tier for model resolution (spec 11).
            budget_session_id: Session key for token-budget accounting
                (defaults to session_id; see _invoke_sync docstring).
            apply_bedrock_guardrail: Whether to wire the Bedrock guardrailConfig
                on this call. Default True (safe for standalone calls). Set to
                False for intermediate agentic-loop turns carrying tool results,
                where app-level B3 redaction suffices. The app-level INPUT
                guardrail (pre-call) is ALWAYS applied regardless of this flag.

        Returns:
            Structured result dict:
                {
                    "stop_reason": str,  # "end_turn" | "tool_use" | "max_tokens" | ...
                    "content": [         # list of content blocks
                        {"type": "text", "text": "..."},
                        {"type": "tool_use", "toolUseId": "...", "name": "...", "input": {...}},
                    ],
                    "usage": {"input_tokens": int, "output_tokens": int},
                }
        """
        model_id = resolve_model(role)

        # Layer 1 (INPUT) — evaluate untrusted user content before spending an
        # invoke. Extracts text from last user message's content blocks.
        # G-6 fix: when skip_input_guardrail=True, the genuine user question was
        # already guarded at ingress (supervisor.process_request). The assembled
        # per-stage framing (agent instructions, tool schemas, history) is
        # trusted content that false-positives PROMPT_ATTACK. Skip here; OUTPUT
        # guardrail and tool-args/tool-result guardrails remain active.
        if not skip_input_guardrail:
            last_user_text = self._extract_converse_user_text(messages)
            if last_user_text:
                guardrail.apply(
                    last_user_text,
                    source="INPUT",
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                )

        # Build the Converse API request params.
        #
        # When Bedrock guardrailConfig is wired, we use INPUT TAGGING via
        # guardContent blocks to scope guardrail evaluation to ONLY the latest
        # user message text. This prevents PROMPT_ATTACK false-positives caused
        # by the guardrail evaluating system prompts, tool schemas, and prior
        # history as if they were untrusted user input.
        #
        # AWS docs (guardrails-use-converse-api): "Once you include a
        # guardContent block anywhere in your messages, the guardrail evaluates
        # ONLY the content within guardContent blocks. All other content blocks
        # not wrapped in guardContent are skipped." Content-policy filters
        # (PROMPT_ATTACK, PII entities) honor this; only word/regex filters
        # scan untagged text — our guardrail uses PII entities, not word/regex.
        use_guardrail = (
            apply_bedrock_guardrail
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

        # Wire tool_config when provided (agentic path).
        if tool_config is not None:
            params["toolConfig"] = tool_config

        # Wire Bedrock guardrailConfig for turn-level evaluation (spec 14).
        # Same GUARDRAIL_ID/VERSION the app uses for the invoke() path.
        # When apply_bedrock_guardrail=False (intermediate agentic-loop turns),
        # the Bedrock-level guardrail is skipped — app-level B3 redaction
        # (_guardrail_tool_result) handles those turns instead. This prevents
        # Bedrock from hard-blocking legitimate read-only K8s data (pod names,
        # IPs, namespace lists) that trips PII/attack filters mid-loop.
        if use_guardrail:
            params["guardrailConfig"] = {
                "guardrailIdentifier": settings.guardrail_id,
                "guardrailVersion": settings.guardrail_version,
            }

        for attempt in range(self.max_retries):
            try:
                logger.debug("Invoking Bedrock Converse", extra={
                    "model_id": model_id,
                    "role": role,
                    "attempt": attempt + 1,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "has_tools": tool_config is not None,
                })

                llm_start = time.time()
                response = self.client.converse(**params)
                llm_elapsed_ms = (time.time() - llm_start) * 1000

                # Parse response — Converse returns structured output directly.
                stop_reason = response.get("stopReason", "end_turn")
                output_message = response.get("output", {}).get("message", {})
                raw_content_blocks = output_message.get("content", [])
                usage = response.get("usage", {})
                input_tokens = usage.get("inputTokens", 0)
                output_tokens = usage.get("outputTokens", 0)

                logger.info("Bedrock Converse successful", extra={
                    "model_id": model_id,
                    "role": role,
                    "stop_reason": stop_reason,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "content_blocks": len(raw_content_blocks),
                })

                # Emit metrics (same pattern as invoke()).
                attrs = {"model": model_id, "agent_id": agent_id}
                token_counter.add(input_tokens, {**attrs, "direction": "input"})
                token_counter.add(output_tokens, {**attrs, "direction": "output"})

                # Session budget tracking (spec 11 T4).
                charged_session_id = budget_session_id or session_id
                if charged_session_id:
                    budget_tracker.record_usage(charged_session_id, input_tokens, output_tokens)

                # Per-model cost (spec 11).
                cost = compute_cost(model_id, input_tokens, output_tokens)
                estimated_cost.add(cost, attrs)

                # Efficiency metrics.
                llm_duration.record(llm_elapsed_ms, {"agent_id": agent_id})
                prompt_size_tokens.record(input_tokens, {"agent_id": agent_id})

                # Normalize content blocks into a uniform structure.
                content = self._parse_converse_content(raw_content_blocks)

                # Layer 1 (OUTPUT) — guardrail the final text when the model
                # produces a text response (stop_reason == "end_turn"). For
                # tool_use turns, the output guardrail runs after tool execution
                # on the final answer (later phase). Text blocks are always
                # checked immediately.
                for block in content:
                    if block["type"] == "text" and block["text"]:
                        guardrail.apply(
                            block["text"],
                            source="OUTPUT",
                            agent_id=agent_id,
                            user_id=user_id,
                            session_id=session_id,
                        )

                return {
                    "stop_reason": stop_reason,
                    "content": content,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                }

            except ClientError as e:
                error_code = e.response['Error']['Code']

                logger.warning("Bedrock Converse failed", extra={
                    "error_code": error_code,
                    "attempt": attempt + 1,
                    "max_retries": self.max_retries,
                })

                if error_code in ['ThrottlingException', 'ServiceUnavailableException', 'InternalServerException']:
                    if attempt < self.max_retries - 1:
                        delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.info(f"Converse retrying in {delay:.2f}s", extra={"delay": delay})
                        time.sleep(delay)
                        continue

                logger.error("Bedrock Converse error", extra={
                    "error_code": error_code,
                    "error_message": str(e),
                })
                raise

            except Exception as e:
                logger.error("Unexpected error in Bedrock Converse", extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                })
                raise

        raise Exception(f"Failed to invoke Bedrock Converse after {self.max_retries} attempts")

    @staticmethod
    def _extract_converse_user_text(messages: List[Dict[str, Any]]) -> str:
        """Extract text from user-role messages for pre-call guardrail evaluation.

        Converse messages use block format:
            {"role": "user", "content": [{"text": "..."}, {"toolResult": {...}}, ...]}

        Only text blocks from user messages are evaluated (untrusted input).
        """
        parts: List[str] = []
        for m in messages:
            if m.get("role") != "user":
                continue
            for block in m.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
        return "\n".join(p for p in parts if p)

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

        This eliminates PROMPT_ATTACK false-positives caused by the guardrail
        evaluating tool-schema JSON or system instructions as if they were
        adversarial user input.

        Returns:
            A NEW list of messages (shallow copy; only the latest user message
            is deep-copied to avoid mutating the caller's data).
        """
        # Find the index of the last user message.
        last_user_idx: Optional[int] = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            # No user message found — return as-is (defensive).
            return messages

        # Shallow copy the message list; deep-copy only the target message.
        result = list(messages)
        original_msg = messages[last_user_idx]
        tagged_content: List[Dict[str, Any]] = []

        for block in original_msg.get("content", []):
            if isinstance(block, dict) and "text" in block and "toolResult" not in block:
                # Wrap text block in guardContent for guardrail evaluation.
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

        This uniform format decouples downstream code from the raw Converse
        response shape and makes it straightforward to iterate over text vs
        tool_use blocks.
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

    async def converse(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        tool_config: Optional[Dict[str, Any]] = None,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
        role: str = "agent",
        budget_session_id: Optional[str] = None,
        apply_bedrock_guardrail: bool = True,
        skip_input_guardrail: bool = False,
    ) -> Dict[str, Any]:
        """Async Bedrock Converse API invocation with circuit breaker.

        Async wrapper around ``_converse_sync``, analogous to ``invoke()``
        wrapping ``_invoke_sync``. Uses the same circuit breaker instance.

        A ``GuardrailBlockedError`` is re-raised without recording a
        circuit-breaker failure (deliberate policy refusal, not a Bedrock fault).

        Args:
            apply_bedrock_guardrail: When False, the Bedrock-level guardrailConfig
                is omitted from this call. Used for intermediate agentic-loop turns
                carrying tool results where app-level B3 redaction suffices.
                Default True preserves behavior for standalone/non-loop callers.
            skip_input_guardrail: G-6 fix — skip per-stage INPUT scan when
                ingress already guarded the genuine user question.

        Returns:
            Structured result dict (see ``_converse_sync`` docstring).
        """
        if not self.circuit_breaker.can_execute():
            raise Exception("Bedrock circuit breaker is OPEN")

        try:
            result = await asyncio.to_thread(
                self._converse_sync, messages, system_prompt, max_tokens,
                temperature, tool_config, agent_id, user_id, session_id,
                role, budget_session_id, apply_bedrock_guardrail,
                skip_input_guardrail,
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
