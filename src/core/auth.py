"""Internal API token authentication. Fail-closed: no token in env = deny all."""
import hmac
import os
from fastapi import Header, HTTPException


_EXPECTED_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")


def require_token(x_internal_token: str = Header(default="")) -> None:
    """FastAPI dependency that validates X-Internal-Token header."""
    if not _EXPECTED_TOKEN or not hmac.compare_digest(x_internal_token.encode(), _EXPECTED_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
