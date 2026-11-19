"""Provider-neutral LLM access (spec 28).

``from src.core.llm import llm`` returns the process-wide service selected by
``LLM_PROVIDER`` (default ``bedrock`` → the ``src.core.bedrock.bedrock``
singleton). It is built lazily on first access (PEP 562) so importing the
``src.core.llm`` submodules from ``src.core.bedrock`` never forms a cycle.
"""
from __future__ import annotations

from typing import Optional

from src.core.llm.service import LLMService

_llm: Optional[LLMService] = None


def get_llm() -> LLMService:
    """Return (building once) the provider-selected LLM service."""
    global _llm
    if _llm is None:
        from src.core.llm.factory import build_llm_service

        _llm = build_llm_service()
    return _llm


def __getattr__(name: str) -> LLMService:
    if name == "llm":
        return get_llm()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["LLMService", "get_llm"]  # plus the lazy `llm` attribute (PEP 562)
