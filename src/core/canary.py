"""CanaryGuard — exfiltration detection via canary token injection (spec 14, L5).

Injects per-request unique, cryptographically unpredictable tokens into
``infra_data`` before the model invoke. After the invoke, scans the model's
response for any injected canary. If found, the model leaked context verbatim
— an exfiltration signal — and the response is refused (fail-closed).

Design choices:
- Tokens are per-request 128-bit cryptographically random hex strings (via
  ``secrets``). They cannot be guessed by an attacker crafting output.
- Detection is exact-match AND fuzzy: the hex suffix is also matched with common
  obfuscations stripped (whitespace/separators inserted between chars), so an
  injection that says "reproduce refs but replace '-' with ' '" is still caught.
- Tokens are NEVER logged in clear text (same invariant as guardrail payloads).
  Only a sha256[:12] digest is logged for correlation.
- Multiple canaries are injected (head/tail) to detect partial leaks.
- Reuses ``GuardrailBlockedError`` so existing entrypoints (HTTP 403 mapping)
  work unchanged.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import List, Tuple

from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.logger import logger

# Prefix aids internal correlation; the entropy is in the random suffix.
# NOTE: the prefix is recognizable — a sophisticated injection could try to
# suppress "CNRY-"-prefixed strings. The fuzzy hex match below is the real
# defense; Phase 4 (multi-language attack suite) should probe prompt-level
# canary suppression.
_CANARY_PREFIX = "CNRY-"
_TOKEN_BYTES = 16  # 16 bytes -> 32 hex chars -> 128 bits of entropy per token
_NUM_CANARIES = 2  # head and tail injection
# Fuzzy matcher: the 32 hex chars of a token with optional separators (space,
# -, _, ., zero-width) allowed BETWEEN characters. Catches trivial obfuscation
# ("replace - with space", "insert dots") that defeats exact substring match.
_SEP = r"[\s\-_.\u200b]*"


def _generate_token() -> str:
    """Generate a single canary token: prefix + 128-bit random hex."""
    return f"{_CANARY_PREFIX}{secrets.token_hex(_TOKEN_BYTES)}"


def _fuzzy_pattern(token: str) -> re.Pattern[str]:
    """Build a regex that matches the token's hex suffix even if an attacker
    inserted separators between characters (obfuscation bypass, HIGH-1).

    Matches the raw hex chars with optional separators between each — so
    'a1b2...' also matches 'a 1 b 2', 'a-1-b-2', 'a.1.b.2', zero-width joined.
    """
    hex_suffix = token[len(_CANARY_PREFIX):]
    return re.compile(_SEP.join(re.escape(c) for c in hex_suffix), re.IGNORECASE)


def _digest(token: str) -> str:
    """Non-reversible short digest for audit logging (never log the token)."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class CanaryGuard:
    """Inject canary tokens into context; detect them in model output.

    Usage (inside GenericAgent.process_request):
        guard = CanaryGuard()
        infra_data, tokens = guard.inject(infra_data)
        ...  # invoke model
        guard.detect(response_text, tokens, agent_id, user_id, session_id)
    """

    def __init__(self) -> None:
        self.enabled: bool = settings.canary_enabled

    def inject(self, infra_data: str) -> Tuple[str, List[str]]:
        """Inject canary tokens into infra_data. Returns (modified_data, tokens).

        Tokens are placed as hidden markers that a well-behaved model should
        never reproduce verbatim. An instruction-following model will not echo
        opaque hex tokens unless manipulated by an injection.

        If disabled, returns the original data unchanged with an empty list.
        """
        if not self.enabled:
            return infra_data, []

        tokens = [_generate_token() for _ in range(_NUM_CANARIES)]

        # Embed canaries as data-context markers. They look like internal
        # correlation IDs — innocuous to the model but detectable on output.
        head_marker = f"[session-ref: {tokens[0]}]"
        tail_marker = f"[trace-ref: {tokens[1]}]"

        injected = f"{head_marker}\n{infra_data}\n{tail_marker}"
        return injected, tokens

    def detect(
        self,
        response: str,
        tokens: List[str],
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
    ) -> None:
        """Scan model response for canary tokens. Raise on detection.

        If any injected token appears in the response, it means the model
        reproduced context verbatim — an exfiltration signal. Fail-closed:
        raises ``GuardrailBlockedError`` (mapped to HTTP 403 by entrypoints).

        Does nothing if disabled or if no tokens were injected.
        """
        if not self.enabled or not tokens:
            return

        for token in tokens:
            # Exact match first (cheap), then fuzzy (catches separator-based
            # obfuscation that defeats a literal substring search — HIGH-1).
            if token in response or _fuzzy_pattern(token).search(response):
                self._audit(
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                    token_digest=_digest(token),
                )
                raise GuardrailBlockedError(
                    reason="blocked",
                    source="OUTPUT",
                    categories=["exfiltration:canary_leak"],
                )

    @staticmethod
    def _audit(
        agent_id: str,
        user_id: str,
        session_id: str,
        token_digest: str,
    ) -> None:
        """Structured audit log for canary leak detection.

        Invariant: never logs the canary token in clear text (only digest).
        """
        logger.warning(
            "canary exfiltration detected",
            extra={
                "audit": True,
                "event": "canary_leak",
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
                "token_digest": token_digest,
            },
        )
