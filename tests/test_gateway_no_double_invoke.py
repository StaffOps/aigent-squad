"""Regression test: stream:true triggers exactly ONE agentic/supervisor invocation.

Phase 3.5 BLOCKER fix — the gateway previously called _one_shot() (non-streaming
process()) UNCONDITIONALLY, then for stream:true called process_stream() a SECOND
time. This double-invocation caused 2x token cost + latency on every streaming
request.

After the fix:
  - stream:true → process_stream() ONLY (one invocation); process() NOT called
  - stream:true + process_stream failure → process() fallback (still one total)
  - stream:false → process() via worker pool as before (no process_stream)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from src.gateway.auth import require_edge_auth
from src.gateway.main import app, supervisor_client, worker_pool


@pytest.fixture(autouse=True)
def _bypass_edge_auth():
    """Override the auth dependency so all requests pass through."""
    app.dependency_overrides[require_edge_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _openai_body(stream: bool, model: str = "aigent-squad"):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": stream,
    }


# ─── BLOCKER REGRESSION: stream:true → exactly ONE invocation ─────────────


class TestStreamTrueNoDoubleInvocation:
    """stream:true → exactly ONE invocation via process_stream (happy path)."""

    @patch("src.gateway.main._check_admission", new_callable=AsyncMock, return_value=None)
    @patch("src.gateway.main._agent_names", ["aigent-squad"])
    def test_streaming_calls_process_stream_not_process(self, mock_adm, client):
        """REGRESSION: stream:true must NOT call process() at all on happy path."""
        with patch.object(worker_pool, "has_capacity", return_value=True), \
             patch.object(supervisor_client, "is_supervisor_ready", new_callable=AsyncMock, return_value=True):

            # Simulate a successful streaming response
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            async def _fake_aiter_bytes():
                yield b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n"
                yield b"data: [DONE]\n\n"

            mock_resp.aiter_bytes = _fake_aiter_bytes
            mock_resp.aclose = AsyncMock()

            with patch.object(supervisor_client, "process_stream", new_callable=AsyncMock, return_value=mock_resp) as mock_stream, \
                 patch.object(supervisor_client, "process", new_callable=AsyncMock) as mock_process, \
                 patch.object(worker_pool, "submit", new_callable=AsyncMock) as mock_submit:

                response = client.post("/v1/chat/completions", json=_openai_body(stream=True))

                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]

                # CRITICAL: process_stream was called exactly once
                mock_stream.assert_called_once()
                # CRITICAL: process() was NEVER called
                mock_process.assert_not_called()
                # Worker pool submit was NEVER called (streaming bypasses pool)
                mock_submit.assert_not_called()

    @patch("src.gateway.main._check_admission", new_callable=AsyncMock, return_value=None)
    @patch("src.gateway.main._agent_names", ["aigent-squad"])
    def test_streaming_fallback_calls_process_once_on_stream_failure(self, mock_adm, client):
        """When process_stream fails, fall back to process() — still one total invocation."""
        from src.gateway.supervisor_client import SupervisorUnavailableError

        with patch.object(worker_pool, "has_capacity", return_value=True), \
             patch.object(supervisor_client, "is_supervisor_ready", new_callable=AsyncMock, return_value=True):

            with patch.object(
                supervisor_client, "process_stream",
                new_callable=AsyncMock,
                side_effect=SupervisorUnavailableError("stream endpoint down"),
            ) as mock_stream, \
                 patch.object(
                supervisor_client, "process",
                new_callable=AsyncMock,
                return_value={"response": "fallback answer", "agent_id": "aigent-squad"},
            ) as mock_process:

                response = client.post("/v1/chat/completions", json=_openai_body(stream=True))

                assert response.status_code == 200
                # process_stream was attempted once (failed)
                mock_stream.assert_called_once()
                # process() was called exactly once as fallback
                mock_process.assert_called_once()


# ─── stream:false → process() via worker pool, process_stream NOT called ──


class TestStreamFalseNoProcessStream:
    """stream:false → process() via worker pool, process_stream NOT called."""

    @patch("src.gateway.main._check_admission", new_callable=AsyncMock, return_value=None)
    @patch("src.gateway.main._agent_names", ["aigent-squad"])
    def test_non_streaming_uses_worker_pool_not_process_stream(self, mock_adm, client):
        """stream:false must NOT call process_stream()."""
        with patch.object(worker_pool, "has_capacity", return_value=True), \
             patch.object(supervisor_client, "is_supervisor_ready", new_callable=AsyncMock, return_value=True):

            with patch.object(
                supervisor_client, "process",
                new_callable=AsyncMock,
                return_value={"response": "non-streaming answer", "agent_id": "aigent-squad"},
            ) as mock_process, \
                 patch.object(supervisor_client, "process_stream", new_callable=AsyncMock) as mock_stream:

                async def fake_submit(job_id, stream):
                    async for item in stream:
                        yield item

                with patch.object(worker_pool, "submit", side_effect=fake_submit) as mock_submit:
                    response = client.post("/v1/chat/completions", json=_openai_body(stream=False))

                    assert response.status_code == 200
                    # process_stream was NEVER called
                    mock_stream.assert_not_called()
                    # process() was called (via _one_shot in worker pool)
                    mock_process.assert_called_once()
                    # Worker pool was used
                    mock_submit.assert_called_once()


# ─── SR3: _proxy_sse framing — verbatim forwarding ───────────────────────


class TestProxySSEFramingVerbatim:
    """_proxy_sse forwards upstream bytes verbatim (no extra newlines)."""

    @patch("src.gateway.main._check_admission", new_callable=AsyncMock, return_value=None)
    @patch("src.gateway.main._agent_names", ["aigent-squad"])
    def test_sse_framing_preserved_no_triple_newline(self, mock_adm, client):
        """Upstream SSE frames must arrive at client byte-for-byte identical."""
        with patch.object(worker_pool, "has_capacity", return_value=True), \
             patch.object(supervisor_client, "is_supervisor_ready", new_callable=AsyncMock, return_value=True):

            # Craft an upstream SSE body with correct framing (data:...\n\n)
            sse_body = (
                b"data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n"
                b"data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n"
                b"data: [DONE]\n\n"
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200

            async def _fake_aiter_bytes():
                yield sse_body

            mock_resp.aiter_bytes = _fake_aiter_bytes
            mock_resp.aclose = AsyncMock()

            with patch.object(supervisor_client, "process_stream", new_callable=AsyncMock, return_value=mock_resp):
                response = client.post("/v1/chat/completions", json=_openai_body(stream=True))

                body = response.content
                # The body must exactly equal the upstream SSE — no added/removed newlines
                assert body == sse_body
                # Verify no triple-newline (the old bug)
                assert b"\n\n\n" not in body
