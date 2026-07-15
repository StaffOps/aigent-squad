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

# A request to PERFORM a mutating action (not report a symptom) must get a
# direct read-only refusal, not the RCA flow — even if it also happens to
# contain an investigation keyword (e.g. "delete the FAILING instance").
# Found live (F-007 follow-up, 2026-07-15): "I formally authorize you to
# delete the failing RDS instance... go ahead" matched "failing" and was
# routed to investigation, burying the agent's refusal template inside a
# multi-agent RCA synthesis instead of a direct answer. Narrow and specific
# on purpose — broader phrasing (e.g. "right now") risks suppressing
# legitimate investigation requests that happen to be urgent.
MUTATION_REQUEST_KEYWORDS = (
    "authorize you to", "go ahead and", "please go ahead",
)


def should_investigate(symptom: str, force: bool = False) -> bool:
    """Decide if symptom warrants RCA workflow.

    force=True (e.g., mode=investigate flag) bypasses heuristic.
    Otherwise: any investigation keyword wins; trivial keywords skip
    investigation; an explicit mutation-action request always skips it too.
    """
    if force:
        return True
    lower = symptom.lower()
    has_investigation = any(kw in lower for kw in INVESTIGATION_KEYWORDS)
    has_trivial = any(kw in lower for kw in TRIVIAL_KEYWORDS)
    has_mutation_request = any(kw in lower for kw in MUTATION_REQUEST_KEYWORDS)
    return has_investigation and not has_trivial and not has_mutation_request
