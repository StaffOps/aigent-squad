"""Edge auth for the gateway (spec 31, T5 + G-5 per-consumer key scoping).

The gateway is the public surface. It accepts any ONE of:
  - ``X-Internal-Token`` matching ``INTERNAL_API_TOKEN`` (the existing contract;
    what LibreChat sends, mapped via its `headers` config), or
  - ``X-API-Key`` in the ``GATEWAY_API_KEYS`` allowlist (csv), for per-client keys, or
  - ``Authorization: Bearer <token>`` where the bearer value matches
    ``INTERNAL_API_TOKEN`` or is in ``GATEWAY_API_KEYS`` (standard HTTP auth;
    needed by Grafana LLM app which only sends Authorization headers).

Per-consumer agent scoping (G-5):
  ``GATEWAY_KEY_AGENT_MAP`` maps a credential to a DEFAULT agent name. When a
  request is authenticated by a mapped key, the resolved default agent is returned
  so the request handler can use it as a routing hint (when the model is auto-route
  and no explicit agent is forced). This is NOT an auth bypass — all keys still go
  through normal auth; it only adds a routing default.

Fail-closed: if no secret is configured, all requests are denied — the gateway
never serves unauthenticated by accident.
"""
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

_EXPECTED_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
_API_KEYS = {k.strip() for k in os.getenv("GATEWAY_API_KEYS", "").split(",") if k.strip()}

# G-5: per-consumer key → default agent mapping.
# Format: "key1=agent1,key2=agent2" (comma-separated pairs).
# Keys in this map are ALSO implicitly valid API keys (they don't need to be
# duplicated in GATEWAY_API_KEYS). The agent name is a routing default, not a
# hard restriction.
_KEY_AGENT_MAP: dict[str, str] = {}
_raw_map = os.getenv("GATEWAY_KEY_AGENT_MAP", "")
for _pair in _raw_map.split(","):
    _pair = _pair.strip()
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        _k, _v = _k.strip(), _v.strip()
        if _k and _v:
            _KEY_AGENT_MAP[_k] = _v


@dataclass
class AuthResult:
    """Result of edge authentication.

    Attributes:
        consumer_default_agent: The default agent for this consumer (from
            GATEWAY_KEY_AGENT_MAP), or None if the consumer has no scoping
            (internal token or plain API key without mapping).
    """
    consumer_default_agent: Optional[str] = None


def get_key_agent_map() -> dict[str, str]:
    """Expose the parsed key→agent map (for startup validation)."""
    return dict(_KEY_AGENT_MAP)


def _extract_bearer(authorization) -> str:
    """Extract token from 'Bearer <token>' header value.

    Case-insensitive prefix match. Returns empty string on missing/malformed.
    Tolerates non-string input (when called directly outside FastAPI DI).
    """
    if not authorization or not isinstance(authorization, str):
        return ""
    stripped = authorization.strip()
    if stripped.lower().startswith("bearer "):
        return stripped[7:].strip()
    return ""


def _resolve_default_agent(matched_key: str) -> Optional[str]:
    """Look up the default agent for a matched credential.

    Uses timing-safe comparison against the key-agent map keys to avoid
    leaking which keys have mappings via timing.
    """
    for map_key, agent in _KEY_AGENT_MAP.items():
        if hmac.compare_digest(matched_key.encode(), map_key.encode()):
            return agent
    return None


def require_edge_auth(
    x_internal_token: str = Header(default=""),
    x_api_key: Optional[str] = Header(default=None),
    authorization: str = Header(default=""),
) -> AuthResult:
    """FastAPI dependency: accept a valid internal token, API key, or Bearer token.

    Returns AuthResult with consumer_default_agent populated when the matched
    credential has a GATEWAY_KEY_AGENT_MAP entry.
    """
    # 1. X-Internal-Token header → internal consumer, no agent scoping
    if _EXPECTED_TOKEN and hmac.compare_digest(x_internal_token.encode(), _EXPECTED_TOKEN.encode()):
        return AuthResult(consumer_default_agent=None)

    # 2. X-API-Key header
    if x_api_key:
        # Check plain API keys
        if x_api_key in _API_KEYS:
            return AuthResult(consumer_default_agent=_resolve_default_agent(x_api_key))
        # Check key-agent map keys (timing-safe lookup; they are implicitly valid)
        _mapped = _resolve_default_agent(x_api_key)
        if _mapped is not None:
            return AuthResult(consumer_default_agent=_mapped)

    # 3. Authorization: Bearer <token>
    bearer = _extract_bearer(authorization)
    if bearer:
        if _EXPECTED_TOKEN and hmac.compare_digest(bearer.encode(), _EXPECTED_TOKEN.encode()):
            return AuthResult(consumer_default_agent=None)
        if bearer in _API_KEYS:
            return AuthResult(consumer_default_agent=_resolve_default_agent(bearer))
        _mapped = _resolve_default_agent(bearer)
        if _mapped is not None:
            return AuthResult(consumer_default_agent=_mapped)

    raise HTTPException(status_code=401, detail="Unauthorized")
