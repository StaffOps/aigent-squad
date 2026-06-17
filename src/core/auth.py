"""Internal API token authentication. Fail-closed: no token in env = deny all."""
import os
from fastapi import Header, HTTPException


_EXPECTED_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")


def require_token(x_internal_token: str = Header(default="")):
    """FastAPI dependency that validates X-Internal-Token header."""
    if not _EXPECTED_TOKEN or x_internal_token != _EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
