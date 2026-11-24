"""Spec 46 Phase 1 — history store backends (Redis default, Postgres opt-in) + factory."""
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from src.core import history_stores
from src.core.history_stores import PostgresHistoryStore, RedisHistoryStore
from src.core.state_store import (
    HISTORY_TTL_SECONDS,
    ConversationMessage,
    DynamoDBHistoryStore,
    build_history_store,
)


def _msg(content: str, role: str = "user", agent: str = "aws") -> ConversationMessage:
    return ConversationMessage(role=role, content=content, timestamp=f"ts-{content}", agent_id=agent)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_default_backend_is_redis():
    from src.core.config import Settings
    assert Settings.model_fields["history_backend"].default == "redis"


def test_factory_builds_each_backend():
    assert isinstance(build_history_store("redis"), RedisHistoryStore)
    assert isinstance(build_history_store("postgres"), PostgresHistoryStore)
    with patch("src.core.state_store.boto3"):
        assert isinstance(build_history_store("dynamodb"), DynamoDBHistoryStore)


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unknown HISTORY_BACKEND"):
        build_history_store("mongo")


def test_settings_rejects_invalid_backend(monkeypatch):
    from pydantic import ValidationError
    from src.core.config import Settings
    monkeypatch.setenv("HISTORY_BACKEND", "mongo")
    with pytest.raises(ValidationError):
        Settings()


# ---------------------------------------------------------------------------
# Redis backend — contract
# ---------------------------------------------------------------------------

@pytest.fixture
def redis_store():
    return RedisHistoryStore(client=fakeredis.FakeAsyncRedis(decode_responses=True))


@pytest.mark.asyncio
async def test_redis_roundtrip_oldest_first(redis_store):
    for i in range(3):
        await redis_store.save_chat_message("u1", "s1", "aws", _msg(f"m{i}"))
    got = await redis_store.fetch_chat("u1", "s1", "aws")
    assert [m.content for m in got] == ["m0", "m1", "m2"]
    assert got[0].timestamp == "ts-m0" and got[0].agent_id == "aws"


@pytest.mark.asyncio
async def test_redis_per_agent_isolation(redis_store):
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("a", agent="aws"))
    await redis_store.save_chat_message("u1", "s1", "finops", _msg("f", agent="finops"))
    assert [m.content for m in await redis_store.fetch_chat("u1", "s1", "aws")] == ["a"]
    assert [m.content for m in await redis_store.fetch_chat("u1", "s1", "finops")] == ["f"]


@pytest.mark.asyncio
async def test_redis_all_chats_ordered_by_time_across_agents(redis_store):
    await redis_store.save_chat_message("u1", "s1", "finops", _msg("1", agent="finops"))
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("2", agent="aws"))
    await redis_store.save_chat_message("u1", "s1", "finops", _msg("3", agent="finops"))
    got = await redis_store.fetch_all_chats("u1", "s1")
    assert [(m.content, m.agent_id) for m in got] == [("1", "finops"), ("2", "aws"), ("3", "finops")]


@pytest.mark.asyncio
async def test_redis_sessions_and_users_are_isolated(redis_store):
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("x"))
    assert await redis_store.fetch_chat("u1", "s2", "aws") == []
    assert await redis_store.fetch_chat("u2", "s1", "aws") == []
    assert await redis_store.fetch_all_chats("u2", "s1") == []


@pytest.mark.asyncio
async def test_redis_limit_returns_latest_n(redis_store):
    for i in range(10):
        await redis_store.save_chat_message("u1", "s1", "aws", _msg(f"m{i}"))
    got = await redis_store.fetch_chat("u1", "s1", "aws", max_messages=3)
    assert [m.content for m in got] == ["m7", "m8", "m9"]
    got_all = await redis_store.fetch_all_chats("u1", "s1", max_messages=2)
    assert [m.content for m in got_all] == ["m8", "m9"]


@pytest.mark.asyncio
async def test_redis_identical_messages_are_kept(redis_store):
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("same"))
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("same"))
    assert len(await redis_store.fetch_chat("u1", "s1", "aws")) == 2


@pytest.mark.asyncio
async def test_redis_keys_expire_and_hide_raw_ids(redis_store):
    await redis_store.save_chat_message("alice@example.com", "sess-1", "aws", _msg("x"))
    keys = await redis_store.redis.keys("aigent:history:*")
    assert len(keys) == 2
    for k in keys:
        assert "alice" not in k and "sess-1" not in k
        ttl = await redis_store.redis.ttl(k)
        assert 0 < ttl <= HISTORY_TTL_SECONDS


@pytest.mark.asyncio
async def test_redis_messages_older_than_retention_are_not_returned(redis_store):
    real_time = history_stores.time.time
    with patch.object(history_stores.time, "time", return_value=real_time() - HISTORY_TTL_SECONDS - 60):
        await redis_store.save_chat_message("u1", "s1", "aws", _msg("old"))
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("new"))
    assert [m.content for m in await redis_store.fetch_chat("u1", "s1", "aws")] == ["new"]
    assert [m.content for m in await redis_store.fetch_all_chats("u1", "s1")] == ["new"]


@pytest.mark.asyncio
async def test_redis_caps_messages_per_key(redis_store, monkeypatch):
    monkeypatch.setattr(history_stores, "REDIS_MAX_MESSAGES_PER_KEY", 5)
    for i in range(8):
        await redis_store.save_chat_message("u1", "s1", "aws", _msg(f"m{i}"))
    for k in await redis_store.redis.keys("aigent:history:*"):
        assert await redis_store.redis.zcard(k) == 5


@pytest.mark.asyncio
async def test_redis_fail_open_on_errors():
    broken = MagicMock()
    broken.pipeline.side_effect = ConnectionError("redis down")
    broken.zrevrangebyscore = AsyncMock(side_effect=ConnectionError("redis down"))
    store = RedisHistoryStore(client=broken)
    with patch("src.core.history_stores._record_error") as rec2:
        await store.save_chat_message("u1", "s1", "aws", _msg("x"))  # no raise
        assert await store.fetch_chat("u1", "s1", "aws") == []
        assert await store.fetch_all_chats("u1", "s1") == []
    assert [c.args for c in rec2.call_args_list] == [("redis", "save"), ("redis", "fetch"), ("redis", "fetch")]


@pytest.mark.asyncio
async def test_redis_ping(redis_store):
    assert await redis_store.ping() is True


def test_redis_default_client_is_lazy_and_uses_settings():
    with patch("src.core.history_stores.aioredis.from_url") as from_url:
        RedisHistoryStore()
    url = from_url.call_args.args[0]
    assert url.endswith(":6379") or ":" in url
    assert from_url.call_args.kwargs["decode_responses"] is True


# ---------------------------------------------------------------------------
# Postgres backend — SQL contract against a fake pool
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None, fail: bool = False):
        self.rows = rows or []
        self.fail = fail
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        if self.fail:
            raise OSError("pg down")
        self.executed.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if self.fail:
            raise OSError("pg down")
        self.executed.append((sql, args))
        return self.rows

    async def fetchval(self, sql: str) -> int:
        return 1


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self.conn = conn

    def acquire(self) -> "_FakePool":
        return self

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_postgres_save_inserts_with_expiry_and_purges():
    conn = _FakeConn()
    store = PostgresHistoryStore(pool=_FakePool(conn))
    await store.save_chat_message("u1", "s1", "aws", _msg("hello"))
    (insert_sql, args), (purge_sql, _) = conn.executed
    assert "INSERT INTO aigent_history" in insert_sql
    pk, sk, agent, role, content, ts, created, expires = args
    assert (pk, agent, role, content, ts) == ('["u1","s1"]', "aws", "user", "hello", "ts-hello")
    assert sk.startswith("aws#")
    assert (expires - created).total_seconds() == HISTORY_TTL_SECONDS
    assert "DELETE FROM aigent_history" in purge_sql and "LIMIT 100" in purge_sql


@pytest.mark.asyncio
async def test_postgres_fetch_chat_filters_agent_and_returns_oldest_first():
    rows = [  # SQL returns newest first
        {"agent_id": "aws", "role": "assistant", "content": "a1", "ts": "t2"},
        {"agent_id": "aws", "role": "user", "content": "q1", "ts": "t1"},
    ]
    conn = _FakeConn(rows)
    store = PostgresHistoryStore(pool=_FakePool(conn))
    got = await store.fetch_chat("u1", "s1", "aws", max_messages=7)
    assert [m.content for m in got] == ["q1", "a1"]
    sql, args = conn.executed[0]
    assert "agent_id = $2" in sql and "expires_at > now()" in sql and "ORDER BY created_at DESC" in sql
    assert args == ('["u1","s1"]', "aws", 7)


@pytest.mark.asyncio
async def test_postgres_fetch_all_chats_no_agent_filter():
    conn = _FakeConn([{"agent_id": "finops", "role": "user", "content": "x", "ts": "t"}])
    store = PostgresHistoryStore(pool=_FakePool(conn))
    got = await store.fetch_all_chats("u1", "s1", max_messages=4)
    assert got[0].agent_id == "finops"
    sql, args = conn.executed[0]
    assert "agent_id" not in sql.split("WHERE", 1)[1] and args == ('["u1","s1"]', 4)


@pytest.mark.asyncio
async def test_postgres_fail_open_on_query_errors():
    store = PostgresHistoryStore(pool=_FakePool(_FakeConn(fail=True)))
    with patch("src.core.history_stores._record_error") as rec:
        await store.save_chat_message("u1", "s1", "aws", _msg("x"))
        assert await store.fetch_chat("u1", "s1", "aws") == []
        assert await store.fetch_all_chats("u1", "s1") == []
    assert [c.args for c in rec.call_args_list] == [("postgres", "save"), ("postgres", "fetch"), ("postgres", "fetch")]


@pytest.mark.asyncio
async def test_postgres_connect_failure_is_fail_open_with_backoff():
    store = PostgresHistoryStore()
    create_pool = AsyncMock(side_effect=OSError("no route"))
    with patch("asyncpg.create_pool", create_pool), \
         patch("src.core.history_stores._record_error") as rec:
        assert await store.fetch_chat("u1", "s1", "aws") == []
        await store.save_chat_message("u1", "s1", "aws", _msg("x"))
        assert await store.ping() is False
    assert create_pool.await_count == 1  # backoff: no reconnect storm
    assert rec.call_args_list[0].args == ("postgres", "connect")


@pytest.mark.asyncio
async def test_postgres_connect_creates_schema_once():
    conn = _FakeConn()
    pool = _FakePool(conn)
    create_pool = AsyncMock(return_value=pool)
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", create_pool):
        await store.ping()
        await store.ping()
    assert create_pool.await_count == 1
    statements = [sql for sql, _ in conn.executed]
    schema_sql, = [sql for sql in statements if "CREATE TABLE" in sql]
    # DDL runs under a session advisory lock (replicas starting together)
    assert "pg_advisory_lock" in statements[0] and "pg_advisory_unlock" in statements[-1]
    assert "CREATE TABLE IF NOT EXISTS aigent_history" in schema_sql
    assert "vector" not in schema_sql.lower()


# ---------------------------------------------------------------------------
# Optional live Postgres check (set HISTORY_PG_LIVE=1 + POSTGRES_* to run)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("HISTORY_PG_LIVE") != "1", reason="live Postgres not requested")
async def test_postgres_live_roundtrip():  # pragma: no cover - opt-in integration
    import uuid
    store = PostgresHistoryStore()
    user = f"live-{uuid.uuid4().hex[:8]}"
    await store.save_chat_message(user, "s1", "aws", _msg("q", agent="aws"))
    await store.save_chat_message(user, "s1", "finops", _msg("f", agent="finops"))
    await store.save_chat_message(user, "s1", "aws", _msg("a", role="assistant", agent="aws"))
    assert [m.content for m in await store.fetch_chat(user, "s1", "aws")] == ["q", "a"]
    assert [m.content for m in await store.fetch_all_chats(user, "s1")] == ["q", "f", "a"]
    assert await store.ping() is True


# ---------------------------------------------------------------------------
# Spec 46 review fixes (F2, F3, F10, T16 bugs)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redis_keys_share_a_cluster_hash_tag(redis_store):
    await redis_store.save_chat_message("u1", "s1", "aws", _msg("x"))
    keys = sorted(await redis_store.redis.keys("aigent:history:*"))
    tags = {k[k.index("{"):k.index("}") + 1] for k in keys}
    assert len(keys) == 2 and len(tags) == 1  # same slot → MULTI valid on Redis Cluster


@pytest.mark.asyncio
async def test_postgres_schema_failure_closes_the_pool():
    class _DenyConn(_FakeConn):
        async def execute(self, sql: str, *args: Any) -> str:
            if "CREATE TABLE" in sql:
                raise PermissionError("permission denied for schema public")
            return await super().execute(sql, *args)

    class _ClosablePool(_FakePool):
        closed = 0

        async def close(self) -> None:
            type(self).closed += 1

    pool = _ClosablePool(_DenyConn())
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", AsyncMock(return_value=pool)), \
         patch("src.core.history_stores._record_error"):
        assert await store.fetch_chat("u1", "s1", "aws") == []
    assert _ClosablePool.closed == 1  # no leaked connections on retry
    # the advisory lock is always released, even when the DDL fails
    assert "pg_advisory_unlock" in pool.conn.executed[-1][0]


@pytest.mark.asyncio
async def test_postgres_concurrent_requests_do_not_repeat_a_failed_connect():
    import asyncio

    async def slow_fail(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(0.01)
        raise OSError("blackholed")

    create_pool = AsyncMock(side_effect=slow_fail)
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", create_pool), patch("src.core.history_stores._record_error"):
        await asyncio.gather(*[store.fetch_chat("u", "s", "aws") for _ in range(5)])
    assert create_pool.await_count == 1


def test_postgres_connect_timeout_below_readiness_probe():
    from src.core import history_stores as hs
    from src.core.health import DependencyChecker
    assert hs.PG_CONNECT_TIMEOUT_SECONDS < DependencyChecker()._timeout
