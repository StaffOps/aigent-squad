"""Bedrock Guardrail client — primary anti-prompt-injection layer (spec 14, L1).

The guardrail is evaluated independently of the agent's prompt via the
``apply_guardrail`` API. An injection that fools the LLM does not fool the
guardrail (distinct evaluations) — real defense-in-depth, not the same layer
twice.

Fail-closed (spec 14, Decision 2): if the guardrail blocks the content OR the
guardrail service is unavailable/misconfigured, the request is REFUSED
(``GuardrailBlockedError``), never bypassed. Security is prioritized over
availability because the product is consultative (read-only) — if it goes down,
the operator still has their own tools.

The audit log (Task 4) records every detection/refusal WITHOUT the malicious
payload in clear text (avoids log injection / re-exposure). A short sha256
prefix correlates an event to its input without revealing it.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.core.config import settings
from src.core.logger import logger
from src.core.metrics import guardrail_blocks

# apply_guardrail response actions
_ACTION_INTERVENED = "GUARDRAIL_INTERVENED"

# Guardrail evaluation sources
SOURCE_INPUT = "INPUT"
SOURCE_OUTPUT = "OUTPUT"


class GuardrailBlockedError(Exception):
    """Raised when the guardrail refuses a request (fail-closed).

    Two reasons, both fail-closed:
    - ``"blocked"``: the guardrail actively intervened (prompt attack, denied
      topic, PII, harmful content).
    - ``"unavailable"``: the guardrail service errored or is misconfigured;
      we refuse rather than invoke the model without protection.

    Carries no malicious payload — only the reason, source, and detected
    categories — so it is safe to surface and log.
    """

    def __init__(self, reason: str, source: str, categories: Optional[List[str]] = None):
        self.reason = reason  # "blocked" | "unavailable"
        self.source = source  # "INPUT" | "OUTPUT"
        self.categories = categories or []
        super().__init__(
            f"Guardrail {reason} on {source}"
            + (f" ({', '.join(self.categories)})" if self.categories else "")
        )


def _digest(text: str) -> str:
    """Short, non-reversible correlation id for an evaluated payload.

    Lets an audit event be tied back to a specific input during investigation
    without storing the input itself (no cleartext payload in logs).
    """
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _extract_categories(assessments: List[Dict[str, Any]]) -> List[str]:
    """Pull the triggered detector categories from an assessment block.

    Only category/type labels are extracted — never the matched text — so the
    result is safe to put in an audit log.
    """
    categories: List[str] = []
    for assessment in assessments or []:
        topic = assessment.get("topicPolicy", {})
        for t in topic.get("topics", []):
            if t.get("action") == "BLOCKED":
                categories.append(f"topic:{t.get('name', 'unknown')}")

        content = assessment.get("contentPolicy", {})
        for f in content.get("filters", []):
            if f.get("action") == "BLOCKED":
                categories.append(f"content:{f.get('type', 'unknown')}")

        sensitive = assessment.get("sensitiveInformationPolicy", {})
        for p in sensitive.get("piiEntities", []):
            if p.get("action") in ("BLOCKED", "ANONYMIZED"):
                categories.append(f"pii:{p.get('type', 'unknown')}")

        word = assessment.get("wordPolicy", {})
        for w in word.get("customWords", []):
            if w.get("action") == "BLOCKED":
                categories.append("word:custom")

    return categories


class GuardrailClient:
    """Applies a Bedrock guardrail to untrusted text (input) and model output.

    Stateless wrapper around ``bedrock-runtime.apply_guardrail``. Designed to be
    called from inside ``BedrockClient._invoke_sync`` (synchronous path).
    """

    def __init__(self, client: Any = None):
        self._client = client
        self.enabled = settings.guardrail_enabled
        self.guardrail_id = settings.guardrail_id
        self.guardrail_version = settings.guardrail_version

    @property
    def client(self) -> Any:
        """Lazily build the boto3 client so importing this module never needs AWS."""
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=settings.aws_region,
                config=Config(retries={"mode": "adaptive", "max_attempts": 2}),
            )
        return self._client

    def apply(
        self,
        text: str,
        source: str,
        agent_id: str = "unknown",
        user_id: str = "unknown",
        session_id: str = "",
    ) -> None:
        """Evaluate ``text`` against the guardrail. Fail-closed.

        Returns ``None`` when the content is allowed. Raises
        ``GuardrailBlockedError`` when blocked or when the guardrail cannot be
        evaluated (unavailable/misconfigured).
        """
        if not self.enabled:
            return  # Explicitly disabled (e.g. local dev) — no evaluation.

        # Enabled but not provisioned → fail-closed. Refuse rather than run the
        # model unprotected; a missing guardrail id is a security misconfig.
        if not self.guardrail_id:
            self._audit(
                "guardrail_misconfigured", source, agent_id, user_id, session_id,
                text_digest=_digest(text),
            )
            raise GuardrailBlockedError(reason="unavailable", source=source)

        try:
            response = self.client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.guardrail_version,
                source=source,
                content=[{"text": {"text": text}}],
            )
        except (ClientError, BotoCoreError) as e:
            # Service error → fail-closed (do NOT proceed to the model).
            self._audit(
                "guardrail_unavailable", source, agent_id, user_id, session_id,
                text_digest=_digest(text),
                error_type=type(e).__name__,
            )
            raise GuardrailBlockedError(reason="unavailable", source=source) from e

        if response.get("action") == _ACTION_INTERVENED:
            categories = _extract_categories(response.get("assessments", []))
            self._audit(
                "guardrail_block", source, agent_id, user_id, session_id,
                text_digest=_digest(text),
                categories=categories,
            )
            # ADD (b): emit guardrail block metric (bounded: source ∈ 4 values, agent_id ∈ ~10)
            guardrail_blocks.add(1, {"source": source, "agent_id": agent_id})
            raise GuardrailBlockedError(reason="blocked", source=source, categories=categories)

    @staticmethod
    def _audit(
        event: str,
        source: str,
        agent_id: str,
        user_id: str,
        session_id: str,
        **extra: Any,
    ) -> None:
        """Structured audit log for a detection/refusal (Task 4).

        Invariant: never logs the malicious payload in clear text. Carries
        agent_id/user_id/session_id for traceability (spec 14 invariant).
        """
        logger.warning(
            "guardrail event",
            extra={
                "audit": True,
                "event": event,
                "guardrail_source": source,
                "agent_id": agent_id,
                "user_id": user_id,
                "session_id": session_id,
                **extra,
            },
        )


guardrail = GuardrailClient()
