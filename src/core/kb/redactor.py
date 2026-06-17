"""Strip PII and secrets before LLM processing."""
import re

PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<redacted:email>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<redacted:aws-key>"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "<redacted:aws-key>"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "<redacted:github-pat>"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "<redacted:gitlab-pat>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE), "Bearer <redacted:token>"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "<redacted:openai-key>"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in PATTERNS:
        text = pattern.sub(repl, text)
    return text
