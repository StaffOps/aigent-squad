"""Tests for spec 47 — user feedback (POST /feedback + response id plumbing).

Uses fakeredis (shared FakeServer: async client for the app, sync client for
assertions) and the real edge-auth dependency where consumer scoping matters.
"""
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

import src.gateway.main as gw
from src.gateway import feedback as fb
from src.gateway.auth import AuthResult, consumer_id_for_key, require_edge_auth

KEY_A = "key-alpha-123"
KEY_B = "key-bravo-456"


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    gw.app.dependency_overrides.clear()


@pytest.fixture
def stores():
    server = fakeredis.FakeServer()
    async_r = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    sync_r = fakeredis.FakeRedis(server=server, decode_responses=True)
    store = fb.FeedbackStore(async_r)
    with patch.object(gw, "feedback_store", store):
        yield store, sync_r


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEYS", f"{KEY_A},{KEY_B}")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "internal-tok")
    monkeypatch.delenv("GATEWAY_KEY_AGENT_MAP", raising=False)


def _fake_submit():
    async def fake_submit(job_id, stream):
        async def wrapper():
            async for item in stream:
                yield item
        return wrapper()
    return AsyncMock(side_effect=fake_submit)


@contextmanager
def _backend(result):
    """Admit everything and answer with ``result`` from a mocked supervisor."""
    with patch.object(gw, "_check_admission", AsyncMock(return_value=None)), \
         patch.object(gw, "worker_pool") as pool, \
         patch.object(gw, "supervisor_client") as sc, \
         patch.object(gw, "_agent_names", ["aws", "kubernetes"]):
        pool.has_capacity.return_value = True
        pool.submit = _fake_submit()
        sc.is_supervisor_ready = AsyncMock(return_value=True)
        sc.process = AsyncMock(return_value=result)
        yield sc


# The supervisor result shape the gateway forwards (src/supervisor/agent.py).
ANSWER = {"agent": "aws", "response": "3 instances", "confidence": 0.9}


def _query(client, key=KEY_A):
    return client.post(
        "/query",
        json={"user_input": "list ec2", "user_id": "u1", "session_id": "s1"},
        headers={"X-API-Key": key},
    )


def _feedback(client, body, key=KEY_A):
    return client.post("/feedback", json=body, headers={"X-API-Key": key})


# ─── response id plumbing + shape regression ────────────────────────────


class TestQueryResponseId:
    def test_query_adds_response_id_and_header_keeps_fields(self, stores, keys):
        _, sync_r = stores
        with _backend(ANSWER):
            resp = _query(TestClient(gw.app))
        assert resp.status_code == 200
        body = resp.json()
        rid = body.pop("response_id")
        assert body == ANSWER  # nothing removed or renamed
        assert rid.startswith("resp-")
        assert resp.headers[fb.RESPONSE_ID_HEADER] == rid
        record = json.loads(sync_r.get(f"feedback:resp:{rid}"))
        assert record["consumer_id"] == consumer_id_for_key(KEY_A)
        assert record["user_id"] == f"{consumer_id_for_key(KEY_A)}:u1"
        assert record["session_id"] == "s1"
        assert record["agent_id"] == "aws"
        assert "content" not in record and "user_input" not in record

    def test_response_record_ttl(self, stores, keys):
        _, sync_r = stores
        with patch.object(fb.settings, "feedback_response_ttl_seconds", 1234), _backend(ANSWER):
            rid = _query(TestClient(gw.app)).json()["response_id"]
        assert 0 < sync_r.ttl(f"feedback:resp:{rid}") <= 1234

    def test_query_succeeds_when_recording_fails(self, keys):
        broken = MagicMock()
        broken.set = AsyncMock(side_effect=ConnectionError("redis down"))
        with patch.object(gw, "feedback_store", fb.FeedbackStore(broken)), \
             patch.object(fb, "feedback_errors") as errs, _backend(ANSWER):
            resp = _query(TestClient(gw.app))
        assert resp.status_code == 200
        assert resp.json()["response"] == "3 instances"
        errs.add.assert_called_once_with(1, {"reason": "record_failed"})

    def test_query_without_redis_client_still_answers(self, keys):
        with patch.object(gw, "feedback_store", fb.FeedbackStore(None)), _backend(ANSWER):
            resp = _query(TestClient(gw.app))
        assert resp.status_code == 200
        assert "response_id" in resp.json()

    def test_disabled_skips_recording(self, stores, keys):
        _, sync_r = stores
        with patch.object(fb.settings, "feedback_enabled", False), _backend(ANSWER):
            rid = _query(TestClient(gw.app)).json()["response_id"]
        assert sync_r.get(f"feedback:resp:{rid}") is None

    def test_non_dict_result_passes_through(self, stores, keys):
        with _backend(None):
            resp = _query(TestClient(gw.app))
        assert resp.status_code == 200
        assert resp.json() is None


class TestOpenAIResponseId:
    def test_completion_id_is_rateable_id(self, stores, keys):
        _, sync_r = stores
        with _backend(ANSWER):
            resp = TestClient(gw.app).post(
                "/v1/chat/completions",
                json={"model": "aigent-squad", "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-API-Key": KEY_A},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"].startswith("chatcmpl-")
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "3 instances"
        assert resp.headers[fb.RESPONSE_ID_HEADER] == body["id"]
        assert json.loads(sync_r.get(f"feedback:resp:{body['id']}"))["agent_id"] == "aws"

    def test_proxied_stream_sets_header_and_records_forced_agent(self, stores, keys):
        _, sync_r = stores
        stream_resp = MagicMock()

        async def _chunks():
            yield b"data: [DONE]\n\n"

        stream_resp.aiter_bytes = _chunks
        stream_resp.aclose = AsyncMock()
        with _backend(ANSWER) as sc:
            sc.process_stream = AsyncMock(return_value=stream_resp)
            resp = TestClient(gw.app).post(
                "/v1/chat/completions",
                json={"model": "aigent-squad-aws", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-API-Key": KEY_A},
            )
        rid = resp.headers[fb.RESPONSE_ID_HEADER]
        assert json.loads(sync_r.get(f"feedback:resp:{rid}"))["agent_id"] == "aws"

    def test_proxied_auto_route_stream_labelled_auto(self, stores, keys):
        _, sync_r = stores
        stream_resp = MagicMock()

        async def _chunks():
            yield b"data: [DONE]\n\n"

        stream_resp.aiter_bytes = _chunks
        stream_resp.aclose = AsyncMock()
        with _backend(ANSWER) as sc:
            sc.process_stream = AsyncMock(return_value=stream_resp)
            resp = TestClient(gw.app).post(
                "/v1/chat/completions",
                json={"model": "aigent-squad", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-API-Key": KEY_A},
            )
        rid = resp.headers[fb.RESPONSE_ID_HEADER]
        assert json.loads(sync_r.get(f"feedback:resp:{rid}"))["agent_id"] == "auto"

    def test_fallback_stream_chunks_carry_response_id(self, stores, keys):
        with _backend(ANSWER) as sc:
            sc.process_stream = AsyncMock(side_effect=RuntimeError("no stream"))
            resp = TestClient(gw.app).post(
                "/v1/chat/completions",
                json={"model": "aigent-squad", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-API-Key": KEY_A},
            )
        rid = resp.headers[fb.RESPONSE_ID_HEADER]
        frames = [line[6:] for line in resp.text.splitlines() if line.startswith("data: {")]
        assert frames and all(json.loads(f)["id"] == rid for f in frames)


# ─── POST /feedback ─────────────────────────────────────────────────────


class TestFeedbackEndpoint:
    def _rated_id(self, client, key=KEY_A):
        with _backend(ANSWER):
            return _query(client, key).json()["response_id"]

    def test_requires_auth(self, stores, keys):
        resp = TestClient(gw.app).post("/feedback", json={"response_id": "resp-abcdefgh", "score": "up"})
        assert resp.status_code == 401

    def test_owner_can_rate_once(self, stores, keys):
        _, sync_r = stores
        client = TestClient(gw.app)
        rid = self._rated_id(client)
        with patch.object(fb.settings, "feedback_record_ttl_seconds", 777):
            resp = _feedback(client, {"response_id": rid, "score": "down", "comment": "wrong region"})
        assert resp.status_code == 201
        assert resp.json() == {"response_id": rid, "status": "recorded"}
        stored = json.loads(sync_r.get(f"feedback:score:{rid}"))
        assert stored["bucket"] == "negative" and stored["comment"] == "wrong region"
        assert stored["agent_id"] == "aws" and stored["session_id"] == "s1"
        assert 0 < sync_r.ttl(f"feedback:score:{rid}") <= 777
        again = _feedback(client, {"response_id": rid, "score": "up"})
        assert again.status_code == 409
        assert again.json()["error"]["type"] == "already_rated"
        assert json.loads(sync_r.get(f"feedback:score:{rid}"))["bucket"] == "negative"

    def test_other_consumer_gets_404(self, stores, keys):
        _, sync_r = stores
        client = TestClient(gw.app)
        rid = self._rated_id(client, KEY_A)
        resp = _feedback(client, {"response_id": rid, "score": "up"}, key=KEY_B)
        assert resp.status_code == 404
        assert resp.json()["error"]["type"] == "unknown_response"
        assert sync_r.get(f"feedback:score:{rid}") is None
        # The owner can still rate afterwards.
        assert _feedback(client, {"response_id": rid, "score": "up"}).status_code == 201

    def test_internal_token_is_its_own_consumer(self, stores, keys):
        client = TestClient(gw.app)
        rid = self._rated_id(client, KEY_A)
        resp = client.post("/feedback", json={"response_id": rid, "score": 5},
                           headers={"X-Internal-Token": "internal-tok"})
        assert resp.status_code == 404

    def test_unknown_id_404(self, stores, keys):
        resp = _feedback(TestClient(gw.app), {"response_id": "resp-doesnotexist", "score": 4})
        assert resp.status_code == 404

    def test_expired_id_404(self, stores, keys):
        _, sync_r = stores
        client = TestClient(gw.app)
        rid = self._rated_id(client)
        sync_r.delete(f"feedback:resp:{rid}")  # what the TTL expiry leaves behind
        assert _feedback(client, {"response_id": rid, "score": "up"}).status_code == 404

    @pytest.mark.parametrize("body", [
        {"response_id": "resp-abcdefgh", "score": 0},
        {"response_id": "resp-abcdefgh", "score": 6},
        {"response_id": "resp-abcdefgh", "score": "meh"},
        {"response_id": "resp-abcdefgh", "score": "3"},
        {"response_id": "resp-abcdefgh", "score": True},
        {"response_id": "resp-abcdefgh", "score": 2.5},
        {"response_id": "short", "score": "up"},
        {"response_id": "resp-abc/../x", "score": "up"},
        {"response_id": "resp-abcdefgh"},
        {"score": "up"},
        {"response_id": "resp-abcdefgh", "score": "up", "user_id": "someone-else"},
    ])
    def test_validation_422(self, stores, keys, body):
        assert _feedback(TestClient(gw.app), body).status_code == 422

    def test_comment_length_cap(self, stores, keys):
        client = TestClient(gw.app)
        rid = self._rated_id(client)
        with patch.object(fb.settings, "feedback_comment_max_chars", 10):
            too_long = _feedback(client, {"response_id": rid, "score": "up", "comment": "x" * 11})
            ok = _feedback(client, {"response_id": rid, "score": "up", "comment": "x" * 10})
        assert too_long.status_code == 422
        assert ok.status_code == 201

    def test_store_unavailable_503(self, keys):
        broken = MagicMock()
        broken.get = AsyncMock(side_effect=ConnectionError("redis down"))
        with patch.object(gw, "feedback_store", fb.FeedbackStore(broken)):
            resp = _feedback(TestClient(gw.app), {"response_id": "resp-abcdefgh", "score": "up"})
        assert resp.status_code == 503
        assert resp.json()["error"]["type"] == "feedback_unavailable"

    def test_no_redis_client_503(self, keys):
        with patch.object(gw, "feedback_store", fb.FeedbackStore(None)):
            resp = _feedback(TestClient(gw.app), {"response_id": "resp-abcdefgh", "score": "up"})
        assert resp.status_code == 503

    def test_corrupt_record_503(self, stores, keys):
        _, sync_r = stores
        sync_r.set("feedback:resp:resp-corrupted", "not-json")
        resp = _feedback(TestClient(gw.app), {"response_id": "resp-corrupted", "score": "up"})
        assert resp.status_code == 503

    def test_disabled_404(self, stores, keys):
        with patch.object(fb.settings, "feedback_enabled", False):
            resp = _feedback(TestClient(gw.app), {"response_id": "resp-abcdefgh", "score": "up"})
        assert resp.status_code == 404
        assert resp.json()["error"]["type"] == "feedback_disabled"


# ─── metric labels ──────────────────────────────────────────────────────


class TestMetricLabels:
    @pytest.mark.parametrize("score,bucket", [
        ("up", "positive"), (5, "positive"), (4, "positive"), (3, "neutral"),
        (2, "negative"), (1, "negative"), ("down", "negative"),
    ])
    def test_score_metric_bounded(self, stores, keys, score, bucket):
        client = TestClient(gw.app)
        with _backend(ANSWER):
            rid = _query(client).json()["response_id"]
        with patch.object(fb, "feedback_score") as metric:
            assert _feedback(client, {"response_id": rid, "score": score}).status_code == 201
        metric.add.assert_called_once_with(1, {"agent_id": "aws", "score": bucket})

    @pytest.mark.parametrize("status,reason", [(404, "unknown_response"), (409, "already_rated")])
    def test_error_metric_reasons(self, stores, keys, status, reason):
        client = TestClient(gw.app)
        rid = "resp-neverissued"
        if reason == "already_rated":
            with _backend(ANSWER):
                rid = _query(client).json()["response_id"]
            _feedback(client, {"response_id": rid, "score": "up"})
        with patch.object(fb, "feedback_errors") as errs:
            assert _feedback(client, {"response_id": rid, "score": "up"}).status_code == status
        errs.add.assert_called_once_with(1, {"reason": reason})

    def test_unbounded_agent_id_becomes_other(self, stores, keys):
        _, sync_r = stores
        weird = {**ANSWER, "agent": "Some Agent / user-42 é"}
        client = TestClient(gw.app)
        with _backend(weird):
            rid = _query(client).json()["response_id"]
        assert json.loads(sync_r.get(f"feedback:resp:{rid}"))["agent_id"] == "other"
        with patch.object(fb, "feedback_score") as metric:
            _feedback(client, {"response_id": rid, "score": "up"})
        assert metric.add.call_args.args[1] == {"agent_id": "other", "score": "positive"}


# ─── helpers ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_agent_of_prefers_agent_key(self):
        assert fb.agent_of({"agent": "aws", "agent_id": "x"}) == "aws"
        assert fb.agent_of({"agent_id": "kubernetes"}) == "kubernetes"
        assert fb.agent_of({}) is None

    def test_ids_match_pattern(self):
        import re
        for rid in (fb.new_query_response_id(), fb.new_completion_id()):
            assert re.match(fb.RESPONSE_ID_PATTERN, rid)

    @pytest.mark.parametrize("value,label", [
        ("aws", "aws"), ("investigation", "investigation"), (None, "other"),
        (42, "other"), ("x" * 41, "other"), ("AWS", "other"),
    ])
    def test_agent_label(self, value, label):
        assert fb.agent_label(value) == label

    @pytest.mark.asyncio
    async def test_store_scopes_none_auth_to_internal(self):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = fb.FeedbackStore(r)
        await store.record_response("resp-internal1", None, "internal:u", "s", "aws")
        req = fb.FeedbackRequest(response_id="resp-internal1", score="up")
        assert await store.submit(req, AuthResult(consumer_id="key-other")) == "unknown_response"
        assert await store.submit(req, None) == "recorded"

    def test_auth_override_none_still_works(self, stores):
        # Other gateway tests override auth to None; /feedback must tolerate it.
        gw.app.dependency_overrides[require_edge_auth] = lambda: None
        resp = TestClient(gw.app).post("/feedback", json={"response_id": "resp-abcdefgh", "score": "up"})
        assert resp.status_code == 404
