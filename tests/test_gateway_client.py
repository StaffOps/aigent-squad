"""Tests for src/gateway/supervisor_client.py (spec 31, L1).

Tests the SupervisorClient CONTRACT: preflight (is_supervisor_ready), process()
return/error semantics, list_agents behavior, connection pool sizing.

Uses respx to mock httpx without network I/O.
"""
import httpx
import pytest
import respx

from src.gateway.supervisor_client import SupervisorClient, SupervisorUnavailableError


@pytest.fixture
def client():
    return SupervisorClient(
        base_url="http://supervisor:8001",
        token="test-internal-token",
        max_connections=10,
        timeout=5.0,
    )


# ─── is_supervisor_ready ─────────────────────────────────────────────


class TestIsReady:
    @pytest.mark.asyncio
    @respx.mock
    async def test_ready_returns_true_on_200(self, client):
        respx.get("http://supervisor:8001/ready").mock(
            return_value=httpx.Response(200, json={"status": "ready"})
        )
        assert await client.is_supervisor_ready() is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_ready_returns_false_on_503(self, client):
        respx.get("http://supervisor:8001/ready").mock(
            return_value=httpx.Response(503)
        )
        assert await client.is_supervisor_ready() is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_ready_returns_false_on_transport_error(self, client):
        respx.get("http://supervisor:8001/ready").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert await client.is_supervisor_ready() is False


# ─── process() ───────────────────────────────────────────────────────


class TestProcess:
    @pytest.mark.asyncio
    @respx.mock
    async def test_process_returns_dict_on_200(self, client):
        expected = {"agent": "aws", "response": "hello", "confidence": 0.9}
        respx.post("http://supervisor:8001/internal/process").mock(
            return_value=httpx.Response(200, json=expected)
        )
        result = await client.process("hi", "u1", "s1")
        assert result == expected

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_raises_supervisor_unavailable_on_transport_error(self, client):
        respx.post("http://supervisor:8001/internal/process").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        with pytest.raises(SupervisorUnavailableError):
            await client.process("hi", "u1", "s1")

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_propagates_http_status_error_on_4xx(self, client):
        respx.post("http://supervisor:8001/internal/process").mock(
            return_value=httpx.Response(403, json={"detail": "blocked"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.process("hi", "u1", "s1")
        assert exc_info.value.response.status_code == 403

    @pytest.mark.asyncio
    @respx.mock
    async def test_process_propagates_http_status_error_on_5xx(self, client):
        respx.post("http://supervisor:8001/internal/process").mock(
            return_value=httpx.Response(500, json={"detail": "crash"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.process("hi", "u1", "s1")
        assert exc_info.value.response.status_code == 500


# ─── list_agents ─────────────────────────────────────────────────────


class TestListAgents:
    @pytest.mark.asyncio
    @respx.mock
    async def test_list_agents_returns_names_on_200(self, client):
        respx.get("http://supervisor:8001/internal/agents").mock(
            return_value=httpx.Response(200, json={"agents": ["aws", "k8s"]})
        )
        assert await client.list_agents() == ["aws", "k8s"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_agents_returns_empty_on_error(self, client):
        respx.get("http://supervisor:8001/internal/agents").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await client.list_agents() == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_agents_returns_empty_on_non_200(self, client):
        respx.get("http://supervisor:8001/internal/agents").mock(
            return_value=httpx.Response(500)
        )
        assert await client.list_agents() == []


# ─── max_connections default ─────────────────────────────────────────


class TestConnectionPool:
    def test_max_connections_defaults_to_max_concurrent_plus_5(self):
        """From design: httpx max_connections = gateway_max_concurrent + 5."""
        from src.core.config import settings
        c = SupervisorClient(
            base_url="http://x:8001",
            token="t",
        )
        expected = settings.gateway_max_concurrent + 5
        # Verify the limits were passed correctly to httpx
        pool = c._client._transport._pool
        assert pool._max_connections == expected
