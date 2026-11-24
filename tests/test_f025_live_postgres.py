"""Opt-in live Postgres check for the history store (BACKLOG F-025 review).

Skipped unless ``AIGENT_LIVE_POSTGRES=1``. Uses the ``POSTGRES_*`` settings as a
role that may CREATE ROLE / CREATE TABLE (e.g. a throwaway ``postgres:16``
container). It proves, against a real server, what the unit tests only mock:
the purge statement deletes expired rows in a bounded batch, ``to_regclass``
detects a pre-created table, and a DML-only role connects and works with no DDL.
No CI job runs it; it is a manual verification aid.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from src.core import history_stores
from src.core.config import reveal, settings
from src.core.conversation import ConversationMessage

pytestmark = pytest.mark.skipif(
    os.environ.get("AIGENT_LIVE_POSTGRES") != "1", reason="set AIGENT_LIVE_POSTGRES=1 to run"
)


async def _admin():  # type: ignore[no-untyped-def]
    import asyncpg

    return await asyncpg.connect(
        host=settings.postgres_host, port=settings.postgres_port, user=settings.postgres_user,
        password=reveal(settings.postgres_password), database=settings.postgres_db,
    )


@pytest.mark.asyncio
async def test_live_purge_regclass_and_dml_only_role():
    role = f"aigent_dml_{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    admin = await _admin()
    try:
        await admin.execute("DROP TABLE IF EXISTS aigent_history")
        assert await admin.fetchval(history_stores._PG_TABLE_EXISTS) is None
        await admin.execute(history_stores._PG_SCHEMA)
        assert await admin.fetchval(history_stores._PG_TABLE_EXISTS) is not None

        # Bounded purge: only expired rows, at most PG_PURGE_BATCH per statement.
        batch = history_stores.PG_PURGE_BATCH
        await admin.execute(
            """INSERT INTO aigent_history (pk, sk, agent_id, role, content, ts, created_at, expires_at)
               SELECT 'p', 's' || g, 'aws', 'user', 'c', 't', now(), now() - interval '1 hour'
               FROM generate_series(1, $1) g""",
            batch + 5,
        )
        await admin.execute(
            """INSERT INTO aigent_history (pk, sk, agent_id, role, content, ts, created_at, expires_at)
               VALUES ('p', 'live', 'aws', 'user', 'c', 't', now(), now() + interval '1 hour')"""
        )
        assert history_stores._deleted_rows(await admin.execute(history_stores._PG_PURGE)) == batch
        assert history_stores._deleted_rows(await admin.execute(history_stores._PG_PURGE)) == 5
        assert await admin.fetchval("SELECT count(*) FROM aigent_history") == 1

        # DML-only runtime role against the pre-created table.
        await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        await admin.execute(f"GRANT SELECT, INSERT, DELETE ON aigent_history TO {role}")
        await admin.execute(f"REVOKE CREATE ON SCHEMA public FROM {role}")

        store = history_stores.PostgresHistoryStore()
        with patch.multiple(settings, postgres_user=role, postgres_password=SecretStr(password)):
            assert await store.ping() is True
            if store._purge_task is not None:
                await store._purge_task
            msg = ConversationMessage(role="user", content="hello", timestamp="t", agent_id="aws")
            await store.save_chat_message("u-live", "s-live", "aws", msg)
            got = await store.fetch_chat("u-live", "s-live", "aws")
        assert [m.content for m in got] == ["hello"]
        pool = store._pool
        assert pool is not None
        await pool.close()
    finally:
        await admin.execute("DROP TABLE IF EXISTS aigent_history")
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()
