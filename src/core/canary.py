"""CanaryGuard — exfiltration detection via canary token injection (spec 14, L5).

Injects per-request unique, cryptographically unpredictable tokens into
``infra_data`` before the model invoke. After the invoke, scans the model's
response for any injected canary. If found: audited as an exfiltration
signal AND redacted from the response, which is then returned to the user
(NOT blocked).

Redact-and-continue, not block (revised 2026-07-13, F-005 — a deliberate
product/security decision, not the spec-14 default posture for the other
layers): live testing found a real, non-trivial false-positive rate on
ordinary benign responses — Bedrock's own learned habit of appending a
"Session:"/"Trace:" footer to thorough answers, grabbing the canary token
(the only opaque-hex value in context) to fill it, no injection or malicious
intent involved. Hard-blocking denied a legitimate answer every time this
fired. Redaction keeps the audit signal (still logged, still visible to
security monitoring) without denying real answers on a false positive — the
token itself is single-use and worthless once it's out of the response text,
so redacting it costs nothing from a security standpoint. A genuine
exfiltration attempt is still caught and still loses its payload (the leaked
context is stripped before the user sees it); what changes is that the REST
of a legitimate, otherwise-fine answer is no longer denied along with it.

Design choices:
- Tokens are per-request 128-bit cryptographically random hex strings (via
  ``secrets``). They cannot be guessed by an attacker crafting output.
- Detection is exact-match AND fuzzy: the hex suffix is also matched with common
  obfuscations stripped (whitespace/separators inserted between chars), so an
  injection that says "reproduce refs but replace '-' with ' '" is still caught.
- Tokens are NEVER logged in clear text (same invariant as guardrail payloads).
  Only a sha256[:12] digest is logged for correlation.
- Multiple canaries are injected (head/tail) to detect partial leaks.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from typing import List, Tuple

from src.core.config import settings
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
    """Inject canary tokens into context; detect + redact them in model output.

    Usage (inside GenericAgent.process_request):
        guard = CanaryGuard()
        infra_data, tokens = guard.inject(infra_data)
        ...  # invoke model
        response_text = guard.detect(response_text, tokens, agent_id, user_id, session_id)
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

        # Embed canaries as HTML-comment-style annotations, not a "[field:
        # value]" line. Found live (2026-07-13, F-005): a "[session-ref: ...]"
        # / later "[internal-marker: ...]" line — even paired with an explicit
        # "never repeat this" instruction placed OUTSIDE <infra_data> in
        # generic_agent.py's context template — still got echoed verbatim as
        # a self-invented "Session Reference"/"Trace" footer in ~25% of real
        # trials (Claude Sonnet, aws agent). The model wasn't copying the
        # marker's own wording — it was pattern-matching "opaque hex string
        # near the top/bottom of a data block" to its learned prior that
        # professional technical responses cite a correlation/session ID, and
        # synthesized its own label regardless of what the field was called or
        # what the instruction said. An HTML-comment shape reads as inert
        # backstage tooling noise (not a display field) to models trained
        # heavily on code/markup, which should weaken that prior. This does
        # NOT change the security model for a real exfiltration attempt: the
        # exact+fuzzy detection below is unaffected by marker wording, and an
        # adversarial injection still has to override the instruction
        # hierarchy regardless of how the canary is formatted.
        head_marker = f"<!-- internal-telemetry-id, do not output: {tokens[0]} -->"
        tail_marker = f"<!-- internal-telemetry-id, do not output: {tokens[1]} -->"

        injected = f"{head_marker}\n{infra_data}\n{tail_marker}"
        return injected, tokens

    def detect(
        self,
        response: str,
        tokens: List[str],
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
    ) -> str:
        """Scan model response for canary tokens. Audit + redact on detection.

        If any injected token appears in the response, it means the model
        reproduced context verbatim — audited as an exfiltration signal, and
        the leaked value is redacted from the response (see module docstring
        for why this is redact-and-continue, not block, as of 2026-07-13).

        Returns the response unchanged if disabled, no tokens were injected,
        or nothing leaked; otherwise returns the response with the leaked
        value(s) replaced by ``[redacted]``.
        """
        if not self.enabled or not tokens:
            return response

        for token in tokens:
            pattern = _fuzzy_pattern(token)
            # Exact match first (cheap) to decide whether to audit; the
            # actual redaction always goes through the fuzzy pattern since it
            # is a superset (catches separator-obfuscated leaks too — HIGH-1).
            if token in response or pattern.search(response):
                self._audit(
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                    token_digest=_digest(token),
                )
                # _fuzzy_pattern only spans the hex suffix, not the "CNRY-"
                # prefix — redact that too (with the same separator
                # tolerance) so no residue signals "a canary was here" to
                # whoever reads the response.
                response = pattern.sub("[redacted]", response)
                response = re.sub(
                    re.escape(_CANARY_PREFIX[:-1]) + _SEP + r"\[redacted\]",
                    "[redacted]", response, flags=re.IGNORECASE,
                )
        return response

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
