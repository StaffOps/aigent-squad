"""Edge auth for the gateway (spec 31, T5).

The gateway is the public surface. It accepts either:
  - ``X-Internal-Token`` matching ``INTERNAL_API_TOKEN`` (the existing contract;
    what LibreChat sends, mapped via its `headers` config), or
  - ``X-API-Key`` in the ``GATEWAY_API_KEYS`` allowlist (csv), for per-client keys.

Fail-closed: if neither secret is configured, all requests are denied — the
gateway never serves unauthenticated by accident. This mirrors `src/core/auth.py`
(the supervisor's old edge gate) and is distinct from `internal_auth` (the
gateway→supervisor link).
"""
import os

from fastapi import Header, HTTPException
from typing import Optional

_EXPECTED_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
_API_KEYS = {k.strip() for k in os.getenv("GATEWAY_API_KEYS", "").split(",") if k.strip()}


def require_edge_auth(
    x_internal_token: str = Header(default=""),
    x_api_key: Optional[str] = Header(default=None),
):
    """FastAPI dependency: accept a valid internal token OR an allowlisted API key."""
    if _EXPECTED_TOKEN and x_internal_token == _EXPECTED_TOKEN:
        return
    if x_api_key and x_api_key in _API_KEYS:
        return
    raise HTTPException(status_code=401, detail="Unauthorized")
