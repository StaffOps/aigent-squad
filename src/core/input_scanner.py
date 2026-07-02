"""InputScanner — pre-LLM normalization + cheap heuristics (spec 14, L2).

Runs BEFORE context construction and Bedrock Guardrail (L1). Canonicalizes
obfuscated text (unicode confusables, zero-width chars, homoglyphs) so that
downstream layers (Guardrail, prompt, model) see a clean, canonical form.
Cheap heuristics reject obviously malformed/junk input before spending a
Bedrock invoke.

Design choices:
- **Normalize**: NFKC + zero-width stripping + Cyrillic/Greek→Latin homoglyph
  folding. Ensures confusable-obfuscated injections are canonicalized so L1
  (Bedrock Guardrail) sees the de-obfuscated text.
- **Cheap reject**: control-char density, repeated-char abuse, base64-encoded
  large blobs (inspected for injection markers but NEVER expanded into context).
  All O(N) or constant-time checks — no model calls.
- **Fail-closed** (spec 14, Decision 2): if the scanner itself errors, the
  request is refused rather than forwarded unscanned. Security > availability.
- **Audit invariant**: never logs the raw payload — only a sha256[:12] digest
  for correlation (mirrors guardrail.py and canary.py).
- Reuses ``GuardrailBlockedError`` for uniform HTTP 403 mapping.
"""
from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from typing import Optional

from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.logger import logger

# --- Zero-width + bidi-override characters to strip ---
# Zero-width: U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+FEFF BOM,
#   U+2060 WORD JOINER, U+180E MONGOLIAN VOWEL SEP, U+00AD SOFT HYPHEN.
# Bidi overrides (LOW-1): U+202A-U+202E (LRE/RLE/PDF/LRO/RLO) and
#   U+2066-U+2069 (LRI/RLI/FSI/PDI) — reverse/hide text direction to obfuscate
#   injection from human review; no legitimate use in a chat input.
_ZERO_WIDTH_RE = re.compile(
    r"[\u200b\u200c\u200d\ufeff\u2060\u180e\u00ad\u202a-\u202e\u2066-\u2069]+"
)

# --- Homoglyph map: Cyrillic/Greek lookalikes → Latin ---
# Only the most common confusables used in adversarial obfuscation.
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic → Latin
    "\u0410": "A",  # А
    "\u0412": "B",  # В
    "\u0421": "C",  # С
    "\u0415": "E",  # Е
    "\u041d": "H",  # Н
    "\u041a": "K",  # К
    "\u041c": "M",  # М
    "\u041e": "O",  # О
    "\u0420": "P",  # Р
    "\u0422": "T",  # Т
    "\u0425": "X",  # Х
    "\u0430": "a",  # а
    "\u0435": "e",  # е
    "\u043e": "o",  # о
    "\u0440": "p",  # р
    "\u0441": "c",  # с
    "\u0443": "y",  # у (maps to y visually)
    "\u0445": "x",  # х
    "\u0456": "i",  # і (Ukrainian i)
    "\u0458": "j",  # ј (Serbian je)
    # Greek → Latin
    "\u0391": "A",  # Α (Alpha)
    "\u0392": "B",  # Β (Beta)
    "\u0395": "E",  # Ε (Epsilon)
    "\u0397": "H",  # Η (Eta)
    "\u0399": "I",  # Ι (Iota)
    "\u039a": "K",  # Κ (Kappa)
    "\u039c": "M",  # Μ (Mu)
    "\u039d": "N",  # Ν (Nu)
    "\u039f": "O",  # Ο (Omicron)
    "\u03a1": "P",  # Ρ (Rho)
    "\u03a4": "T",  # Τ (Tau)
    "\u03a7": "X",  # Χ (Chi)
    "\u03b1": "a",  # α (alpha) — visual confusable in some fonts
    "\u03bf": "o",  # ο (omicron)
    "\u03c1": "p",  # ρ (rho) — looks like p in some fonts
}

# Pre-build translation table for str.translate (O(N) single pass)
_HOMOGLYPH_TABLE = str.maketrans(_HOMOGLYPH_MAP)

# --- Cheap heuristic thresholds ---
# Control characters: C0 (0x00-0x1F except \t\n\r) + C1 (0x80-0x9F) + DEL (0x7F)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]")
# If >5% of the input is control characters, reject.
_CONTROL_CHAR_RATIO_THRESHOLD = 0.05
# Minimum length to apply ratio check (very short strings skip this).
_CONTROL_CHAR_MIN_LENGTH = 20

# Repeated character abuse: same char repeated 50+ times in a row (padding/DoS).
_REPEATED_CHAR_RE = re.compile(r"(.)\1{49,}")

# Base64 blob detection: a contiguous block of base64 chars ≥ 200 chars long
# (a small inline token is fine; a big blob is suspicious).
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")

# Injection markers to scan FOR inside decoded base64 blobs.
# These are cheap literal checks — NOT the full L1 scanner. Just common
# prompt-injection preambles that attackers encode to bypass text filters.
# MUST be lowercase: the comparison target is decoded.lower() (see
# _check_base64_blobs), so an uppercase entry could never match.
_INJECTION_MARKERS = [
    "ignore previous",
    "ignore all",
    "disregard",
    "system prompt",
    "you are now",
    "new instructions",
    "override",
    "forget everything",
    "act as",
    "jailbreak",
    "dan",
    "bypass",
]

# Maximum input size. Coordinated with generic_agent's 10k limit — the scanner
# uses the SAME cap. If input exceeds this, generic_agent would reject it
# anyway, but the scanner rejects it earlier with a security-appropriate error
# (fail-closed via GuardrailBlockedError rather than ValueError).
_MAX_INPUT_LENGTH = 10_000


def _digest(text: str) -> str:
    """Short non-reversible correlation id (never log the payload itself)."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


class InputScanner:
    """Pre-LLM input normalization and cheap rejection heuristics.

    Usage (inside GenericAgent.process_request, at the very start):
        scanner = InputScanner()
        normalized = scanner.scan(input_text, agent_id, user_id, session_id)
        # Use normalized text downstream (replaces input_text).

    Returns the normalized text on success.
    Raises ``GuardrailBlockedError`` on rejection or internal error (fail-closed).
    """

    def __init__(self) -> None:
        self.enabled: bool = settings.input_scanner_enabled

    def scan(
        self,
        text: str,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
    ) -> str:
        """Normalize and validate input text. Fail-closed.

        Returns:
            The normalized text (NFKC + zero-width stripped + homoglyphs folded).

        Raises:
            GuardrailBlockedError: if the input is rejected by a heuristic OR
                if an internal error occurs (fail-closed on scanner failure).
        """
        if not self.enabled:
            return text

        try:
            return self._scan_internal(text, agent_id, user_id, session_id)
        except GuardrailBlockedError:
            # Already audited — re-raise.
            raise
        except Exception as exc:
            # Unexpected error in scanner → fail-closed (security > availability).
            self._audit(
                event="input_scanner_error",
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                text_digest=_digest(text) if text else "empty",
                error_type=type(exc).__name__,
            )
            raise GuardrailBlockedError(
                reason="unavailable",
                source="INPUT",
                categories=["scanner:internal_error"],
            ) from exc

    def _scan_internal(
        self,
        text: str,
        agent_id: str,
        user_id: str,
        session_id: str,
    ) -> str:
        """Core scan logic: normalize → heuristics → return normalized text."""
        # --- Heuristic: oversized input ---
        if len(text) > _MAX_INPUT_LENGTH:
            self._audit(
                event="input_scanner_block",
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                text_digest=_digest(text),
                reason="oversized",
                categories=["scanner:oversized"],
            )
            raise GuardrailBlockedError(
                reason="blocked",
                source="INPUT",
                categories=["scanner:oversized"],
            )

        # --- Normalize ---
        normalized = self._normalize(text)

        # --- Heuristic: excessive control characters ---
        if len(normalized) >= _CONTROL_CHAR_MIN_LENGTH:
            control_count = len(_CONTROL_RE.findall(normalized))
            ratio = control_count / len(normalized)
            if ratio > _CONTROL_CHAR_RATIO_THRESHOLD:
                self._audit(
                    event="input_scanner_block",
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                    text_digest=_digest(text),
                    reason="excessive_control_chars",
                    categories=["scanner:control_chars"],
                    control_ratio=round(ratio, 4),
                )
                raise GuardrailBlockedError(
                    reason="blocked",
                    source="INPUT",
                    categories=["scanner:control_chars"],
                )

        # --- Heuristic: repeated character abuse ---
        if _REPEATED_CHAR_RE.search(normalized):
            self._audit(
                event="input_scanner_block",
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                text_digest=_digest(text),
                reason="repeated_chars",
                categories=["scanner:repeated_chars"],
            )
            raise GuardrailBlockedError(
                reason="blocked",
                source="INPUT",
                categories=["scanner:repeated_chars"],
            )

        # --- Heuristic: base64 blob inspection ---
        self._check_base64_blobs(normalized, text, agent_id, user_id, session_id)

        return normalized

    def _normalize(self, text: str) -> str:
        """Apply normalization pipeline: NFKC → zero-width strip → homoglyph fold.

        The order matters:
        1. NFKC first — decomposes compatibility sequences (e.g. ﬁ→fi, ½→1/2)
           and recomposes canonical equivalents. Some zero-width chars survive.
        2. Strip zero-width characters.
        3. Fold homoglyphs (Cyrillic/Greek lookalikes → Latin).
        """
        # 1. Unicode NFKC normalization
        result = unicodedata.normalize("NFKC", text)
        # 2. Strip zero-width characters
        result = _ZERO_WIDTH_RE.sub("", result)
        # 3. Fold homoglyphs via translation table (single O(N) pass)
        result = result.translate(_HOMOGLYPH_TABLE)
        return result

    def _check_base64_blobs(
        self,
        normalized: str,
        original: str,
        agent_id: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """Detect large base64 blobs; decode and inspect for injection markers.

        IMPORTANT: the decoded content is inspected IN MEMORY only. It is NEVER
        returned or injected into the context/prompt. The scanner INSPECTS; it
        does not expand.
        """
        blobs = _BASE64_BLOB_RE.findall(normalized)
        for blob in blobs:
            decoded = self._try_decode_base64(blob)
            if decoded is None:
                continue  # Not valid base64 — just a long alphanumeric string.
            # Re-normalize the decoded content (NFKC + lower) before matching so
            # a recursively-obfuscated payload (base64 of fullwidth/homoglyph
            # text) is also caught — MEDIUM-1.
            decoded_norm = unicodedata.normalize("NFKC", decoded).lower()
            for marker in _INJECTION_MARKERS:
                # Word-boundary match: avoids false positives on short markers
                # (e.g. "dan" inside "abundant"/"Sudan") while still catching the
                # standalone jailbreak keyword.
                if re.search(rf"\b{re.escape(marker)}\b", decoded_norm):
                    self._audit(
                        event="input_scanner_block",
                        agent_id=agent_id,
                        user_id=user_id,
                        session_id=session_id,
                        text_digest=_digest(original),
                        reason="base64_injection",
                        categories=["scanner:base64_injection"],
                        marker_found=marker,
                    )
                    raise GuardrailBlockedError(
                        reason="blocked",
                        source="INPUT",
                        categories=["scanner:base64_injection"],
                    )

    @staticmethod
    def _try_decode_base64(blob: str) -> Optional[str]:
        """Attempt to decode a base64 blob. Returns decoded text or None.

        Adds padding if missing (common in adversarial inputs). Returns None if
        the blob is not valid base64 or the decoded bytes are not valid UTF-8.
        """
        # Add padding if needed
        padded = blob + "=" * (-len(blob) % 4)
        try:
            decoded_bytes = base64.b64decode(padded, validate=True)
            return decoded_bytes.decode("utf-8", errors="strict")
        except Exception:
            return None

    @staticmethod
    def _audit(
        event: str,
        agent_id: str,
        user_id: str,
        session_id: str,
        **extra,
    ) -> None:
        """Structured audit log (no raw payload — digest only)."""
        logger.warning(
            "input scanner event",
            extra={
                "audit": True,
                "event": event,
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
                **extra,
            },
        )
