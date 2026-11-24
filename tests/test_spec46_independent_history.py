"""Independent verification of spec 46 history backends (task T16).

Written from the spec's acceptance criteria and the public store contract
(``save_chat_message`` / ``fetch_chat`` / ``fetch_all_chats`` / ``ping``), not
from the implementer's tests. Redis runs against fakeredis; Postgres runs
against a small in-memory asyncpg shim (no real database in ``make test``).
"""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import src.core.history_stores as hs
from src.core.config import Settings, settings
from src.core.conversation import HISTORY_TTL_SECONDS, ConversationMessage
from src.core.health import DependencyChecker
from src.core.state_store import DynamoDBHistoryStore, build_history_store


def _msg(content: str, role: str = "user", agent_id: Any = None) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content,
        timestamp=datetime.now(timezone.utc).isoformat(), agent_id=agent_id,
    )


class _Clock:
    """Controllable replacement for ``time.time`` inside the history module."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(hs, "time", SimpleNamespace(time=c.time, monotonic=c.monotonic))
    return c


@pytest.fixture
def errors(monkeypatch):
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(hs, "_record_error", lambda backend, op: recorded.append((backend, op)))
    return recorded


# ---------------------------------------------------------------------------
# Redis backend contract
# ---------------------------------------------------------------------------

class TestRedisContract:
    @pytest.mark.asyncio
    async def test_per_agent_isolation_and_cross_agent_time_order(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        plan = [("aws", "a1"), ("kubernetes", "k1"), ("aws", "a2"), ("finops", "f1"), ("kubernetes", "k2")]
        for agent, text in plan:
            clock.now += 1
            await store.save_chat_message("u1", "s1", agent, _msg(text))

        aws = await store.fetch_chat("u1", "s1", "aws")
        k8s = await store.fetch_chat("u1", "s1", "kubernetes")
        assert [m.content for m in aws] == ["a1", "a2"]
        assert [m.content for m in k8s] == ["k1", "k2"]
        assert {m.agent_id for m in aws} == {"aws"}

        everything = await store.fetch_all_chats("u1", "s1")
        # Ordered by write time across agents, not grouped by agent name.
        assert [m.content for m in everything] == ["a1", "k1", "a2", "f1", "k2"]
        assert [m.agent_id for m in everything] == [a for a, _ in plan]

    @pytest.mark.asyncio
    async def test_sessions_and_users_do_not_leak(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        await store.save_chat_message("u1", "s1", "aws", _msg("mine"))
        clock.now += 1
        await store.save_chat_message("u1", "s2", "aws", _msg("other-session"))
        clock.now += 1
        await store.save_chat_message("u2", "s1", "aws", _msg("other-user"))

        assert [m.content for m in await store.fetch_all_chats("u1", "s1")] == ["mine"]
        assert [m.content for m in await store.fetch_chat("u1", "s2", "aws")] == ["other-session"]
        assert [m.content for m in await store.fetch_chat("u2", "s1", "aws")] == ["other-user"]
        assert await store.fetch_chat("u3", "s1", "aws") == []

    @pytest.mark.asyncio
    async def test_agent_name_prefix_does_not_match_other_agent(self, fake_redis, clock):
        # DynamoDB filters with begins_with(sk, agent); the Redis store must not
        # inherit a prefix match ("aws" vs "aws-cost").
        store = hs.RedisHistoryStore(client=fake_redis)
        await store.save_chat_message("u", "s", "aws-cost", _msg("cost"))
        clock.now += 1
        await store.save_chat_message("u", "s", "aws", _msg("plain"))
        assert [m.content for m in await store.fetch_chat("u", "s", "aws")] == ["plain"]

    @pytest.mark.asyncio
    async def test_oldest_first_and_last_n(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        for i in range(10):
            clock.now += 1
            await store.save_chat_message("u", "s", "aws", _msg(f"m{i}"))
        last3 = await store.fetch_chat("u", "s", "aws", max_messages=3)
        assert [m.content for m in last3] == ["m7", "m8", "m9"]
        last1 = await store.fetch_all_chats("u", "s", max_messages=1)
        assert [m.content for m in last1] == ["m9"]

    @pytest.mark.asyncio
    async def test_max_messages_zero_returns_nothing(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        assert await store.fetch_chat("u", "s", "aws", max_messages=0) == []
        assert await store.fetch_all_chats("u", "s", max_messages=0) == []

    @pytest.mark.asyncio
    async def test_retention_window_boundary(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        t0 = clock.now
        await store.save_chat_message("u", "s", "aws", _msg("old"))

        clock.now = t0 + HISTORY_TTL_SECONDS - 1
        assert [m.content for m in await store.fetch_chat("u", "s", "aws")] == ["old"]

        clock.now = t0 + HISTORY_TTL_SECONDS + 1
        assert await store.fetch_chat("u", "s", "aws") == []
        assert await store.fetch_all_chats("u", "s") == []

    @pytest.mark.asyncio
    async def test_expired_members_are_pruned_on_write(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        t0 = clock.now
        await store.save_chat_message("u", "s", "aws", _msg("old"))
        clock.now = t0 + HISTORY_TTL_SECONDS + 10
        await store.save_chat_message("u", "s", "aws", _msg("new"))
        for key in await fake_redis.keys("*"):
            members = await fake_redis.zrange(key, 0, -1)
            assert [json.loads(m)["content"] for m in members] == ["new"]

    @pytest.mark.asyncio
    async def test_keys_carry_native_expiry(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        keys = await fake_redis.keys("*")
        assert keys, "a save must create at least one key"
        for key in keys:
            ttl = await fake_redis.ttl(key)
            assert 0 < ttl <= HISTORY_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_cap_bounds_memory_but_keeps_latest(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        total = hs.REDIS_MAX_MESSAGES_PER_KEY + 7
        for i in range(total):
            clock.now += 1
            await store.save_chat_message("u", "s", "aws", _msg(f"m{i}"))
        for key in await fake_redis.keys("*"):
            assert await fake_redis.zcard(key) == hs.REDIS_MAX_MESSAGES_PER_KEY
        got = await store.fetch_all_chats("u", "s", max_messages=total)
        assert len(got) == hs.REDIS_MAX_MESSAGES_PER_KEY
        assert got[0].content == f"m{total - hs.REDIS_MAX_MESSAGES_PER_KEY}"
        assert got[-1].content == f"m{total - 1}"

    @pytest.mark.asyncio
    async def test_key_names_do_not_contain_raw_identifiers(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        user, session, agent = "alice@example.com", "session-XYZ-123", "observability"
        await store.save_chat_message(user, session, agent, _msg("hi"))
        keys = await fake_redis.keys("*")
        assert len(keys) == 2  # one per session, one per session+agent
        for key in keys:
            assert key.startswith("aigent:history:")
            for raw in (user, session, agent, "alice"):
                assert raw not in key

    @pytest.mark.asyncio
    async def test_keys_are_deterministic_across_store_instances(self, fake_redis, clock):
        await hs.RedisHistoryStore(client=fake_redis).save_chat_message("u", "s", "aws", _msg("one"))
        clock.now += 1
        got = await hs.RedisHistoryStore(client=fake_redis).fetch_chat("u", "s", "aws")
        assert [m.content for m in got] == ["one"]

    @pytest.mark.asyncio
    async def test_concurrent_saves_lose_nothing(self, fake_redis):
        store = hs.RedisHistoryStore(client=fake_redis)
        await asyncio.gather(*[
            store.save_chat_message("u", "s", f"agent{i % 3}", _msg(f"c{i}")) for i in range(60)
        ])
        everything = await store.fetch_all_chats("u", "s", max_messages=100)
        assert sorted(m.content for m in everything) == sorted(f"c{i}" for i in range(60))
        per_agent = [await store.fetch_chat("u", "s", f"agent{a}", max_messages=100) for a in range(3)]
        assert [len(p) for p in per_agent] == [20, 20, 20]

    @pytest.mark.asyncio
    async def test_unicode_and_large_content_round_trip(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        text = "ação 🚀 日本語 ​ \"quotes\" \\ newline\n" + "x" * 200_000
        await store.save_chat_message("ü", "sé", "aws", _msg(text, role="assistant"))
        [got] = await store.fetch_chat("ü", "sé", "aws")
        assert got.content == text
        assert got.role == "assistant"

    @pytest.mark.asyncio
    async def test_agent_id_comes_from_the_save_call(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        msg = _msg("x", agent_id=None)
        await store.save_chat_message("u", "s", "security", msg)
        [got] = await store.fetch_all_chats("u", "s")
        assert got.agent_id == "security"
        assert got.timestamp == msg.timestamp
        assert got.quality_assessment is None

    @pytest.mark.asyncio
    async def test_hash_in_ids_does_not_merge_histories(self, fake_redis, clock):
        store = hs.RedisHistoryStore(client=fake_redis)
        await store.save_chat_message("team#alpha", "s1", "aws", _msg("first pair"))
        assert await store.fetch_chat("team", "alpha#s1", "aws") == []

    @pytest.mark.asyncio
    async def test_order_kept_when_writes_share_a_timestamp(self, fake_redis, clock, monkeypatch):
        # Deterministic ids that sort in REVERSE write order expose the tie-break.
        ids = iter(f"{n:032x}" for n in range(100, 0, -1))
        monkeypatch.setattr(hs.uuid, "uuid4", lambda: SimpleNamespace(hex=next(ids)))
        store = hs.RedisHistoryStore(client=fake_redis)
        for i in range(5):
            await store.save_chat_message("u", "s", "aws", _msg(f"m{i}"))
        got = await store.fetch_chat("u", "s", "aws")
        assert [m.content for m in got] == [f"m{i}" for i in range(5)]


class _BrokenRedis:
    def pipeline(self, *a, **kw):
        raise ConnectionError("redis down")

    async def zrevrangebyscore(self, *a, **kw):
        raise ConnectionError("redis down")

    async def ping(self):
        raise ConnectionError("redis down")


class TestRedisFailOpen:
    @pytest.mark.asyncio
    async def test_errors_are_swallowed_and_counted(self, errors):
        store = hs.RedisHistoryStore(client=_BrokenRedis())
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        assert await store.fetch_chat("u", "s", "aws") == []
        assert await store.fetch_all_chats("u", "s") == []
        assert errors == [("redis", "save"), ("redis", "fetch"), ("redis", "fetch")]

    @pytest.mark.asyncio
    async def test_ping_failure_propagates_for_readiness(self):
        # ping is a readiness probe: it must NOT be fail-open (check_history catches it).
        with pytest.raises(ConnectionError):
            await hs.RedisHistoryStore(client=_BrokenRedis()).ping()

    @pytest.mark.asyncio
    async def test_corrupt_member_yields_empty_history(self, fake_redis, errors):
        store = hs.RedisHistoryStore(client=fake_redis)
        key = store._agent_key(hs._pk("u", "s"), "aws")
        import time as real_time
        await fake_redis.zadd(key, {"not-json": real_time.time()})
        assert await store.fetch_chat("u", "s", "aws") == []
        assert errors == [("redis", "fetch")]

    def test_default_client_is_lazy_and_needs_no_server(self, monkeypatch):
        monkeypatch.setattr(settings, "redis_ssl", False)
        store = hs.RedisHistoryStore()
        assert store.backend == "redis"
        assert store.redis is not None


# ---------------------------------------------------------------------------
# Postgres backend via an in-memory asyncpg shim
# ---------------------------------------------------------------------------

class _ShimConn:
    def __init__(self, db: "_ShimPool") -> None:
        self.db = db

    async def execute(self, sql: str, *args: Any) -> str:
        self.db.executed.append(sql)
        if self.db.fail:
            raise OSError("postgres down")
        if sql.lstrip().upper().startswith("INSERT"):
            pk, sk, agent_id, role, content, ts, created_at, expires_at = args
            assert (pk, sk) not in {(r["pk"], r["sk"]) for r in self.db.rows}, "PK collision"
            self.db.rows.append(dict(
                pk=pk, sk=sk, agent_id=agent_id, role=role, content=content, ts=ts,
                created_at=created_at, expires_at=expires_at,
            ))
        elif sql.lstrip().upper().startswith("DELETE"):
            now = datetime.now(timezone.utc)
            self.db.rows = [r for r in self.db.rows if r["expires_at"] >= now]
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if self.db.fail:
            raise OSError("postgres down")
        now = datetime.now(timezone.utc)
        if "agent_id = $2" in sql:
            pk, agent_id, limit = args
            rows = [r for r in self.db.rows if r["pk"] == pk and r["agent_id"] == agent_id]
        else:
            pk, limit = args
            rows = [r for r in self.db.rows if r["pk"] == pk]
        rows = [r for r in rows if r["expires_at"] > now]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    async def fetchval(self, sql: str) -> Any:
        if self.db.fail:
            raise OSError("postgres down")
        return 1


class _Acquire:
    def __init__(self, conn: _ShimConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _ShimConn:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _ShimPool:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.executed: list[str] = []
        self.fail = False

    def acquire(self) -> _Acquire:
        return _Acquire(_ShimConn(self))


class TestPostgresContract:
    @pytest.mark.asyncio
    async def test_isolation_order_and_last_n(self):
        pool = _ShimPool()
        store = hs.PostgresHistoryStore(pool=pool)
        for agent, text in [("aws", "a1"), ("kubernetes", "k1"), ("aws", "a2"), ("aws", "a3")]:
            await store.save_chat_message("u", "s", agent, _msg(text))
            await asyncio.sleep(0.001)  # distinct created_at
        await store.save_chat_message("u", "other", "aws", _msg("elsewhere"))

        assert [m.content for m in await store.fetch_chat("u", "s", "aws")] == ["a1", "a2", "a3"]
        assert [m.content for m in await store.fetch_chat("u", "s", "aws", max_messages=1)] == ["a3"]
        assert [m.content for m in await store.fetch_all_chats("u", "s")] == ["a1", "k1", "a2", "a3"]
        assert [m.agent_id for m in await store.fetch_all_chats("u", "s")] == [
            "aws", "kubernetes", "aws", "aws"]

    @pytest.mark.asyncio
    async def test_rows_expire_after_retention_and_are_purged_on_write(self):
        pool = _ShimPool()
        store = hs.PostgresHistoryStore(pool=pool)
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        [row] = pool.rows
        assert (row["expires_at"] - row["created_at"]).total_seconds() == HISTORY_TTL_SECONDS
        assert row["created_at"].tzinfo is not None
        assert row["sk"].startswith("aws#")
        assert any(s.lstrip().upper().startswith("DELETE") for s in pool.executed), \
            "every write purges expired rows opportunistically"

        # Age the row past its expiry: reads exclude it, the next write purges it.
        row["expires_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert await store.fetch_chat("u", "s", "aws") == []
        await store.save_chat_message("u", "s", "aws", _msg("y"))
        assert [r["content"] for r in pool.rows] == ["y"]

    @pytest.mark.asyncio
    async def test_concurrent_saves_lose_nothing(self):
        pool = _ShimPool()
        store = hs.PostgresHistoryStore(pool=pool)
        await asyncio.gather(*[store.save_chat_message("u", "s", "aws", _msg(f"c{i}")) for i in range(40)])
        assert len(pool.rows) == 40
        assert len({r["sk"] for r in pool.rows}) == 40

    @pytest.mark.asyncio
    async def test_unicode_round_trip_and_ping(self):
        store = hs.PostgresHistoryStore(pool=_ShimPool())
        await store.save_chat_message("u", "s", "aws", _msg("ação 🚀", role="assistant"))
        [got] = await store.fetch_chat("u", "s", "aws")
        assert (got.content, got.role, got.agent_id) == ("ação 🚀", "assistant", "aws")
        assert await store.ping() is True

    @pytest.mark.asyncio
    async def test_db_errors_are_fail_open_and_counted(self, errors):
        pool = _ShimPool()
        pool.fail = True
        store = hs.PostgresHistoryStore(pool=pool)
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        assert await store.fetch_chat("u", "s", "aws") == []
        assert await store.fetch_all_chats("u", "s") == []
        assert errors == [("postgres", "save"), ("postgres", "fetch"), ("postgres", "fetch")]


class _FakeAsyncpg:
    """Stand-in for the asyncpg module: counts create_pool calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.pool = _ShimPool()

    async def create_pool(self, **kwargs: Any) -> _ShimPool:
        self.calls += 1
        await asyncio.sleep(0)
        if self.fail:
            raise OSError("connection refused")
        return self.pool


class TestPostgresConnection:
    @pytest.mark.asyncio
    async def test_schema_created_once_and_has_no_vector_dependency(self, monkeypatch):
        fake = _FakeAsyncpg()
        monkeypatch.setitem(__import__("sys").modules, "asyncpg", fake)
        store = hs.PostgresHistoryStore()
        await asyncio.gather(*[store.fetch_all_chats("u", "s") for _ in range(5)])
        await store.save_chat_message("u", "s", "aws", _msg("x"))
        assert fake.calls == 1, "concurrent first use must open a single pool"
        schema = [s for s in fake.pool.executed if "CREATE TABLE" in s]
        assert len(schema) == 1
        assert "IF NOT EXISTS" in schema[0]
        assert "vector" not in schema[0].lower()
        assert "expires_at" in schema[0]

    @pytest.mark.asyncio
    async def test_connect_failure_backs_off_then_retries(self, monkeypatch, clock, errors):
        fake = _FakeAsyncpg(fail=True)
        monkeypatch.setitem(__import__("sys").modules, "asyncpg", fake)
        store = hs.PostgresHistoryStore()

        assert await store.fetch_chat("u", "s", "aws") == []
        assert await store.ping() is False
        await store.save_chat_message("u", "s", "aws", _msg("x"))  # must not raise
        assert fake.calls == 1, "no reconnect storm inside the back-off window"
        assert errors == [("postgres", "connect")]

        clock.now += hs.PG_RECONNECT_SECONDS + 1
        fake.fail = False
        await store.save_chat_message("u", "s", "aws", _msg("after"))
        assert fake.calls == 2
        assert [m.content for m in await store.fetch_chat("u", "s", "aws")] == ["after"]
        assert await store.ping() is True


# ---------------------------------------------------------------------------
# Backend selection (AC 1)
# ---------------------------------------------------------------------------

class TestBackendSelection:
    def test_default_backend_is_redis(self, monkeypatch):
        monkeypatch.delenv("HISTORY_BACKEND", raising=False)
        assert Settings(_env_file=None).history_backend == "redis"

    @pytest.mark.parametrize("value", ["redis", "dynamodb", "postgres", "DYNAMODB"])
    def test_env_selects_backend(self, monkeypatch, value):
        monkeypatch.setenv("HISTORY_BACKEND", value)
        try:
            got = Settings(_env_file=None).history_backend
        except ValidationError:
            # Literal values are case-sensitive; an upper-cased value must fail loudly, not
            # silently fall back to the default.
            assert value != value.lower()
            return
        assert got == value

    @pytest.mark.parametrize("value", ["mysql", "", "dynamo", "redis "])
    def test_invalid_env_fails_loudly(self, monkeypatch, value):
        monkeypatch.setenv("HISTORY_BACKEND", value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_factory_builds_each_backend(self, monkeypatch):
        assert isinstance(build_history_store("redis"), hs.RedisHistoryStore)
        assert isinstance(build_history_store("postgres"), hs.PostgresHistoryStore)
        assert isinstance(build_history_store("dynamodb"), DynamoDBHistoryStore)
        for name, cls in (("redis", hs.RedisHistoryStore), ("postgres", hs.PostgresHistoryStore)):
            monkeypatch.setattr(settings, "history_backend", name)
            assert isinstance(build_history_store(), cls)

    @pytest.mark.parametrize("value", ["sqlite", "Redis", "memory"])
    def test_factory_rejects_unknown(self, value):
        with pytest.raises(ValueError, match="HISTORY_BACKEND"):
            build_history_store(value)

    def test_every_store_names_its_backend(self):
        names = {build_history_store(b).backend for b in ("redis", "dynamodb", "postgres")}
        assert names == {"redis", "dynamodb", "postgres"}


# ---------------------------------------------------------------------------
# Readiness (AC 4)
# ---------------------------------------------------------------------------

class TestCheckHistory:
    @pytest.mark.asyncio
    async def test_ping_results(self):
        class _Store:
            backend = "postgres"

            def __init__(self, result: Any) -> None:
                self.result = result
                self.calls = 0

            async def ping(self) -> bool:
                self.calls += 1
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

        ok = await DependencyChecker(cache_ttl=0).check_history(_Store(True))
        assert ok.ok and "postgres" in ok.detail
        down = await DependencyChecker(cache_ttl=0).check_history(_Store(False))
        assert not down.ok and "postgres" in down.detail
        boom = await DependencyChecker(cache_ttl=0).check_history(_Store(OSError("refused")))
        assert not boom.ok and "refused" in boom.detail

        cached_store = _Store(True)
        checker = DependencyChecker(cache_ttl=60)
        await checker.check_history(cached_store)
        await checker.check_history(cached_store)
        assert cached_store.calls == 1

    @pytest.mark.asyncio
    async def test_slow_ping_times_out_and_unknown_backend(self):
        class _Slow:
            async def ping(self) -> bool:
                await asyncio.sleep(1)
                return True

        result = await DependencyChecker(cache_ttl=0, timeout=0.05).check_history(_Slow())
        assert not result.ok and "timeout" in result.detail

        class _NoName:
            async def ping(self) -> bool:
                return False

        result = await DependencyChecker(cache_ttl=0).check_history(_NoName())
        assert not result.ok and "unknown" in result.detail


class _SyncRedis:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    def ping(self) -> bool:
        if not self.ok:
            raise ConnectionError("redis down")
        return True


class _Table:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.loads = 0

    def load(self) -> None:
        self.loads += 1
        if not self.ok:
            raise RuntimeError("ResourceNotFoundException")


class _PingStore:
    backend = "postgres"

    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def ping(self) -> bool:
        return self.ok


@pytest.fixture
def ready(monkeypatch):
    import src.supervisor.server as srv

    def _call(backend: str, *, redis_ok=True, table_ok=True, store_ok=True, agents=1, degraded=None):
        table = _Table(table_ok)
        monkeypatch.setattr(settings, "history_backend", backend)
        monkeypatch.setattr(srv, "_checker", DependencyChecker(cache_ttl=0))
        monkeypatch.setattr(srv, "_health_redis", _SyncRedis(redis_ok))
        monkeypatch.setattr(srv, "_health_dynamodb_table", table)
        monkeypatch.setattr(srv, "_history_store", _PingStore(store_ok))
        monkeypatch.setattr(srv, "supervisor", SimpleNamespace(
            agents={f"a{i}": object() for i in range(agents)},
            registry=SimpleNamespace(degraded_datasources=degraded or []),
        ))
        resp = TestClient(srv.app).get("/ready")
        return resp, table

    return _call


class TestReadyEndpoint:
    def test_redis_backend_needs_no_dynamodb(self, ready):
        resp, table = ready("redis", table_ok=False)
        assert resp.status_code == 200
        body = resp.json()
        assert body["checks"]["history"]["backend"] == "redis"
        assert body["checks"]["history"]["ok"] is True
        assert "dynamodb" not in body["checks"]
        assert table.loads == 0

    def test_redis_backend_down_is_not_ready(self, ready):
        resp, _ = ready("redis", redis_ok=False)
        assert resp.status_code == 503
        assert resp.json()["checks"]["history"]["ok"] is False

    def test_dynamodb_backend_keeps_its_check(self, ready):
        resp, table = ready("dynamodb")
        assert resp.status_code == 200
        checks = resp.json()["checks"]
        assert checks["dynamodb"]["ok"] is True
        assert checks["history"]["backend"] == "dynamodb"
        assert table.loads == 1

        # History is informational (spec 46 review F1): a history outage is reported,
        # never a 503 — queries still answer with empty history (invariant 6).
        resp, _ = ready("dynamodb", table_ok=False)
        assert resp.status_code == 200
        assert resp.json()["checks"]["history"]["ok"] is False

    @pytest.mark.parametrize("store_ok", [True, False])
    def test_postgres_backend_follows_ping(self, ready, store_ok):
        resp, table = ready("postgres", store_ok=store_ok, table_ok=False)
        assert resp.status_code == 200  # informational (spec 46 review F1)
        checks = resp.json()["checks"]
        assert checks["history"]["ok"] is store_ok
        assert checks["history"]["backend"] == "postgres"
        assert "dynamodb" not in checks
        assert table.loads == 0

    def test_no_agents_is_not_ready(self, ready):
        resp, _ = ready("redis", agents=0)
        assert resp.status_code == 503
