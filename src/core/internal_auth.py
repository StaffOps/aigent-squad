"""Internal-token auth for the supervisor's gateway-only surface (spec 31).

The supervisor's `/internal/process` is meant to be called ONLY by the edge
gateway. This guards it with a token DISTINCT from the edge `INTERNAL_API_TOKEN`
(`src/core/auth.py`), so a leak of one does not grant the other.

Fail-closed: if `SUPERVISOR_INTERNAL_TOKEN` is unset, every call is denied — a
misconfigured supervisor refuses internal traffic rather than accepting it
unauthenticated. This is the L7 layer; NetworkPolicy (L3) and future Istio mTLS
(identity) are independent layers — the link trusts no single control alone.
"""
from fastapi import Header, HTTPException

from src.core.config import settings


def require_internal_token(x_supervisor_token: str = Header(default="")):
    """FastAPI dependency validating the gateway→supervisor internal token."""
    expected = settings.supervisor_internal_token or ""
    if not expected or x_supervisor_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized (internal)")
