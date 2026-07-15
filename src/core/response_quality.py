"""ResponseQualityGuard — structural quality defect detection (spec 35, T1).

Scans the model's response text for two proven defect classes that shipped to
the real cluster and were caught only by ad-hoc human reading (F-001, F-002,
F-003): tool-call scaffolding leaking into the answer, and raw adapter/
infrastructure error text reaching the user verbatim instead of a clean
summary. If detected, the response is refused (fail-closed), same as
`OutputFilter` (L4) — unlike `CanaryGuard` (L5, redact-and-continue since
F-005), neither defect class is ever legitimate content under any
circumstance, so there is no benign-false-positive case to preserve
availability for.

Design choices:
- Patterns target the literal signatures observed live, not broad keywords
  (e.g. matching our own adapters' `f"[svc] error: {e}"` prefix format,
  rather than the bare word "error", which a legitimate advisory answer can
  say honestly) — precise enough to avoid flagging a normal answer that
  merely *discusses* an access-denied situation in prose.
- Patterns are compiled once at import time (performance, matches
  `output_filter.py`/`canary.py`).
- Reuses `GuardrailBlockedError` so existing entrypoints (HTTP 403 mapping)
  work unchanged.
- Audit log never includes the full leaked text — only the matched category
  and a digest, same invariant as `output_filter.py`.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Tuple

from src.core.config import settings
from src.core.guardrail import GuardrailBlockedError
from src.core.logger import logger
from src.core.metrics import quality_violations, ungrounded_numeric_claims

# --- Compiled patterns (module-level, compiled once) ---

_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    # Tool-call scaffolding syntax leaking into the answer (F-001: aws agent
    # hallucinated <use_mcp_tool> instead of producing a normal answer).
    ("tool_scaffolding", re.compile(
        r"</?(?:use_mcp_tool|tool_call|function_calls|invoke)\b", re.IGNORECASE
    )),
    # Our own adapters' error-prefix format (src/core/adapters.py:
    # f"[{svc}] error: {e}", f"[mcp:{name}] error: {e}", etc.) — if this
    # literal bracketed prefix appears in the final answer, the raw adapter
    # failure text leaked through unsummarized (F-002/F-003). Precise: no
    # legitimate prose naturally produces "[ec2] error:" as a substring.
    ("raw_adapter_error", re.compile(r"\[[\w:.\-]+\]\s*error:", re.IGNORECASE)),
    # Raw Python/boto3 exception signatures — never legitimate in a
    # business-facing answer, regardless of agent or phrasing.
    ("raw_traceback", re.compile(r"Traceback \(most recent call last\):")),
    ("raw_botocore_exception", re.compile(r"botocore\.exceptions\.\w+")),
    ("raw_boto3_error_string", re.compile(
        r"An error occurred \(\w+(?:Exception|Error)\) when calling"
    )),
    ("raw_taskgroup_exception", re.compile(r"unhandled errors in a TaskGroup")),
]

_MIN_RESPONSE_LENGTH = 10

# --- Groundedness (spec 35 requirements.md PR-05) ---
#
# The quality twin of the canary check: canary catches context data that
# SHOULDN'T leave; groundedness catches claims that never came IN — a
# numeric value or resource ID the model states as fact but that isn't
# anywhere in the infra_data it was actually given (fabrication, same root
# cause as F-001's hallucinated tool-call XML, just numeric instead of
# structural).
#
# Two different confidence levels, two different responses:
# - Resource IDs (instance/volume/security-group/snapshot/subnet/VPC/AMI
#   IDs, ARNs) are NEVER legitimately "derived" — an agent either saw a
#   real ID in infra_data or it invented one. Safe to hard-block, same as
#   the other T1 patterns.
# - Bare numeric claims (dollar amounts, counts) CAN be legitimate derived
#   values (a sum, an average, a rounding) that won't appear verbatim in
#   infra_data even though the underlying data fully supports them.
#   Hard-blocking here risks denying a correct answer that did real
#   arithmetic — same false-positive-vs-availability tradeoff already
#   decided for the canary (F-005), so this is metric-only (audit +
#   `aigent.quality.ungrounded_numeric_claims`), never
#   `GuardrailBlockedError`.
_RESOURCE_ID_PATTERN = re.compile(
    r"\b(?:i|vol|sg|snap|subnet|vpc|ami|eni|nat|igw|rtb|acl|vpce)-[0-9a-f]{8,17}\b"
    r"|arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:\d{12}:[\w\-/:.*]+",
    re.IGNORECASE,
)
# Dollar amounts with cents (the shape of a specific queried figure, not a
# vague "a few hundred dollars" prose estimate) — 2+ digit whole part to
# skip trivial/rounded numbers less likely to be a meaningful fabrication.
_NUMERIC_CLAIM_PATTERN = re.compile(r"\$\d{1,3}(?:,\d{3})*\.\d{2}\b")


def _digest(text: str) -> str:
    """Non-reversible short digest for audit (never log the matched content)."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


class ResponseQualityGuard:
    """Scan model response for structural quality defects. Fail-closed on detection.

    Usage (inside GenericAgent.process_request):
        guard = ResponseQualityGuard()
        guard.scan(response_text, agent_id, user_id, session_id)
    """

    def __init__(self) -> None:
        self.enabled: bool = settings.response_quality_enabled

    def scan(
        self,
        response: str,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
        infra_data: str = "",
    ) -> None:
        """Scan response for structural quality defects. Raise on detection.

        If any pattern matches, the response is blocked (fail-closed). Raises
        ``GuardrailBlockedError`` with category ``quality:<pattern_name>``.
        A fabricated resource ID (``quality:ungrounded_resource_id``) is
        block-worthy the same way — see module-level groundedness comment.

        ``infra_data`` is optional (defaults to ``""``, which makes every
        resource ID in the response "ungrounded" by construction — callers
        that don't pass it get NO groundedness checking, not a false-positive
        flood): pass the same infra_data the response was generated from to
        enable it.

        Does nothing if disabled or response is too short to contain a defect.
        """
        if not self.enabled:
            return

        if len(response) < _MIN_RESPONSE_LENGTH:
            return

        detected = self._find_defects(response)
        categories = [f"quality:{name}" for name, _ in detected]

        if infra_data:
            ungrounded_ids = self._find_ungrounded_resource_ids(response, infra_data)
            if ungrounded_ids:
                categories.append("quality:ungrounded_resource_id")

            self._check_numeric_groundedness(response, infra_data, agent_id)

        if categories:
            for category in categories:
                quality_violations.add(1, {"agent_id": agent_id, "category": category.removeprefix("quality:")})
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

    @staticmethod
    def _find_ungrounded_resource_ids(response: str, infra_data: str) -> List[str]:
        """Resource IDs stated in the response that don't appear anywhere in
        infra_data — never legitimate (an ID is either real or invented)."""
        return [
            match for match in _RESOURCE_ID_PATTERN.findall(response)
            if match.lower() not in infra_data.lower()
        ]

    @staticmethod
    def _check_numeric_groundedness(response: str, infra_data: str, agent_id: str) -> None:
        """Metric-only (non-blocking) — see module-level groundedness comment
        for why dollar-figure claims aren't hard-blocked like resource IDs."""
        for claim in _NUMERIC_CLAIM_PATTERN.findall(response):
            if claim not in infra_data:
                ungrounded_numeric_claims.add(1, {"agent_id": agent_id})

    def _find_defects(self, text: str) -> List[Tuple[str, str]]:
        """Return list of (pattern_name, matched_text) for all detections.

        Stops at first match per pattern (no need to find all occurrences).
        The matched text is used only for the digest — never logged verbatim.
        """
        found: List[Tuple[str, str]] = []
        for name, pattern in _PATTERNS:
            match = pattern.search(text)
            if match:
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
        """Structured audit log for response quality detection.

        Invariant: never logs the offending content — only categories and a digest.
        """
        logger.warning(
            "response quality: structural defect detected in response",
            extra={
                "audit": True,
                "event": "response_quality_block",
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
                "categories": categories,
                "response_digest": response_digest,
            },
        )
