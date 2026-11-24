"""F-024: history, rate limits and sessions are scoped to the authenticated consumer.

Before, the gateway forwarded the client-supplied ``user_id`` as-is, so any API-key
holder could read another user's history by sending its ids, and every OpenAI client
without ``user`` shared the session ``openai-librechat`` across all keys.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.gateway.auth import AuthResult, consumer_id_for_key, scoped_user_id
from src.gateway.main import app

KEY_A, KEY_B, INTERNAL = "key-alpha-123", "key-bravo-456", "internal-token-xyz"


@pytest.fixture(autouse=True)
def _real_auth(monkeypatch):
    app.dependency_overrides.clear()
    monkeypatch.setenv("INTERNAL_API_TOKEN", INTERNAL)
    monkeypatch.setenv("GATEWAY_API_KEYS", f"{KEY_A},{KEY_B}")
    monkeypatch.setenv("GATEWAY_KEY_AGENT_MAP", "")
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def forwarded():
    """Run requests through the real auth; capture what reaches the supervisor."""
    calls: list[dict] = []
    result = {"agent": "aws", "response": "ok", "confidence": 0.9}

    async def fake_process(**kwargs):
        calls.append(kwargs)
        return result

    async def fake_submit(job_id, stream):
        async def wrapper():
            async for item in stream:
                yield item
        return wrapper()

    with patch("src.gateway.main._check_admission", AsyncMock(return_value=None)) as adm, \
         patch("src.gateway.main._agent_names", ["aws"]), \
         patch("src.gateway.main.worker_pool") as pool, \
         patch("src.gateway.main.supervisor_client") as sc:
        pool.has_capacity.return_value = True
        pool.submit = AsyncMock(side_effect=fake_submit)
        sc.is_supervisor_ready = AsyncMock(return_value=True)
        sc.process = AsyncMock(side_effect=fake_process)
        yield calls, adm


def test_consumer_id_is_stable_short_and_not_the_key():
    cid = consumer_id_for_key(KEY_A)
    assert cid == consumer_id_for_key(KEY_A) != consumer_id_for_key(KEY_B)
    assert KEY_A not in cid and cid.startswith("key-") and len(cid) == 20


def test_scoped_user_id_defaults_to_internal_without_auth():
    assert scoped_user_id(None, "u1") == "internal:u1"
    assert scoped_user_id(AuthResult(consumer_id="key-x"), "u1") == "key-x:u1"


def test_same_user_and_session_from_two_keys_never_share_history(forwarded):
    calls, _ = forwarded
    client = TestClient(app)
    body = {"user_input": "q", "user_id": "alice", "session_id": "s1"}
    assert client.post("/query", json=body, headers={"X-API-Key": KEY_A}).status_code == 200
    assert client.post("/query", json=body, headers={"Authorization": f"Bearer {KEY_B}"}).status_code == 200
    a, b = calls[0]["user_id"], calls[1]["user_id"]
    assert a != b
    assert a == f"{consumer_id_for_key(KEY_A)}:alice" and b == f"{consumer_id_for_key(KEY_B)}:alice"
    assert calls[0]["session_id"] == calls[1]["session_id"] == "s1"  # history key differs via user


def test_internal_token_consumer(forwarded):
    calls, adm = forwarded
    client = TestClient(app)
    client.post("/query", json={"user_input": "q", "user_id": "ops", "session_id": "s"},
                headers={"X-Internal-Token": INTERNAL})
    assert calls[0]["user_id"] == "internal:ops"
    assert adm.await_args.args[0] == "internal:ops"  # rate limits scoped the same way


def test_openai_anonymous_clients_get_a_session_per_key(forwarded):
    calls, _ = forwarded
    client = TestClient(app)
    body = {"model": "aigent-squad", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    client.post("/v1/chat/completions", json=body, headers={"X-API-Key": KEY_A})
    client.post("/v1/chat/completions", json=body, headers={"X-API-Key": KEY_B})
    s_a, s_b = calls[0]["session_id"], calls[1]["session_id"]
    assert s_a != s_b  # no more one shared "openai-librechat" across every key
    assert s_a == f"openai-{consumer_id_for_key(KEY_A)}:librechat"


def test_openai_explicit_user_is_scoped_too(forwarded):
    calls, _ = forwarded
    client = TestClient(app)
    body = {"model": "aigent-squad", "user": "bob", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/chat/completions", json=body, headers={"X-API-Key": KEY_A},
                )
    assert calls[0]["user_id"] == f"{consumer_id_for_key(KEY_A)}:bob"


def test_unauthenticated_still_401(forwarded):
    client = TestClient(app)
    assert client.post("/query", json={"user_input": "q", "user_id": "a", "session_id": "s"}).status_code == 401
