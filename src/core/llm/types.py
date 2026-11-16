"""Provider-neutral LLM transport contract (spec 28).

``LLMService`` (``src/core/llm/service.py``) owns every cross-cutting concern —
circuit breaker, retry/backoff, guardrail calls, token/cost metrics, session
token budget. A provider only does transport: build the request, call the API,
parse the response into the shapes below, and classify its own errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


class ErrorClass(str, Enum):
    """Bounded classification of a transport error (also a metric label)."""

    THROTTLED = "throttled"        # 429 / ThrottlingException — retryable
    SERVER_ERROR = "server_error"  # 5xx / ServiceUnavailable — retryable
    TIMEOUT = "timeout"            # read/connect timeout, transport error — retryable
    CLIENT_ERROR = "client_error"  # other 4xx / validation — NOT retryable
    MALFORMED = "malformed"        # unparseable response — NOT retryable

    @property
    def retryable(self) -> bool:
        return self in (ErrorClass.THROTTLED, ErrorClass.SERVER_ERROR, ErrorClass.TIMEOUT)


class LLMProviderError(Exception):
    """Transport failure raised by a non-Bedrock provider.

    Carries only bounded, non-secret facts (provider, error class, HTTP status,
    the server's error code). Never the request body, never the API key.
    """

    def __init__(
        self,
        provider: str,
        error_class: ErrorClass,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.error_class = error_class
        self.status_code = status_code
        self.code = code
        detail = f"{provider} LLM call failed: {error_class.value}"
        if status_code is not None:
            detail += f" (HTTP {status_code})"
        if code:
            detail += f" [{code}]"
        super().__init__(detail)


@dataclass(frozen=True)
class LLMResult:
    """One completed model call, as seen by the common layer.

    ``model`` is the id actually invoked (AIP ARN for Bedrock cost attribution).
    ``content`` is the normalized block list (``{"type": "text"|"tool_use", ...}``);
    ``text`` is the concatenation of its text blocks.
    """

    model: str
    input_tokens: int
    output_tokens: int
    content: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # True when the provider returned no usage and the counts are estimated.
    usage_estimated: bool = False

    @property
    def text(self) -> str:
        return "".join(
            str(b.get("text", "")) for b in self.content if b.get("type") == "text"
        )


@runtime_checkable
class LLMProvider(Protocol):
    """Transport hooks every provider implements (template-method contract).

    ``LLMService`` calls these from inside its retry loop. Implementations must
    not retry, must not emit token/cost metrics and must not call the guardrail —
    those belong to the common layer.
    """

    provider_name: str

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
        use_cache: bool,
    ) -> LLMResult:
        """Plain completion (Anthropic Messages-style ``messages``)."""
        ...

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
        """Tool-capable turn (Bedrock Converse-style ``messages``/``tool_config``)."""
        ...

    def _classify_error(self, exc: BaseException, model_id: str) -> Optional[ErrorClass]:
        """Classify a transport exception; ``None`` = not a transport error."""
        ...
