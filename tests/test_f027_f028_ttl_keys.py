"""F-027 (DynamoDB history ttl filter) and F-028 (Redis key TTLs + hashed ids).

F-027 contract: DynamoDB history reads never return an item whose ``ttl`` is
already past, and still return up to ``max_messages`` unexpired items even when
DynamoDB's ``Limit`` (applied before ``FilterExpression``) yields short pages —
with a bounded number of pages.

F-028 contract: gateway ``job:<id>`` records expire; Redis key names for the
rate limiter, the session token budget and the session lock contain a SHA-256
digest of the id, never the raw id, under unchanged prefixes.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest

from src.core.rate_limiter import AdmissionGuard
from src.core.redis_keys import hash_id
from src.core.session_lock import SessionLock
from src.core.state_store import _MAX_QUERY_PAGES, DynamoDBHistoryStore
from src.core.token_budget import SessionBudgetTracker
from src.gateway.worker_pool import WorkerPool


# ─── F-027 ───────────────────────────────────────────────────────────


@pytest.fixture
def store_and_table():
    with patch("src.core.state_store.boto3") as mock_boto3:
        table = MagicMock()
        mock_boto3.resource.return_value.Table.return_value = table
        store = DynamoDBHistoryStore()
        store.table = table
    return store, table


def _item(n: int, agent: str = "aws") -> dict[str, Any]:
    return {
        "role": "user", "content": f"m{n}", "timestamp": f"t{n}",
        "agent_id": agent, "ttl": int(time.time()) + 3600,
    }


@pytest.mark.asyncio
async def test_fetch_chat_filters_on_ttl_with_reserved_word_alias(store_and_table):
    store, table = store_and_table
    table.query.return_value = {"Items": [_item(1)]}

    before = int(time.time())
    await store.fetch_chat("u1", "s1", "aws", max_messages=5)
    after = int(time.time())

    kwargs = table.query.call_args.kwargs
    assert kwargs["FilterExpression"] == "#ttl > :now"
    assert kwargs["ExpressionAttributeNames"] == {"#ttl": "ttl"}
    assert before <= kwargs["ExpressionAttributeValues"][":now"] <= after
    assert kwargs["ExpressionAttributeValues"][":pk"] == "u1#s1"
    assert kwargs["ExpressionAttributeValues"][":agent"] == "aws#"  # '#': no prefix overlap
    assert kwargs["KeyConditionExpression"] == "pk = :pk AND begins_with(sk, :agent)"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 5
    assert "ExclusiveStartKey" not in kwargs


@pytest.mark.asyncio
async def test_fetch_all_chats_filters_on_ttl(store_and_table):
    store, table = store_and_table
    table.query.return_value = {"Items": [_item(1)]}

    await store.fetch_all_chats("u1", "s1", max_messages=7)

    kwargs = table.query.call_args.kwargs
    assert kwargs["FilterExpression"] == "#ttl > :now"
    assert kwargs["ExpressionAttributeNames"] == {"#ttl": "ttl"}
    assert ":now" in kwargs["ExpressionAttributeValues"]
    assert kwargs["KeyConditionExpression"] == "pk = :pk"
    # sk orders by agent name, so the whole session is read and sorted by time.
    assert "Limit" not in kwargs


@pytest.mark.asyncio
async def test_short_filtered_page_follows_last_evaluated_key(store_and_table):
    """Limit is applied before the filter: a short page with more data paginates."""
    store, table = store_and_table
    # Newest-first pages: page 1 had 3 evaluated items, only 1 survived the filter.
    table.query.side_effect = [
        {"Items": [_item(5)], "LastEvaluatedKey": {"pk": "u1#s1", "sk": "aws#3"}},
        {"Items": [_item(2), _item(1)], "LastEvaluatedKey": {"pk": "u1#s1", "sk": "aws#1"}},
    ]

    msgs = await store.fetch_chat("u1", "s1", "aws", max_messages=3)

    assert table.query.call_count == 2
    second = table.query.call_args_list[1].kwargs
    assert second["ExclusiveStartKey"] == {"pk": "u1#s1", "sk": "aws#3"}
    # Returned oldest-first (callers feed history chronologically).
    assert [m.content for m in msgs] == ["m1", "m2", "m5"]


@pytest.mark.asyncio
async def test_all_chats_keeps_the_newest_by_timestamp(store_and_table):
    store, table = store_and_table

    def _ts(n: int, agent: str) -> dict[str, Any]:
        return {**_item(n, agent), "timestamp": f"2026-11-24T10:0{n}:00+00:00"}

    table.query.side_effect = [
        {"Items": [_ts(9, "aws"), _ts(6, "aws")], "LastEvaluatedKey": {"sk": "a"}},
        {"Items": [_ts(8, "k8s"), _ts(7, "k8s")]},
    ]

    msgs = await store.fetch_all_chats("u1", "s1", max_messages=2)

    assert table.query.call_count == 2
    # Keeps the two NEWEST across agents, returned oldest-first.
    assert [m.content for m in msgs] == ["m8", "m9"]


@pytest.mark.asyncio
async def test_pagination_is_bounded(store_and_table):
    """A session full of expired rows cannot turn one read into an unbounded scan."""
    store, table = store_and_table
    table.query.return_value = {"Items": [], "LastEvaluatedKey": {"sk": "more"}}

    msgs = await store.fetch_chat("u1", "s1", "aws", max_messages=20)

    assert msgs == []
    assert table.query.call_count == _MAX_QUERY_PAGES


@pytest.mark.asyncio
async def test_stops_when_no_last_evaluated_key(store_and_table):
    store, table = store_and_table
    table.query.return_value = {"Items": [_item(1)]}

    msgs = await store.fetch_chat("u1", "s1", "aws", max_messages=20)

    assert table.query.call_count == 1
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_non_positive_max_messages_skips_query(store_and_table):
    store, table = store_and_table

    assert await store.fetch_chat("u1", "s1", "aws", max_messages=0) == []
    assert await store.fetch_all_chats("u1", "s1", max_messages=-1) == []
    table.query.assert_not_called()


@pytest.mark.asyncio
async def test_error_on_later_page_is_fail_open(store_and_table):
    store, table = store_and_table
    table.query.side_effect = [
        {"Items": [_item(1)], "LastEvaluatedKey": {"sk": "x"}},
        Exception("throttled"),
    ]

    assert await store.fetch_all_chats("u1", "s1", max_messages=5) == []


# ─── F-028: hashed ids ───────────────────────────────────────────────


def test_hash_id_is_deterministic_sha256():
    value = "key-0123abcd:alice@example.com"
    assert hash_id(value) == hashlib.sha256(value.encode("utf-8")).hexdigest()
    assert hash_id(value) == hash_id(value)
    assert hash_id("a") != hash_id("b")


def test_hash_id_handles_unicode_and_separators():
    digest = hash_id("key-x:josé/ü:{tag}*")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


@pytest.mark.asyncio
async def test_rate_key_uses_hashed_user_id():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    guard = AdmissionGuard(redis, rate_per_minute=5, daily_budget_usd=10.0)
    user = "key-0123abcd:alice"

    allowed, _ = await guard.check_rate(user)

    keys = [k async for k in redis.scan_iter("rate:user:*")]
    assert allowed is True
    assert keys == [f"rate:user:{hash_id(user)}"]
    assert all(user not in k and "alice" not in k for k in keys)
    assert await redis.ttl(keys[0]) > 0


@pytest.mark.asyncio
async def test_rate_limit_still_enforced_per_user_with_hashed_key():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    guard = AdmissionGuard(redis, rate_per_minute=2, daily_budget_usd=10.0)

    assert (await guard.check_rate("key-a:u"))[0] is True
    assert (await guard.check_rate("key-a:u"))[0] is True
    assert (await guard.check_rate("key-a:u"))[0] is False
    # A different consumer-scoped id is a different bucket.
    assert (await guard.check_rate("key-b:u"))[0] is True


def test_token_budget_key_uses_hashed_session_id():
    client = fakeredis.FakeRedis(decode_responses=True)
    tracker = SessionBudgetTracker(max_tokens_per_session=1000, redis_client=client)
    session = "sess:raw/id-123"

    tracker.record_usage(session, input_tokens=10, output_tokens=5)

    keys = list(client.scan_iter("token_budget:session:*"))
    assert keys == [f"token_budget:session:{hash_id(session)}"]
    assert session not in keys[0]
    assert tracker.get_usage(session) == 15


@pytest.mark.asyncio
async def test_session_lock_key_uses_hashed_session_id():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session = "key-0123abcd:alice#chat-1"

    async with SessionLock(session, redis, ttl=30):
        keys = [k async for k in redis.scan_iter("session_lock:*")]
        assert keys == [f"session_lock:{hash_id(session)}"]
        assert "alice" not in keys[0]

    assert [k async for k in redis.scan_iter("session_lock:*")] == []


# ─── F-028: job record TTL ───────────────────────────────────────────


def _pool(redis: Any, **kw: Any) -> WorkerPool:
    return WorkerPool(
        max_concurrent=2, job_timeout=5.0, first_byte_timeout=1.0,
        idle_timeout=1.0, cancel_poll_interval=0.02, redis_client=redis, **kw,
    )


async def _drain(pool: WorkerPool, job_id: str) -> list[str]:
    async def gen():
        yield "a"
        yield "b"
    stream = await pool.submit(job_id, gen())
    return [c async for c in stream]


@pytest.mark.asyncio
async def test_job_record_written_with_configured_ttl():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pool = _pool(redis, job_record_ttl=120)

    await _drain(pool, "j-ttl")

    raw = await redis.get("job:j-ttl")
    assert json.loads(raw)["status"] == "completed"
    ttl = await redis.ttl("job:j-ttl")
    assert 0 < ttl <= 120


@pytest.mark.asyncio
async def test_job_record_default_ttl_is_24h():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pool = _pool(redis)

    await _drain(pool, "j-default")

    ttl = await redis.ttl("job:j-default")
    assert 86400 - 5 < ttl <= 86400


def test_setting_default_and_validation():
    from pydantic import ValidationError

    from src.core.config import Settings

    assert Settings().gateway_job_record_ttl_seconds == 86400
    with patch.dict("os.environ", {"GATEWAY_JOB_RECORD_TTL_SECONDS": "3600"}):
        assert Settings().gateway_job_record_ttl_seconds == 3600
    with patch.dict("os.environ", {"GATEWAY_JOB_RECORD_TTL_SECONDS": "0"}):
        with pytest.raises(ValidationError):
            Settings()
