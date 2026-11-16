"""OpenAI-compatible provider (spec 28 Phase 2, Decision 4).

A hand-written ``httpx`` client for ``POST {OPENAI_BASE_URL}/chat/completions``
— the wire format served by OpenAI, OpenAI-compatible Azure endpoints, vLLM,
Ollama, OpenRouter and the LiteLLM proxy. Transport only: retry, circuit
breaker, guardrail and metrics come from ``LLMService``.

The internal message formats stay the existing ones (spec 28 Decision 6):
Anthropic Messages-style for ``invoke`` and Bedrock Converse-style for
``converse``. The pure translation functions below convert at this edge.

Security: the API key is read from ``OPENAI_API_KEY_FILE`` (preferred) or
``OPENAI_API_KEY``, is only ever placed in the request header, and never
appears in logs or exception messages.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.core.config import settings
from src.core.llm.service import LLMService
from src.core.llm.types import ErrorClass, LLMProviderError, LLMResult
from src.core.logger import logger
from src.core.model_tier import estimate_tokens

PROVIDER_NAME = "openai"

# finish_reason (Chat Completions) → stop_reason (internal / Converse vocabulary).
_STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "content_filtered",
}

# Only bounded, printable error codes reach logs/exceptions (log-injection safe).
_SAFE_CODE = re.compile(r"[^A-Za-z0-9_.\-]")
_CODE_SEPARATORS = re.compile(r"[_.\-]")
# Leading segments used by common credential formats (sk-…, pk-…, rk-…, key-…).
_KEY_PREFIXES = ("sk", "pk", "rk", "key", "bearer")
REDACTED_CODE = "redacted"

# Hosts reachable without TLS (spec 28 Decision 11): loopback, in-cluster
# Service DNS and single-label names (compose service / K8s same-namespace).
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
_CLUSTER_SUFFIXES = (".svc", ".svc.cluster.local")


def _strip_bearer(value: str) -> str:
    value = value.strip()
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return value


def load_api_key() -> str:
    """Return the configured API key ('' when unset). Never logged.

    ``OPENAI_API_KEY_FILE`` (file-mounted secret) wins over ``OPENAI_API_KEY``.
    A leading ``Bearer `` is stripped (the header adds it back). An unreadable
    key file raises ``RuntimeError`` naming the path only.
    """
    key_file = settings.openai_api_key_file
    if key_file:
        try:
            return _strip_bearer(Path(key_file).read_text(encoding="utf-8"))
        except OSError as e:
            raise RuntimeError(f"OPENAI_API_KEY_FILE is not readable: {key_file}") from e
    return _strip_bearer(settings.openai_api_key or "")


def plaintext_host_allowed(base_url: str) -> bool:
    """True when ``base_url`` uses TLS, or plain http to a local/in-cluster host."""
    parts = urlsplit(base_url.strip())
    if parts.scheme == "https":
        return True
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in _LOOPBACK_HOSTS or host.endswith(_CLUSTER_SUFFIXES):
        return True
    return "." not in host and ":" not in host


def chat_completions_url(base_url: str) -> str:
    """``{base_url}/chat/completions``, preserving a query string (``?api-version=``)."""
    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/") + "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _looks_like_secret(code: str) -> bool:
    """Heuristic deny for credential-shaped error codes (Decision 11).

    Real codes are short words (``invalid_api_key``, ``rate_limit_exceeded``,
    ``429``). Refused: a key-style leading segment (``sk-…``), any segment
    longer than 32 chars, or a ≥8-char segment mixing letters and digits.
    """
    segments = [s for s in _CODE_SEPARATORS.split(code) if s]
    if len(segments) > 1 and segments[0].lower() in _KEY_PREFIXES:
        return True
    for seg in segments:
        if len(seg) > 32:
            return True
        if len(seg) >= 8 and any(c.isalpha() for c in seg) and any(c.isdigit() for c in seg):
            return True
    return False


def safe_error_code(raw: Any) -> Optional[str]:
    """Sanitize a server-provided error code. Never returns the API key.

    ``None`` when empty; ``"redacted"`` when it contains the loaded key (or is a
    fragment of it) or looks like any credential; otherwise the sanitized code.
    """
    if raw is None:
        return None
    code = _SAFE_CODE.sub("", str(raw))[:64]
    if not code:
        return None
    try:
        key = load_api_key()
    except RuntimeError:
        key = ""
    if len(key) >= 4:
        clean_key = _SAFE_CODE.sub("", key)
        # A fragment of the key (≥8 chars) is still sensitive; short codes like
        # "429" may coincide with key characters by chance, so they are kept.
        if key in str(raw) or (clean_key and (clean_key in code or (len(code) >= 8 and code in clean_key))):
            return REDACTED_CODE
    if _looks_like_secret(code):
        return REDACTED_CODE
    return code


def error_class_for_status(status_code: int) -> ErrorClass:
    """HTTP status → error class. 408/429/5xx are retryable; 3xx/other 4xx are not."""
    if status_code == 429:
        return ErrorClass.THROTTLED
    if status_code == 408:
        return ErrorClass.TIMEOUT
    if status_code >= 500:
        return ErrorClass.SERVER_ERROR
    return ErrorClass.CLIENT_ERROR


def _error_code(response: httpx.Response) -> Optional[str]:
    """Extract the server's error ``code``/``type`` (sanitized, ≤64 chars)."""
    try:
        data = response.json()
    except ValueError:
        return None
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return None
    code = err.get("code")
    err_type = err.get("type")
    # llama.cpp (and some proxies) put the HTTP status in ``code`` (e.g. 400) and the
    # meaningful reason in ``type`` (e.g. "exceed_context_size_error"). A numeric code
    # only repeats the status, so prefer ``type`` then (spec 28 Task 16).
    if err_type and (code is None or isinstance(code, int) or str(code).strip().isdigit()):
        return safe_error_code(err_type)
    return safe_error_code(code or err_type)


def _join_text(content: Any) -> str:
    """Flatten message content (str or list of text blocks) into one string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif "guardContent" in block:
            # Bedrock input-tagging wrapper — the text is the payload.
            text = block["guardContent"].get("text", {}).get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(p for p in parts if p)


def anthropic_to_chat_messages(
    messages: List[Dict[str, Any]], system_prompt: str,
) -> List[Dict[str, Any]]:
    """Anthropic Messages-style history → Chat Completions ``messages``."""
    chat: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        chat.append({"role": role, "content": _join_text(m.get("content", ""))})
    return chat


def _tool_result_text(tool_result: Dict[str, Any]) -> str:
    parts: List[str] = []
    for item in tool_result.get("content", []) or []:
        if not isinstance(item, dict):
            continue
        if "text" in item:
            parts.append(str(item["text"]))
        elif "json" in item:
            parts.append(json.dumps(item["json"], default=str))
    text = "\n".join(parts)
    if tool_result.get("status") == "error":
        text = f"[tool error] {text}"
    return text


def converse_to_chat_messages(
    messages: List[Dict[str, Any]], system_prompt: str,
) -> List[Dict[str, Any]]:
    """Bedrock Converse-style history → Chat Completions ``messages``.

    toolResult blocks become ``role=tool`` messages placed BEFORE any user text
    of the same turn (they must directly follow the assistant ``tool_calls``).
    """
    chat: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        role = m.get("role")
        content = m.get("content", [])
        if isinstance(content, str):
            content = [{"text": content}]
        if role == "user":
            texts: List[Dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and "toolResult" in block:
                    tr = block["toolResult"]
                    chat.append({
                        "role": "tool",
                        "tool_call_id": str(tr.get("toolUseId", "")),
                        "content": _tool_result_text(tr),
                    })
                else:
                    texts.append(block)
            text = _join_text(texts)
            if text:
                chat.append({"role": "user", "content": text})
        elif role == "assistant":
            tool_calls: List[Dict[str, Any]] = []
            text_blocks: List[Dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and "toolUse" in block:
                    tu = block["toolUse"]
                    tool_calls.append({
                        "id": str(tu.get("toolUseId", "")),
                        "type": "function",
                        "function": {
                            "name": str(tu.get("name", "")),
                            "arguments": json.dumps(tu.get("input", {}), default=str),
                        },
                    })
                else:
                    text_blocks.append(block)
            text = _join_text(text_blocks)
            msg: Dict[str, Any] = {"role": "assistant", "content": text or (None if tool_calls else "")}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            chat.append(msg)
    return chat


def converse_to_chat_tools(tool_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Converse ``toolConfig`` → ``{"tools": [...], "tool_choice": ...}`` body fields."""
    if not tool_config:
        return {}
    tools: List[Dict[str, Any]] = []
    for tool in tool_config.get("tools", []) or []:
        spec = tool.get("toolSpec") if isinstance(tool, dict) else None
        if not isinstance(spec, dict):
            continue
        function: Dict[str, Any] = {
            "name": spec.get("name", ""),
            "parameters": (spec.get("inputSchema") or {}).get("json", {"type": "object", "properties": {}}),
        }
        if spec.get("description"):
            function["description"] = spec["description"]
        tools.append({"type": "function", "function": function})
    if not tools:
        return {}
    fields: Dict[str, Any] = {"tools": tools}
    choice = tool_config.get("toolChoice")
    if isinstance(choice, dict):
        if "any" in choice:
            fields["tool_choice"] = "required"
        elif "auto" in choice:
            fields["tool_choice"] = "auto"
        elif isinstance(choice.get("tool"), dict):
            fields["tool_choice"] = {"type": "function", "function": {"name": choice["tool"].get("name", "")}}
    return fields


_usage_missing_warned = False


def _estimate_usage(
    request_messages: Optional[List[Dict[str, Any]]], content: List[Dict[str, Any]],
) -> tuple[int, int]:
    """Char-based token estimate (same estimator as the token budget)."""
    input_tokens = estimate_tokens(json.dumps(request_messages or [], default=str))
    output_text = "".join(
        b["text"] if b.get("type") == "text" else json.dumps(b.get("input", {}), default=str) + str(b.get("name", ""))
        for b in content
    )
    return input_tokens, estimate_tokens(output_text)


def parse_chat_completion(
    data: Dict[str, Any],
    model_id: str,
    request_messages: Optional[List[Dict[str, Any]]] = None,
) -> LLMResult:
    """Chat Completions response → ``LLMResult`` (normalized content blocks).

    - A tool call without an ``id`` gets a synthesized, unique one
      (``call_{i}_{hex8}``) so parallel results stay correlated and the next
      turn's ``role=tool`` messages reference a valid id.
    - A response without token ``usage`` falls back to the char-based
      estimator (``request_messages`` in, content out) — never 0, so the
      session token budget and the agentic loop token cap keep working. A
      one-shot WARNING ``llm_usage_missing`` is logged.
    """
    global _usage_missing_warned
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMProviderError(PROVIDER_NAME, ErrorClass.MALFORMED, code="no_choices")
    choice = choices[0]
    message = choice.get("message") or {}

    content: List[Dict[str, Any]] = []
    text = _join_text(message.get("content") or "")
    if text:
        content.append({"type": "text", "text": text})

    tool_calls = message.get("tool_calls") or []
    for i, call in enumerate(tool_calls):
        function = call.get("function") or {}
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            logger.warning("LLM returned malformed tool arguments", extra={
                "provider": PROVIDER_NAME, "tool": function.get("name", ""),
            })
            args = {}
        content.append({
            "type": "tool_use",
            "toolUseId": str(call.get("id") or "").strip() or f"call_{i}_{uuid.uuid4().hex[:8]}",
            "name": str(function.get("name", "")),
            "input": args if isinstance(args, dict) else {},
        })

    finish = choice.get("finish_reason") or "stop"
    stop_reason = "tool_use" if tool_calls else _STOP_REASONS.get(finish, "end_turn")

    raw_usage = data.get("usage")
    usage: Dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    details = usage.get("prompt_tokens_details") or {}
    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    usage_estimated = "prompt_tokens" not in usage and "completion_tokens" not in usage
    if usage_estimated:
        input_tokens, output_tokens = _estimate_usage(request_messages, content)
        if not _usage_missing_warned:
            _usage_missing_warned = True
            logger.warning(
                "llm_usage_missing — the endpoint returned no token usage; using the "
                "char-based estimate for budgets and cost (logged once per process)",
                extra={"provider": PROVIDER_NAME, "model_id": model_id},
            )
    return LLMResult(
        model=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=int(details.get("cached_tokens", 0) or 0),
        content=content,
        stop_reason=stop_reason,
        usage_estimated=usage_estimated,
    )


class OpenAICompatClient(LLMService):
    """LLMService bound to an OpenAI-compatible Chat Completions endpoint."""

    provider_name = PROVIDER_NAME
    display_name = "OpenAI-compatible LLM"

    def __init__(
        self,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else settings.openai_base_url or "").strip()
        # trust_env=False: HTTP(S)_PROXY / NETRC / SSL_CERT_* from the environment
        # are NOT honored — the LLM endpoint (and the key sent to it) is exactly
        # OPENAI_BASE_URL, never silently rerouted through an env-configured proxy.
        # Redirects are not followed either (a 3xx is a client_error).
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(
                float(settings.bedrock_read_timeout_seconds),
                connect=float(settings.bedrock_connect_timeout_seconds),
            ),
            trust_env=False,
            follow_redirects=False,
        )
        super().__init__("openai")

    def close(self) -> None:
        """Release the pooled HTTP connections (supervisor lifespan shutdown)."""
        self._http.close()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = load_api_key()
        if key:
            header = (settings.openai_api_key_header or "").strip() or "Authorization"
            headers[header] = f"Bearer {key}" if header.lower() == "authorization" else key
        return headers

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self._base_url:
            raise RuntimeError("OPENAI_BASE_URL is not set (LLM_PROVIDER=openai)")
        response = self._http.post(
            chat_completions_url(self._base_url), json=body, headers=self._headers(),
        )
        if 300 <= response.status_code < 400:
            # Not followed on purpose: a redirect could carry the key elsewhere.
            raise LLMProviderError(
                PROVIDER_NAME, ErrorClass.CLIENT_ERROR,
                status_code=response.status_code, code="redirect",
            )
        if response.status_code >= 400:
            raise LLMProviderError(
                PROVIDER_NAME,
                error_class_for_status(response.status_code),
                status_code=response.status_code,
                code=_error_code(response),
            )
        try:
            data = response.json()
        except ValueError as e:
            raise LLMProviderError(
                PROVIDER_NAME, ErrorClass.MALFORMED,
                status_code=response.status_code, code="invalid_json",
            ) from e
        if not isinstance(data, dict):
            raise LLMProviderError(PROVIDER_NAME, ErrorClass.MALFORMED, code="not_an_object")
        return data

    def _body(
        self, model_id: str, chat_messages: List[Dict[str, Any]], max_tokens: int, temperature: float,
    ) -> Dict[str, Any]:
        return {
            "model": model_id,
            "messages": chat_messages,
            "temperature": temperature,
            settings.openai_max_tokens_field or "max_tokens": max_tokens,
        }

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model_id: str,
        use_cache: bool,
    ) -> LLMResult:
        # use_cache: Anthropic prompt caching is Bedrock-only (Decision 12) — no-op.
        chat = anthropic_to_chat_messages(messages, system_prompt)
        body = self._body(model_id, chat, max_tokens, temperature)
        return parse_chat_completion(self._post(body), model_id, chat)

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
        # apply_provider_guardrail: no server-side guardrail here (L1 inactive,
        # Decision 10); the app-level L2–L6 layers run regardless.
        chat = converse_to_chat_messages(messages, system_prompt)
        body = self._body(model_id, chat, max_tokens, temperature)
        body.update(converse_to_chat_tools(tool_config))
        return parse_chat_completion(self._post(body), model_id, chat)

    def _error_code(self, exc: BaseException) -> Optional[str]:
        """Sanitized server error code for LOGS (spec 28 Task 16). The code was
        extracted onto ``LLMProviderError.code`` but the base hook returned None,
        so retry/error logs always showed ``error_code: None``."""
        return exc.code if isinstance(exc, LLMProviderError) else None

    def _classify_error(self, exc: BaseException, model_id: str) -> Optional[ErrorClass]:
        if isinstance(exc, LLMProviderError):
            return exc.error_class
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return ErrorClass.TIMEOUT
        return None
