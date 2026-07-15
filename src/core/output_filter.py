"""OutputFilter — PII/secret/credential leak detection in model responses (spec 14, L4).

Scans the model's response text for patterns that should never appear in an
AI-generated answer: AWS access keys, private keys, email addresses, and other
sensitive data. If detected, the response is refused (fail-closed) per spec 14
Decision 2.

Design choices:
- **Block, not redact**: redaction risks returning a mutilated but seemingly
  valid answer. Fail-closed (block the entire response) is consistent with the
  guardrail philosophy — the user gets a clear refusal, not corrupted output.
- Patterns are compiled once at import time (performance).
- Regex cardinality is bounded: each pattern is applied once over the response.
  Total cost is O(N * P) where N=response length, P=number of patterns (small).
- Reuses ``GuardrailBlockedError`` for HTTP 403 mapping consistency.
- Audit log follows the same invariant: never logs the matched secret in clear
  text — only the pattern category that triggered.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Tuple

from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.logger import logger

# --- Compiled patterns (module-level, compiled once) ---

_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    # AWS Access Key IDs (always start with AKIA/ASIA)
    ("aws_access_key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    # AWS Secret Access Keys: a 40-char base64-ish string, but ONLY when near an
    # AWS-secret indicator. A bare 40-char match is a false-positive magnet
    # (sha1, base64 blobs) — proximity to a key name keeps the catch, cuts noise.
    ("aws_secret_key", re.compile(
        r"(?i)(?:secret[_-]?access[_-]?key|aws[_-]?secret|secretkey)"
        r"['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9/+]{40}"
    )),
    # Private key blocks (PEM)
    ("private_key", re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
    )),
    # Generic API tokens/secrets (common env var patterns leaked verbatim)
    ("generic_secret", re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_\-]{20,}['\"]?"
    )),
    # Email addresses (simple but effective)
    ("email", re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    )),
    # Brazilian CPF (11 digits, formatted or not)
    ("cpf", re.compile(r"\b\d{3}[.\s]?\d{3}[.\s]?\d{3}[.\-\s]?\d{2}\b")),
    # Credit card numbers (13-19 digits, optionally separated). Regex is a
    # pre-filter only — a Luhn check (see _luhn_valid) confirms the match to
    # avoid firing on epoch timestamps / IDs / metric values.
    ("credit_card", re.compile(
        r"\b(?:\d[ \-]?){13,19}\b"
    )),
    # GitHub/GitLab personal access tokens
    ("github_token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}")),
    ("gitlab_token", re.compile(r"glpat-[A-Za-z0-9\-_]{20,}")),
]

# Pre-filter: skip scanning if the response is very short (no realistic leak)
_MIN_RESPONSE_LENGTH = 10


def _digest(text: str) -> str:
    """Non-reversible short digest for audit (never log the matched content)."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _luhn_valid(candidate: str) -> bool:
    """Luhn checksum — confirms a digit sequence is a plausible card number.

    Filters out epoch timestamps / IDs / metric values that the broad
    credit_card regex would otherwise flag (they almost never pass Luhn).
    """
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


class OutputFilter:
    """Scan model response for PII/secrets. Fail-closed on detection.

    Usage (inside GenericAgent.process_request):
        output_filter = OutputFilter()
        output_filter.scan(response_text, agent_id, user_id, session_id)
    """

    def __init__(self) -> None:
        self.enabled: bool = settings.output_filter_enabled

    def scan(
        self,
        response: str,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
    ) -> None:
        """Scan response for sensitive patterns. Raise on detection.

        If any pattern matches, the response is blocked (fail-closed). Raises
        ``GuardrailBlockedError`` with category ``leak:<pattern_name>``.

        Does nothing if disabled or response is too short to contain leaks.
        """
        if not self.enabled:
            return

        if len(response) < _MIN_RESPONSE_LENGTH:
            return

        detected = self._find_leaks(response)
        if detected:
            # Block on first detection — report all categories found.
            categories = [f"leak:{name}" for name, _ in detected]
            self._audit(
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                categories=categories,
                response_digest=_digest(response),
            )
            raise GuardrailBlockedError(
                reason="blocked",
                source="OUTPUT",
                categories=categories,
            )

    def _find_leaks(self, text: str) -> List[Tuple[str, str]]:
        """Return list of (pattern_name, matched_text) for all detections.

        Stops at first match per pattern (no need to find all occurrences).
        The matched text is used only for the digest — never logged verbatim.
        """
        found: List[Tuple[str, str]] = []
        for name, pattern in _PATTERNS:
            match = pattern.search(text)
            if match:
                # credit_card: confirm with Luhn to drop timestamps/IDs (FP).
                if name == "credit_card" and not _luhn_valid(match.group()):
                    continue
                found.append((name, match.group()))
        return found

    @staticmethod
    def _audit(
        agent_id: str,
        user_id: str,
        session_id: str,
        categories: List[str],
        response_digest: str,
    ) -> None:
        """Structured audit log for output filter detection.

        Invariant: never logs the leaked content — only categories and a digest.
        """
        logger.warning(
            "output filter: sensitive content detected in response",
            extra={
                "audit": True,
                "event": "output_filter_block",
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
                "categories": categories,
                "response_digest": response_digest,
            },
        )
