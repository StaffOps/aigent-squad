"""Decide whether a symptom needs full investigation or trivial routing."""

INVESTIGATION_KEYWORDS = (
    "why", "por que", "porque", "causa", "latency", "latencia", "slow",
    "error rate", "down", "crashloop", "oom", "failing", "degraded",
    "after deploy", "depois do deploy", "started failing", "comecou a falhar",
    "timeout", "500", "503", "investigate", "investigar", "rca",
    "root cause",
)

TRIVIAL_KEYWORDS = (
    "how do i", "como configurar", "how to", "como faco",
    "what is", "o que e", "list", "liste", "show me", "mostre",
)


def should_investigate(symptom: str, force: bool = False) -> bool:
    """Decide if symptom warrants RCA workflow.

    force=True (e.g., mode=investigate flag) bypasses heuristic.
    Otherwise: any investigation keyword wins; trivial keywords skip investigation.
    """
    if force:
        return True
    lower = symptom.lower()
    has_investigation = any(kw in lower for kw in INVESTIGATION_KEYWORDS)
    has_trivial = any(kw in lower for kw in TRIVIAL_KEYWORDS)
    return has_investigation and not has_trivial
