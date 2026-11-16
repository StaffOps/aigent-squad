"""LLMService — the provider-neutral common layer (spec 28, Decision 5).

Everything that must behave the same whichever LLM answers lives here, once:

- circuit breaker + concurrency semaphore (spec 25 T8/T9 queue metrics)
- retry with exponential backoff + jitter, driven by the provider's error class
- app-level guardrail (spec 14 L1) on INPUT before the call, OUTPUT after it
- token / cost / latency metrics labeled ``{model, agent_id, direction}`` (spec 27)
- session token budget accounting (spec 11 T4, finding E2)
- role → model resolution (spec 11) and the reply-language directive

Providers subclass this and implement the transport hooks described by
``src.core.llm.types.LLMProvider``. They never retry, never emit token/cost
metrics and never call the guardrail themselves.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional

from src.core.circuit_breaker import CircuitBreaker
from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError, guardrail
from src.core.llm.types import ErrorClass, LLMResult
from src.core.logger import logger
from src.core.metrics import (
    bedrock_queue_depth,
    bedrock_queue_wait,
    estimated_cost,
    llm_duration,
    llm_provider_errors,
    prompt_size_tokens,
    token_counter,
)
from src.core.model_tier import compute_cost, resolve_model
from src.core.token_budget import budget_tracker

# Applied to every user-facing generation (specialists, synthesizer, RCA).
# The system prompts are in English by convention; this keeps the *reply* in
# the user's language. Opt out (match_user_language=False) for structured
# output like the classifier (JSON).
_LANGUAGE_DIRECTIVE = (
    "IMPORTANT: Respond in the SAME language as the user's question "
    "(e.g. a Portuguese question gets a Portuguese answer, English gets English). "
    "These instructions are in English only by convention — they do not set your reply language."
)


class LLMService:
    """Common policy layer; subclasses supply the transport (template method).

    Subclasses MUST set ``provider_name`` (metric label, 2 values today) and
    ``display_name`` (log/error text) and implement ``_complete``,
    ``_converse_transport`` and ``_classify_error``.
    """

    provider_name: str = "unknown"
    display_name: str = "LLM"

    def __init__(self, breaker_name: str) -> None:
        self.max_retries = 3
        self.base_delay = 1.0
        self.circuit_breaker = CircuitBreaker(breaker_name, failure_threshold=5, recovery_timeout=30.0)
        # Concurrency semaphore (spec 25 T8): prevents overwhelming the endpoint.
        self._semaphore: Optional[asyncio.Semaphore] = None
        limit = getattr(settings, "bedrock_max_concurrent", 20)
        self._semaphore_limit: int = limit if isinstance(limit, int) else 20

    # ── Transport hooks (overridden by providers) ──────────────────────────

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
        use_cache: bool,
    ) -> LLMResult:
        raise NotImplementedError

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
        raise NotImplementedError

    def _classify_error(self, exc: BaseException, model_id: str) -> Optional[ErrorClass]:
        """Return the transport error class, or ``None`` (re-raise immediately)."""
        return None

    def _recover_in_place(self, exc: BaseException, model_id: str) -> bool:
        """Provider-local recovery: True = move to the next attempt at once.

        No backoff, no error metric — used by Bedrock to drop an unsupported
        ``cache_control`` block. The attempt is still consumed.
        """
        return False

    def _error_code(self, exc: BaseException) -> Optional[str]:
        """Provider error code for LOGS only (never a metric label), e.g. the
        boto3 ``ClientError`` code. Default: none."""
        return None

    def close(self) -> None:
        """Release transport resources (supervisor lifespan shutdown). Default: no-op."""
        return None

    # ── Shared helpers ─────────────────────────────────────────────────────

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazy-init semaphore (works even when __init__ is bypassed in tests)."""
        sem = getattr(self, "_semaphore", None)
        if sem is None:
            limit = getattr(self, "_semaphore_limit", 20)
            sem = asyncio.Semaphore(max(limit, 1))
            self._semaphore = sem
        return sem

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

    def _record_usage(
        self,
        result: LLMResult,
        model_id: str,
        agent_id: str,
        session_id: str,
        budget_session_id: Optional[str],
        elapsed_ms: float,
        include_cache_tokens: bool,
    ) -> None:
        """Token / cost / budget / latency accounting for one successful call."""
        # Bounded-cardinality labels (model, agent_id, direction) — spec 27.
        attrs = {"model": model_id, "agent_id": agent_id}
        token_counter.add(result.input_tokens, {**attrs, "direction": "input"})
        token_counter.add(result.output_tokens, {**attrs, "direction": "output"})

        # Session token budget (spec 11 T4). Post-call: this response is
        # returned; the NEXT call over budget is refused by check_budget at the
        # supervisor entrypoint. budget_session_id (finding E2) lets fan-out
        # sub-calls under a derived session key book against the parent's cap.
        charged_session_id = budget_session_id or session_id
        if charged_session_id:
            budget_tracker.record_usage(charged_session_id, result.input_tokens, result.output_tokens)

        # Per-model cost (spec 11) — provider-aware pricing in model_tier.
        if include_cache_tokens:
            cost = compute_cost(
                model_id, result.input_tokens, result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                cache_write_tokens=result.cache_write_tokens,
            )
        else:
            cost = compute_cost(model_id, result.input_tokens, result.output_tokens)
        estimated_cost.add(cost, attrs)

        # Efficiency metrics (spec 10): LLM latency + prompt size distribution.
        llm_duration.record(elapsed_ms, {"agent_id": agent_id})
        prompt_size_tokens.record(result.input_tokens, {"agent_id": agent_id})

    def _retry_or_raise(
        self, exc: Exception, model_id: str, attempt: int, op: str, allow_recovery: bool = False,
    ) -> None:
        """Classify ``exc``; return (after backoff) when a retry is due, else re-raise.

        With ``allow_recovery`` (plain-completion path only), a provider-local
        recovery (``_recover_in_place``) returns immediately.
        The ORIGINAL exception is always the one re-raised (e.g. the boto3
        ``ClientError`` for Bedrock), so callers' error handling is unchanged.
        """
        if allow_recovery and self._recover_in_place(exc, model_id):
            return
        error_class = self._classify_error(exc, model_id)
        if error_class is None:
            logger.error(f"Unexpected error in {self.display_name} {op}", extra={
                "provider": self.provider_name,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            raise exc

        llm_provider_errors.add(1, {"provider": self.provider_name, "error_class": error_class.value})
        logger.warning(f"{self.display_name} {op} failed", extra={
            "provider": self.provider_name,
            "error_class": error_class.value,
            "error_code": self._error_code(exc),
            "error_type": type(exc).__name__,
            "attempt": attempt + 1,
            "max_retries": self.max_retries,
        })

        if error_class.retryable and attempt < self.max_retries - 1:
            delay = self.base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.info(f"Retrying in {delay:.2f}s", extra={"delay": delay})
            time.sleep(delay)
            return

        logger.error(f"{self.display_name} {op} error", extra={
            "provider": self.provider_name,
            "error_class": error_class.value,
            "error_code": self._error_code(exc),
            "error_message": str(exc),
        })
        raise exc

    async def _guarded_call(self, fn: Any, *args: Any) -> Any:
        """Circuit breaker + semaphore around a sync engine run in a thread.

        A ``GuardrailBlockedError`` (fail-closed security refusal) is re-raised
        WITHOUT recording a circuit-breaker failure — it is a deliberate policy
        decision, not a provider fault, and must not trip the breaker.
        """
        if not self.circuit_breaker.can_execute():
            raise Exception(f"{self.display_name} circuit breaker is OPEN")

        # Spec 25 T8+T9: acquire semaphore with queue metrics
        sem = self._get_semaphore()
        bedrock_queue_depth.add(1)
        wait_start = time.time()
        try:
            await sem.acquire()
        except BaseException:
            bedrock_queue_depth.add(-1)
            raise
        bedrock_queue_depth.add(-1)
        bedrock_queue_wait.record(time.time() - wait_start)

        try:
            result = await asyncio.to_thread(fn, *args)
            self.circuit_breaker.record_success()
            return result
        except GuardrailBlockedError:
            # Security refusal — not a service fault. Do not touch the breaker.
            raise
        except Exception:
            self.circuit_breaker.record_failure()
            raise
        finally:
            sem.release()

    # ── Plain completion engine ────────────────────────────────────────────

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
        """Synchronous invocation with retry + jitter.

        The guardrail (spec 14, L1) is applied OUTSIDE the retry loop: the input
        is evaluated once before any model call, and the output once after a
        successful call. A ``GuardrailBlockedError`` is fail-closed and is not
        retried. (Under a non-Bedrock provider L1 is inactive and ``apply`` is a
        no-op — spec 28 Decision 10.)

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

        # Layer 1 (INPUT) — evaluate the untrusted user content before spending
        # an invoke. Fail-closed: blocked or unavailable → GuardrailBlockedError.
        if not skip_input_guardrail:
            guardrail.apply(
                self._user_text(messages),
                source="INPUT",
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
            )

        for attempt in range(self.max_retries):
            logger.debug(f"Invoking {self.display_name}", extra={
                "provider": self.provider_name,
                "model_id": model_id,
                "role": role,
                "attempt": attempt + 1,
                "max_tokens": max_tokens,
                "temperature": temperature,
            })
            llm_start = time.time()
            try:
                result = self._complete(
                    messages, system_prompt, max_tokens, temperature, model_id, use_cache,
                )
            except Exception as e:
                self._retry_or_raise(e, model_id, attempt, "invocation", allow_recovery=True)
                continue
            llm_elapsed_ms = (time.time() - llm_start) * 1000

            logger.info(f"{self.display_name} invocation successful", extra={
                "provider": self.provider_name,
                "model_id": model_id,
                "role": role,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cache_read_tokens": result.cache_read_tokens,
                "cache_creation_tokens": result.cache_write_tokens,
            })
            self._record_usage(
                result, model_id, agent_id, session_id, budget_session_id,
                llm_elapsed_ms, include_cache_tokens=True,
            )

            response_text = result.text

            # Layer 1 (OUTPUT) — evaluate the model response before it reaches
            # the user (grounding / PII / denied content). Fail-closed.
            guardrail.apply(
                response_text,
                source="OUTPUT",
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
            )
            return response_text

        raise Exception(f"Failed to invoke {self.display_name} after {self.max_retries} attempts")

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
        """Async invocation with circuit breaker (see ``_invoke_sync``)."""
        result: str = await self._guarded_call(
            self._invoke_sync, messages, system_prompt, max_tokens, temperature,
            use_cache, agent_id, match_user_language, user_id, session_id, role,
            budget_session_id, skip_input_guardrail,
        )
        return result

    # ── Tool-capable (Converse-format) engine — spec 37 ────────────────────

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
        model_id_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synchronous tool-capable turn with retry + jitter (spec 37).

        Messages and ``tool_config`` use the Bedrock Converse shape (the
        internal canonical format — spec 28 Decision 6); non-Bedrock providers
        translate at their edge.

        ``apply_bedrock_guardrail`` is forwarded to the transport as
        ``apply_provider_guardrail``: on Bedrock it wires the server-side
        ``guardrailConfig`` with guardContent input tagging
        (``BedrockClient._tag_latest_user_message_for_guardrail``) so Bedrock
        evaluates only the latest user text; providers without a server-side
        guardrail ignore it. The app-level INPUT guardrail (pre-call, unless
        ``skip_input_guardrail``) and OUTPUT guardrail (every text block) run
        here regardless.

        Returns:
            {
                "stop_reason": str,  # "end_turn" | "tool_use" | "max_tokens" | ...
                "content": [{"type": "text", "text": ...},
                            {"type": "tool_use", "toolUseId": ..., "name": ..., "input": {...}}],
                "usage": {"input_tokens": int, "output_tokens": int},
            }
        """
        model_id = model_id_override if model_id_override else resolve_model(role)

        # Layer 1 (INPUT) — G-6: skipped when ingress already guarded the
        # genuine user question; OUTPUT and tool-args/result guardrails remain.
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

        for attempt in range(self.max_retries):
            logger.debug(f"Invoking {self.display_name} Converse", extra={
                "provider": self.provider_name,
                "model_id": model_id,
                "role": role,
                "attempt": attempt + 1,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "has_tools": tool_config is not None,
            })
            llm_start = time.time()
            try:
                result = self._converse_transport(
                    messages, system_prompt, max_tokens, temperature, model_id,
                    tool_config, apply_bedrock_guardrail,
                )
            except Exception as e:
                self._retry_or_raise(e, model_id, attempt, "Converse")
                continue
            llm_elapsed_ms = (time.time() - llm_start) * 1000

            logger.info(f"{self.display_name} Converse successful", extra={
                "provider": self.provider_name,
                "model_id": model_id,
                "role": role,
                "stop_reason": result.stop_reason,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "content_blocks": len(result.content),
            })
            self._record_usage(
                result, model_id, agent_id, session_id, budget_session_id,
                llm_elapsed_ms, include_cache_tokens=False,
            )

            # Layer 1 (OUTPUT) — guardrail every text block immediately. For
            # tool_use turns the final answer is checked when it arrives.
            for block in result.content:
                if block["type"] == "text" and block["text"]:
                    guardrail.apply(
                        block["text"],
                        source="OUTPUT",
                        agent_id=agent_id,
                        user_id=user_id,
                        session_id=session_id,
                    )

            return {
                "stop_reason": result.stop_reason,
                "content": result.content,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
            }

        raise Exception(f"Failed to invoke {self.display_name} Converse after {self.max_retries} attempts")

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
        model_id_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Async tool-capable turn with circuit breaker (see ``_converse_sync``).

        Args:
            apply_bedrock_guardrail: When False, the Bedrock-level guardrailConfig
                is omitted (intermediate agentic-loop turns carrying tool
                results, where app-level B3 redaction suffices).
            skip_input_guardrail: G-6 fix — skip per-stage INPUT scan when
                ingress already guarded the genuine user question.
            model_id_override: Spec 38 — bypasses resolve_model(role).
        """
        result: Dict[str, Any] = await self._guarded_call(
            self._converse_sync, messages, system_prompt, max_tokens,
            temperature, tool_config, agent_id, user_id, session_id,
            role, budget_session_id, apply_bedrock_guardrail,
            skip_input_guardrail, model_id_override,
        )
        return result
