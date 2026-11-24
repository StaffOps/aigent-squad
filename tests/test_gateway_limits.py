"""Gateway input-size caps and job timeout (BACKLOG F-009 S4 + C1).

Contract under test:
  - /query and /v1/chat/completions reject oversized input with 413 and an
    OpenAI-shaped error body BEFORE admission, the supervisor preflight or any
    LLM work (GATEWAY_MAX_INPUT_CHARS / GATEWAY_MAX_MESSAGES / GATEWAY_MAX_BODY_BYTES).
  - The char cap applies to the forwarded turn, so a long history with a short
    last user message is accepted.
  - A job that runs past GATEWAY_JOB_TIMEOUT_SECONDS is cancelled and answered
    with 504 gateway_timeout (non-streaming), or ends with an SSE error event once
    the stream has started.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.gateway.main as gw
from src.gateway.auth import require_edge_auth
from src.gateway.worker_pool import (
    FirstByteTimeout,
    IdleStreamTimeout,
    JobTimeout,
    WorkerPool,
    stream_with_deadline,
)


@pytest.fixture(autouse=True)
def _bypass_edge_auth():
    gw.app.dependency_overrides[require_edge_auth] = lambda: None
    yield
    gw.app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(gw.app)


@pytest.fixture
def downstream():
    """Admission, pool and supervisor mocks; asserts nothing ran on a 413."""
    with patch.object(gw, "admission") as adm, \
         patch.object(gw, "supervisor_client") as sc, \
         patch.object(gw, "_agent_names", ["aws"]):
        adm.check_rate = AsyncMock(return_value=(True, 59))
        adm.check_budget = AsyncMock(return_value=(True, 10.0))
        sc.is_supervisor_ready = AsyncMock(return_value=True)
        sc.list_agents = AsyncMock(return_value=["aws"])
        sc.process = AsyncMock(return_value={"agent": "aws", "response": "ok", "confidence": 0.9})
        yield adm, sc


def _assert_413(resp):
    assert resp.status_code == 413
    err = resp.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "request_too_large"


def _query(text):
    return {"user_input": text, "user_id": "u1", "session_id": "s1"}


def _chat(messages, stream=False):
    return {"model": "aigent-squad", "messages": messages, "stream": stream}


# ─── Input caps ──────────────────────────────────────────────────────


class TestQueryInputCap:
    def test_oversized_user_input_is_413_before_admission(self, client, downstream):
        adm, sc = downstream
        with patch.object(gw.settings, "gateway_max_input_chars", 10):
            resp = client.post("/query", json=_query("x" * 11))
        _assert_413(resp)
        adm.check_rate.assert_not_called()
        sc.is_supervisor_ready.assert_not_called()
        sc.process.assert_not_called()

    def test_input_at_the_limit_is_forwarded(self, client, downstream):
        _, sc = downstream
        with patch.object(gw.settings, "gateway_max_input_chars", 10):
            resp = client.post("/query", json=_query("x" * 10))
        assert resp.status_code == 200
        sc.process.assert_awaited_once()

    def test_default_char_cap_matches_input_scanner(self):
        from src.core.config import Settings
        from src.core.input_scanner import _MAX_INPUT_LENGTH

        assert Settings.model_fields["gateway_max_input_chars"].default == _MAX_INPUT_LENGTH


class TestChatInputCap:
    def test_too_many_messages_is_413(self, client, downstream):
        adm, sc = downstream
        msgs = [{"role": "user", "content": "hi"}] * 4
        with patch.object(gw.settings, "gateway_max_messages", 3):
            resp = client.post("/v1/chat/completions", json=_chat(msgs))
        _assert_413(resp)
        adm.check_rate.assert_not_called()
        sc.list_agents.assert_not_called()
        sc.process.assert_not_called()

    def test_oversized_last_user_turn_is_413(self, client, downstream):
        adm, sc = downstream
        with patch.object(gw.settings, "gateway_max_input_chars", 10):
            resp = client.post(
                "/v1/chat/completions",
                json=_chat([{"role": "user", "content": "y" * 11}], stream=True),
            )
        _assert_413(resp)
        adm.check_rate.assert_not_called()
        sc.process.assert_not_called()

    def test_long_history_with_short_last_turn_is_accepted(self, client, downstream):
        _, sc = downstream
        history = [
            {"role": "user", "content": "a" * 9},
            {"role": "assistant", "content": "b" * 50},
            {"role": "user", "content": "short"},
        ]
        with patch.object(gw.settings, "gateway_max_input_chars", 10):
            resp = client.post("/v1/chat/completions", json=_chat(history))
        assert resp.status_code == 200
        assert sc.process.await_args.kwargs["user_input"] == "short"


class TestBodySizeCap:
    def test_declared_body_over_limit_is_413(self, client, downstream):
        _, sc = downstream
        with patch.object(gw.settings, "gateway_max_body_bytes", 20):
            resp = client.post("/query", json=_query("well over twenty bytes"))
        _assert_413(resp)
        sc.process.assert_not_called()

    def test_body_cap_applies_to_v1(self, client, downstream):
        with patch.object(gw.settings, "gateway_max_body_bytes", 20):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}]))
        _assert_413(resp)

    def test_non_numeric_content_length_is_not_rejected_by_the_cap(self, client):
        # The header is malformed; the cap must not crash or claim 413. What the
        # server does with the malformed header afterwards is not this contract.
        resp = client.get("/healthz", headers={"content-length": "abc"})
        assert resp.status_code != 413

    def test_small_body_passes(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_chunked_body_over_limit_is_413(self, client, downstream):
        _, sc = downstream

        def chunks():
            yield b'{"user_input": "'
            for _ in range(10):
                yield b"x" * 10
            yield b'", "user_id": "u1", "session_id": "s1"}'

        with patch.object(gw.settings, "gateway_max_body_bytes", 50):
            resp = client.post("/query", content=chunks(), headers={"content-type": "application/json"})
        _assert_413(resp)
        sc.process.assert_not_called()

    def test_chunked_body_under_limit_is_forwarded(self, client, downstream):
        _, sc = downstream

        def chunks():
            yield b'{"user_input": "hi", '
            yield b'"user_id": "u1", "session_id": "s1"}'

        resp = client.post("/query", content=chunks(), headers={"content-type": "application/json"})
        assert resp.status_code == 200
        sc.process.assert_awaited_once()

    def test_rejection_metric_carries_reason(self, client, downstream):
        with patch.object(gw, "gateway_input_rejections") as counter, \
             patch.object(gw.settings, "gateway_max_messages", 1):
            client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "a"}] * 2))
        counter.add.assert_called_once_with(1, {"reason": "messages"})


# ─── Job timeout, non-streaming (worker pool) ────────────────────────


def _real_pool(job_timeout, first_byte=5.0, idle=5.0):
    return WorkerPool(
        max_concurrent=2,
        job_timeout=job_timeout,
        first_byte_timeout=first_byte,
        idle_timeout=idle,
        redis_client=None,
    )


async def _slow_process(**_kwargs):
    await asyncio.sleep(5)
    return {"agent": "aws", "response": "late", "confidence": 0.9}  # pragma: no cover


class TestNonStreamingTimeout:
    def test_query_job_timeout_is_504(self, client, downstream):
        _, sc = downstream
        sc.process = AsyncMock(side_effect=_slow_process)
        with patch.object(gw, "worker_pool", _real_pool(job_timeout=0.05)):
            resp = client.post("/query", json=_query("hi"))
            assert gw.worker_pool.active_count == 0  # slot released
        assert resp.status_code == 504
        assert resp.json()["error"]["type"] == "gateway_timeout"

    def test_chat_job_timeout_is_504(self, client, downstream):
        _, sc = downstream
        sc.process = AsyncMock(side_effect=_slow_process)
        with patch.object(gw, "worker_pool", _real_pool(job_timeout=0.05)):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}]))
        assert resp.status_code == 504
        assert resp.json()["error"]["type"] == "gateway_timeout"

    @pytest.mark.parametrize("exc", [FirstByteTimeout("j"), IdleStreamTimeout("j"), JobTimeout("j")])
    def test_every_pool_timeout_maps_to_504(self, client, downstream, exc):
        async def raising_submit(job_id, stream):
            async def gen():
                raise exc
                yield  # pragma: no cover
            return gen()

        with patch.object(gw, "worker_pool") as pool:
            pool.has_capacity.return_value = True
            pool.submit = AsyncMock(side_effect=raising_submit)
            resp = client.post("/query", json=_query("hi"))
        assert resp.status_code == 504
        assert resp.json()["error"]["type"] == "gateway_timeout"


# ─── Job timeout, streaming (/v1 SSE proxy) ──────────────────────────


class _FakeStreamResponse:
    def __init__(self, chunks, stall_after=None):
        self._chunks = chunks
        self._stall_after = stall_after
        self.closed = False

    async def aiter_bytes(self):
        for i, chunk in enumerate(self._chunks):
            if self._stall_after is not None and i == self._stall_after:
                await asyncio.sleep(5)
            yield chunk

    async def aclose(self):
        self.closed = True


class TestStreamingTimeout:
    def test_stream_that_finishes_in_time_is_proxied(self, client, downstream):
        _, sc = downstream
        fake = _FakeStreamResponse([b"data: a\n\n", b"data: [DONE]\n\n"])
        sc.process_stream = AsyncMock(return_value=fake)
        resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}], stream=True))
        assert resp.status_code == 200
        assert resp.text == "data: a\n\ndata: [DONE]\n\n"
        assert fake.closed

    def test_stream_past_deadline_ends_with_sse_error(self, client, downstream):
        _, sc = downstream
        fake = _FakeStreamResponse([b"data: a\n\n", b"data: late\n\n"], stall_after=1)
        sc.process_stream = AsyncMock(return_value=fake)
        with patch.object(gw.settings, "gateway_job_timeout_seconds", 0.2):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}], stream=True))
        assert resp.status_code == 200
        assert resp.text.startswith("data: a\n\n")
        assert '"gateway_timeout"' in resp.text
        assert "late" not in resp.text
        assert fake.closed
        sc.process.assert_not_called()  # a timeout is not a reason to re-run the job

    def test_timeout_mid_frame_closes_the_frame_first(self, client, downstream):
        _, sc = downstream
        fake = _FakeStreamResponse([b"data: partial", b"never"], stall_after=1)
        sc.process_stream = AsyncMock(return_value=fake)
        with patch.object(gw.settings, "gateway_job_timeout_seconds", 0.2):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}], stream=True))
        assert resp.text.startswith('data: partial\n\ndata: {"error"')

    def test_stream_open_past_deadline_is_504(self, client, downstream):
        _, sc = downstream

        async def slow_open(**_kwargs):
            await asyncio.sleep(5)

        sc.process_stream = AsyncMock(side_effect=slow_open)
        with patch.object(gw.settings, "gateway_job_timeout_seconds", 0.05):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}], stream=True))
        assert resp.status_code == 504
        assert resp.json()["error"]["type"] == "gateway_timeout"
        sc.process.assert_not_called()

    def test_fallback_past_deadline_is_504(self, client, downstream):
        _, sc = downstream
        sc.process_stream = AsyncMock(side_effect=RuntimeError("no stream endpoint"))
        sc.process = AsyncMock(side_effect=_slow_process)
        with patch.object(gw.settings, "gateway_job_timeout_seconds", 0.05):
            resp = client.post("/v1/chat/completions", json=_chat([{"role": "user", "content": "hi"}], stream=True))
        assert resp.status_code == 504
        assert resp.json()["error"]["type"] == "gateway_timeout"


# ─── WorkerPool job deadline + stream_with_deadline ──────────────────


class TestPoolJobDeadline:
    @pytest.mark.asyncio
    async def test_job_timeout_shorter_than_first_byte_wins(self):
        pool = _real_pool(job_timeout=0.05, first_byte=5.0)

        async def never_yields():
            await asyncio.sleep(5)
            yield "never"  # pragma: no cover

        gen = await pool.submit("j1", never_yields())
        with pytest.raises(JobTimeout):
            async for _ in gen:
                pass  # pragma: no cover
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_job_timeout_caps_a_stream_that_never_goes_idle(self):
        # Each chunk arrives well inside the idle budget, but the run as a whole
        # exceeds the job budget.
        pool = _real_pool(job_timeout=0.15, first_byte=1.0, idle=1.0)

        async def steady():
            for i in range(100):
                await asyncio.sleep(0.02)
                yield i

        gen = await pool.submit("j2", steady())
        seen = []
        with pytest.raises(JobTimeout):
            async for c in gen:
                seen.append(c)
        assert 0 < len(seen) < 100
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_first_byte_still_fires_when_shorter_than_job(self):
        pool = _real_pool(job_timeout=5.0, first_byte=0.05)

        async def never_yields():
            await asyncio.sleep(5)
            yield "never"  # pragma: no cover

        gen = await pool.submit("j3", never_yields())
        with pytest.raises(FirstByteTimeout):
            async for _ in gen:
                pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_job_timeout_status_recorded(self):
        import fakeredis.aioredis
        import json

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        pool = WorkerPool(
            max_concurrent=1, job_timeout=0.05, first_byte_timeout=5.0,
            idle_timeout=5.0, redis_client=redis,
        )

        async def never_yields():
            await asyncio.sleep(5)
            yield "never"  # pragma: no cover

        gen = await pool.submit("j4", never_yields())
        with pytest.raises(JobTimeout):
            async for _ in gen:
                pass  # pragma: no cover
        assert json.loads(await redis.get("job:j4"))["status"] == "job_timeout"


class TestTimeoutMetric:
    @pytest.mark.asyncio
    async def test_timeout_kind_label(self):
        pool = _real_pool(job_timeout=5.0, first_byte=1.0, idle=0.05)

        async def stalls():
            yield "first"
            await asyncio.sleep(5)
            yield "never"  # pragma: no cover

        with patch("src.gateway.worker_pool.gateway_timeouts") as counter:
            gen = await pool.submit("jm", stalls())
            with pytest.raises(IdleStreamTimeout):
                async for _ in gen:
                    pass
        counter.add.assert_called_once_with(1, {"kind": "idle"})


class TestStreamWithDeadline:
    @pytest.mark.asyncio
    async def test_completes_within_budget(self):
        async def gen():
            yield 1
            yield 2

        assert [c async for c in stream_with_deadline(gen(), 1.0)] == [1, 2]

    @pytest.mark.asyncio
    async def test_raises_and_closes_source_on_timeout(self):
        closed = MagicMock()

        async def gen():
            try:
                yield 1
                await asyncio.sleep(5)
                yield 2  # pragma: no cover
            finally:
                closed()

        seen = []
        with pytest.raises(JobTimeout):
            async for c in stream_with_deadline(gen(), 0.05):
                seen.append(c)
        assert seen == [1]
        closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_budget_raises_immediately(self):
        async def gen():
            yield 1  # pragma: no cover

        with pytest.raises(JobTimeout):
            async for _ in stream_with_deadline(gen(), 0):
                pass  # pragma: no cover
