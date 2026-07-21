"""HTTP client from the gateway to the supervisor backend (spec 31, L1/T4).

Forwards admitted requests to the supervisor's gateway-only `/internal/process`,
carrying the `SUPERVISOR_INTERNAL_TOKEN`. Preflight (`is_supervisor_ready`)
lets the gateway return a clean 503 instead of failing mid-stream when the
backend is down.

The supervisor returns a full JSON result today (no token streaming until spec
06), so this client exposes `process()` returning the dict. The gateway adapts
that dict to native/OpenAI shapes. When real streaming lands, add a `stream()`
that proxies the SSE body line by line.
"""
from __future__ import annotations

from typing import Optional

import httpx

from src.core.config import settings
from src.core.logger import logger


class SupervisorUnavailableError(Exception):
    """The supervisor backend is unreachable or returned a transport error."""


class SupervisorClient:
    """Thin httpx wrapper around the supervisor's internal contract."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        max_connections: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        self._base_url = (base_url or settings.supervisor_url).rstrip("/")
        self._token = token if token is not None else (settings.supervisor_internal_token or "")
        # httpx pool sized above the worker pool so the connection pool never
        # becomes a hidden second bottleneck below the semaphore (round-table).
        max_conn = max_connections or (settings.gateway_max_concurrent + 5)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            limits=httpx.Limits(max_connections=max_conn),
            timeout=timeout or settings.gateway_job_timeout_seconds,
            headers={"X-Supervisor-Token": self._token},
        )

    async def is_supervisor_ready(self) -> bool:
        """Preflight: is the supervisor up and ready to serve?"""
        try:
            resp = await self._client.get("/ready", timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_agents(self) -> list[str]:
        """Fetch agent names for the OpenAI /v1/models listing. Empty on failure."""
        try:
            resp = await self._client.get("/internal/agents", timeout=2.0)
            resp.raise_for_status()
            return resp.json().get("agents", [])
        except httpx.HTTPError:
            return []

    async def process(
        self,
        user_input: str,
        user_id: str,
        session_id: str,
        mode: str = "query",
        force_agent: Optional[str] = None,
    ) -> dict:
        """Forward to the supervisor's `/internal/process`; return its result.

        Raises:
            SupervisorUnavailableError — transport failure (→ gateway 503)
            httpx.HTTPStatusError       — supervisor returned 4xx/5xx (propagated;
                                          the gateway maps 403 guardrail, etc.)
        """
        payload = {
            "user_input": user_input,
            "user_id": user_id,
            "session_id": session_id,
            "mode": mode,
            "force_agent": force_agent,
        }
        try:
            resp = await self._client.post("/internal/process", json=payload)
        except httpx.HTTPError as exc:
            logger.warning("supervisor transport error", extra={"error": str(exc)})
            raise SupervisorUnavailableError(str(exc)) from exc
        resp.raise_for_status()
        return resp.json()

    async def process_stream(
        self,
        user_input: str,
        user_id: str,
        session_id: str,
        mode: str = "query",
        force_agent: Optional[str] = None,
    ) -> httpx.Response:
        """Forward to the supervisor's `/internal/process/stream` (Phase 3.5).

        Returns the raw httpx.Response for streaming iteration. The caller is
        responsible for iterating the SSE body line by line.

        Raises:
            SupervisorUnavailableError — transport failure (→ gateway 503)
            httpx.HTTPStatusError       — supervisor returned 4xx/5xx
        """
        payload = {
            "user_input": user_input,
            "user_id": user_id,
            "session_id": session_id,
            "mode": mode,
            "force_agent": force_agent,
        }
        try:
            req = self._client.build_request("POST", "/internal/process/stream", json=payload)
            resp = await self._client.send(req, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("supervisor transport error (stream)", extra={"error": str(exc)})
            raise SupervisorUnavailableError(str(exc)) from exc
        if resp.status_code >= 400:
            await resp.aread()
            resp.raise_for_status()
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()
