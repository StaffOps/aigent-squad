"""BACKLOG F-025 (a)+(c): Postgres history purge/TLS/privileges, per-message content cap.

Written against the contract in the F-025 row: expired rows are purged without
depending on writes, in bounded batches; POSTGRES_SSLMODE reaches asyncpg only
when set; stored history content never exceeds HISTORY_MAX_MESSAGE_CHARS.
"""
from contextlib import nullcontext
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from src.core import history_stores
from src.core.config import settings
from src.core.conversation import (
    HISTORY_TRUNCATION_MARKER,
    ConversationMessage,
    cap_history_content,
)
from src.core.history_stores import PostgresHistoryStore, RedisHistoryStore


def _msg(content: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=content, timestamp="t", agent_id="aws")


class _Conn:
    """Fake asyncpg connection: DELETEs report the scripted row counts in order."""

    def __init__(
        self,
        deleted: list[int] | None = None,
        table_exists: Any = None,
        purge_error: Exception | None = None,
    ) -> None:
        self.deleted = list(deleted or [])
        self.table_exists = table_exists
        self.purge_error = purge_error
        self.executed: list[str] = []
        self.rows: list[dict[str, Any]] = [{"agent_id": "aws", "role": "user", "content": "q", "ts": "t"}]

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(sql)
        if "DELETE FROM aigent_history" in sql:
            if self.purge_error is not None:
                raise self.purge_error
            return f"DELETE {self.deleted.pop(0) if self.deleted else 0}"
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append(sql)
        return self.rows

    async def fetchval(self, sql: str) -> Any:
        self.executed.append(sql)
        return self.table_exists if "to_regclass" in sql else 1

    def purges(self) -> int:
        return sum("DELETE FROM aigent_history" in s for s in self.executed)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> "_Pool":
        return self

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def close(self) -> None:
        return None


async def _connect(conn: _Conn, **settings_overrides: Any) -> tuple[PostgresHistoryStore, AsyncMock]:
    create_pool = AsyncMock(return_value=_Pool(conn))
    store = PostgresHistoryStore()
    with patch("asyncpg.create_pool", create_pool), \
         patch.multiple(settings, **settings_overrides) if settings_overrides else nullcontext():
        assert await store.ping() is True
        # The startup purge runs in the background after the connect (finding 5).
        if store._purge_task is not None:
            await store._purge_task
    return store, create_pool


# ---------------------------------------------------------------------------
# (a) purge without writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_purge_runs_once_the_schema_is_ready_until_backlog_drains():
    full = history_stores.PG_PURGE_BATCH
    conn = _Conn(deleted=[full, full, 3])
    await _connect(conn)
    assert conn.purges() == 3  # stops at the first partial batch


@pytest.mark.asyncio
async def test_startup_purge_is_bounded():
    full = history_stores.PG_PURGE_BATCH
    conn = _Conn(deleted=[full] * 50)
    await _connect(conn)
    assert conn.purges() == history_stores.PG_STARTUP_PURGE_BATCHES


@pytest.mark.asyncio
async def test_purge_statement_is_bounded():
    assert f"LIMIT {history_stores.PG_PURGE_BATCH}" in history_stores._PG_PURGE


@pytest.mark.asyncio
async def test_startup_purge_failure_does_not_fail_the_connect():
    conn = _Conn(purge_error=OSError("lock timeout"))
    with patch("src.core.history_stores._record_error") as rec:
        store, _ = await _connect(conn)
    assert store._pool is not None
    assert ("postgres", "purge") in [c.args for c in rec.call_args_list]


@pytest.mark.asyncio
async def test_read_purges_at_most_once_per_interval(monkeypatch):
    conn = _Conn()
    store = PostgresHistoryStore(pool=_Pool(conn))
    clock = [1000.0]
    monkeypatch.setattr(history_stores.time, "monotonic", lambda: clock[0])

    await store.fetch_chat("u", "s", "aws")
    await store.fetch_all_chats("u", "s")
    assert conn.purges() == 1  # second read inside the interval: no purge

    clock[0] += history_stores.PG_READ_PURGE_INTERVAL_SECONDS + 1
    got = await store.fetch_chat("u", "s", "aws")
    assert conn.purges() == 2 and [m.content for m in got] == ["q"]


@pytest.mark.asyncio
async def test_read_purge_failure_keeps_the_read_result():
    conn = _Conn(purge_error=OSError("boom"))
    store = PostgresHistoryStore(pool=_Pool(conn))
    with patch("src.core.history_stores._record_error") as rec:
        got = await store.fetch_all_chats("u", "s")
    assert [m.content for m in got] == ["q"]
    assert [c.args for c in rec.call_args_list] == [("postgres", "purge")]


@pytest.mark.asyncio
async def test_pre_created_table_skips_ddl_and_advisory_lock():
    # A DML-only runtime role (table created by a migration role) must connect.
    conn = _Conn(table_exists="aigent_history")
    await _connect(conn)
    assert not any("CREATE TABLE" in s or "pg_advisory" in s for s in conn.executed)


def test_deleted_rows_parses_command_tags():
    assert history_stores._deleted_rows("DELETE 42") == 42
    assert history_stores._deleted_rows("OK") == 0
    assert history_stores._deleted_rows(None) == 0


# ---------------------------------------------------------------------------
# (a) POSTGRES_SSLMODE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sslmode_is_passed_to_asyncpg_when_set():
    _, create_pool = await _connect(_Conn(), postgres_sslmode="require")
    assert create_pool.call_args.kwargs["ssl"] == "require"


@pytest.mark.asyncio
async def test_sslmode_unset_keeps_asyncpg_default():
    _, create_pool = await _connect(_Conn(), postgres_sslmode=None)
    assert "ssl" not in create_pool.call_args.kwargs


def test_sslmode_setting_parsing(monkeypatch):
    from pydantic import ValidationError
    from src.core.config import Settings

    monkeypatch.delenv("POSTGRES_SSLMODE", raising=False)
    assert Settings().postgres_sslmode is None
    monkeypatch.setenv("POSTGRES_SSLMODE", "")
    assert Settings().postgres_sslmode is None  # chart-rendered empty value
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    assert Settings().postgres_sslmode == "require"
    monkeypatch.setenv("POSTGRES_SSLMODE", "sometimes")
    with pytest.raises(ValidationError):
        Settings()


# ---------------------------------------------------------------------------
# (c) per-message content cap
# ---------------------------------------------------------------------------

def test_cap_truncates_with_marker_within_the_limit(monkeypatch):
    monkeypatch.setattr(settings, "history_max_message_chars", 100)
    out = cap_history_content("x" * 500)
    assert len(out) == 100 and out.endswith(HISTORY_TRUNCATION_MARKER)
    assert cap_history_content("short") == "short"
    assert cap_history_content("y" * 100) == "y" * 100  # exactly at the cap: untouched


def test_cap_zero_disables_and_tiny_cap_never_exceeds(monkeypatch):
    monkeypatch.setattr(settings, "history_max_message_chars", 0)
    assert cap_history_content("z" * 50_000) == "z" * 50_000
    monkeypatch.setattr(settings, "history_max_message_chars", 5)
    assert cap_history_content("abcdefghij") == "abcde"


def test_cap_default_and_validation(monkeypatch):
    from pydantic import ValidationError
    from src.core.config import Settings

    monkeypatch.delenv("HISTORY_MAX_MESSAGE_CHARS", raising=False)
    assert Settings().history_max_message_chars == 16_000
    monkeypatch.setenv("HISTORY_MAX_MESSAGE_CHARS", "-1")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.asyncio
async def test_redis_store_caps_content(monkeypatch):
    monkeypatch.setattr(settings, "history_max_message_chars", 200)
    store = RedisHistoryStore(client=fakeredis.FakeAsyncRedis(decode_responses=True))
    await store.save_chat_message("u", "s", "aws", _msg("r" * 10_000))
    (got,) = await store.fetch_chat("u", "s", "aws")
    assert len(got.content) == 200 and got.content.endswith(HISTORY_TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_postgres_store_caps_content(monkeypatch):
    monkeypatch.setattr(settings, "history_max_message_chars", 200)
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    store = PostgresHistoryStore(pool=_Pool(conn))
    await store.save_chat_message("u", "s", "aws", _msg("p" * 10_000))
    content = conn.execute.await_args_list[0].args[5]
    assert len(content) == 200 and content.endswith(HISTORY_TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_dynamodb_store_caps_content(monkeypatch):
    from src.core.state_store import DynamoDBHistoryStore

    monkeypatch.setattr(settings, "history_max_message_chars", 200)
    with patch("src.core.state_store.dynamodb_resource"):
        store = DynamoDBHistoryStore()
    store.table = MagicMock()
    await store.save_chat_message("u", "s", "aws", _msg("d" * 10_000))
    content = store.table.put_item.call_args.kwargs["Item"]["content"]
    assert len(content) == 200 and content.endswith(HISTORY_TRUNCATION_MARKER)
