"""Off-AWS conversation history backends (spec 46): Redis (default) and Postgres.

Both mirror the DynamoDB store's contract (``src.core.state_store.HistoryStore``):
per-agent isolated history for ``user#session``, 24 h retention per message,
oldest-first results, fail-open on every error.

Unlike the DynamoDB store, "all agents" reads are ordered by time across
agents (DynamoDB orders that query by sort key, i.e. agent name first).
"""
import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import redis.asyncio as aioredis

from src.core.config import reveal, settings
from src.core.logger import logger
from src.core.conversation import HISTORY_TTL_SECONDS, ConversationMessage
from src.core.conversation import record_history_error as _record_error

# Upper bound of messages kept per Redis key. Readers ask for at most 50; the
# cap bounds memory for long sessions without changing what readers see.
REDIS_MAX_MESSAGES_PER_KEY = 200


def _pk(user_id: str, session_id: str) -> str:
    return f"{user_id}#{session_id}"


def _digest(value: str) -> str:
    # Deterministic (invariant 7) and keeps raw user/session ids out of key names.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _to_message(raw: dict[str, Any]) -> ConversationMessage:
    return ConversationMessage(
        role=raw["role"],
        content=raw["content"],
        timestamp=raw["timestamp"],
        agent_id=raw.get("agent_id"),
    )


class RedisHistoryStore:
    """Sorted set per session and per session+agent, scored by write time.

    Writes add to both keys, drop members older than the retention window, trim
    to ``REDIS_MAX_MESSAGES_PER_KEY`` and refresh a 24 h key expiry (so an idle
    session disappears on its own). Reads only return members inside the window.
    """

    backend = "redis"

    def __init__(self, client: Optional[Any] = None) -> None:
        if client is not None:
            self.redis = client
            return
        scheme = "rediss" if settings.redis_ssl else "redis"
        # Lazy: from_url does not connect until the first command.
        self.redis = aioredis.from_url(
            f"{scheme}://{settings.redis_host}:{settings.redis_port}",
            password=reveal(settings.redis_password) or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    @staticmethod
    def _session_key(pk: str) -> str:
        return f"aigent:history:{_digest(pk)}"

    @classmethod
    def _agent_key(cls, pk: str, agent_id: str) -> str:
        return f"{cls._session_key(pk)}:{_digest(agent_id)[:16]}"

    async def save_chat_message(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        message: ConversationMessage,
        max_history: int = 20,
    ) -> None:
        try:
            pk = _pk(user_id, session_id)
            now = time.time()
            member = json.dumps({
                "id": uuid.uuid4().hex,  # identical messages stay distinct members
                "agent_id": agent_id,
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp,
            })
            cutoff = now - HISTORY_TTL_SECONDS
            pipe = self.redis.pipeline(transaction=True)
            for key in (self._session_key(pk), self._agent_key(pk, agent_id)):
                pipe.zadd(key, {member: now})
                pipe.zremrangebyscore(key, "-inf", f"({cutoff}")
                pipe.zremrangebyrank(key, 0, -(REDIS_MAX_MESSAGES_PER_KEY + 1))
                pipe.expire(key, HISTORY_TTL_SECONDS)
            await pipe.execute()
        except Exception as e:
            _record_error("redis", "save")
            logger.warning("Redis history save failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })

    async def _latest(self, key: str, max_messages: int) -> List[ConversationMessage]:
        cutoff = time.time() - HISTORY_TTL_SECONDS
        raw = await self.redis.zrevrangebyscore(
            key, "+inf", f"({cutoff}", start=0, num=max_messages
        )
        return [_to_message(json.loads(m)) for m in reversed(raw)]

    async def fetch_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        max_messages: int = 20,
    ) -> List[ConversationMessage]:
        try:
            return await self._latest(self._agent_key(_pk(user_id, session_id), agent_id), max_messages)
        except Exception as e:
            _record_error("redis", "fetch")
            logger.warning("Redis history fetch_chat failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def fetch_all_chats(
        self,
        user_id: str,
        session_id: str,
        max_messages: int = 50,
    ) -> List[ConversationMessage]:
        try:
            return await self._latest(self._session_key(_pk(user_id, session_id)), max_messages)
        except Exception as e:
            _record_error("redis", "fetch")
            logger.warning("Redis history fetch_all_chats failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def ping(self) -> bool:
        return bool(await self.redis.ping())


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS aigent_history (
    pk          TEXT        NOT NULL,
    sk          TEXT        NOT NULL,
    agent_id    TEXT        NOT NULL,
    role        TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    ts          TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pk, sk)
);
CREATE INDEX IF NOT EXISTS aigent_history_pk_created ON aigent_history (pk, created_at DESC);
CREATE INDEX IF NOT EXISTS aigent_history_expires ON aigent_history (expires_at);
"""

# Expired rows are purged opportunistically on write, in bounded batches, so no
# cron is needed and a write never pays for a large backlog (spec 46 Decision 3).
_PG_PURGE = """
DELETE FROM aigent_history WHERE ctid IN (
    SELECT ctid FROM aigent_history WHERE expires_at < now() LIMIT 100
)
"""

# Seconds to wait before retrying a failed pool connect (fail-open meanwhile).
PG_RECONNECT_SECONDS = 30.0


class PostgresHistoryStore:
    """Plain relational history table (no pgvector) in the KB's Postgres."""

    backend = "postgres"

    def __init__(self, pool: Optional[Any] = None) -> None:
        self._pool = pool
        self._lock: Optional[asyncio.Lock] = None
        self._last_failure: float = 0.0

    async def _get_pool(self) -> Optional[Any]:
        if self._pool is not None:
            return self._pool
        if time.monotonic() - self._last_failure < PG_RECONNECT_SECONDS and self._last_failure:
            return None
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._pool is not None:
                return self._pool
            try:
                import asyncpg
                pool = await asyncpg.create_pool(
                    host=settings.postgres_host,
                    port=settings.postgres_port,
                    user=settings.postgres_user,
                    password=reveal(settings.postgres_password),
                    database=settings.postgres_db,
                    min_size=1,
                    max_size=5,
                    timeout=5,
                )
                async with pool.acquire() as conn:
                    await conn.execute(_PG_SCHEMA)
                self._pool = pool
            except Exception as e:
                self._last_failure = time.monotonic()
                _record_error("postgres", "connect")
                logger.warning("Postgres history unavailable (fail-open)", extra={"error": str(e)})
                return None
        return self._pool

    async def save_chat_message(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        message: ConversationMessage,
        max_history: int = 20,
    ) -> None:
        try:
            pool = await self._get_pool()
            if pool is None:
                return
            now = datetime.now(timezone.utc)
            sk = f"{agent_id}#{now.isoformat()}#{uuid.uuid4().hex[:8]}"
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO aigent_history
                       (pk, sk, agent_id, role, content, ts, created_at, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    _pk(user_id, session_id), sk, agent_id, message.role, message.content,
                    message.timestamp, now, now + timedelta(seconds=HISTORY_TTL_SECONDS),
                )
                await conn.execute(_PG_PURGE)
        except Exception as e:
            _record_error("postgres", "save")
            logger.warning("Postgres history save failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })

    async def _latest(self, sql: str, *args: Any) -> List[ConversationMessage]:
        pool = await self._get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [
            ConversationMessage(role=r["role"], content=r["content"], timestamp=r["ts"], agent_id=r["agent_id"])
            for r in reversed(rows)
        ]

    async def fetch_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        max_messages: int = 20,
    ) -> List[ConversationMessage]:
        try:
            return await self._latest(
                """SELECT agent_id, role, content, ts FROM aigent_history
                   WHERE pk = $1 AND agent_id = $2 AND expires_at > now()
                   ORDER BY created_at DESC LIMIT $3""",
                _pk(user_id, session_id), agent_id, max_messages,
            )
        except Exception as e:
            _record_error("postgres", "fetch")
            logger.warning("Postgres history fetch_chat failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def fetch_all_chats(
        self,
        user_id: str,
        session_id: str,
        max_messages: int = 50,
    ) -> List[ConversationMessage]:
        try:
            return await self._latest(
                """SELECT agent_id, role, content, ts FROM aigent_history
                   WHERE pk = $1 AND expires_at > now()
                   ORDER BY created_at DESC LIMIT $2""",
                _pk(user_id, session_id), max_messages,
            )
        except Exception as e:
            _record_error("postgres", "fetch")
            logger.warning("Postgres history fetch_all_chats failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def ping(self) -> bool:
        pool = await self._get_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT 1") == 1)
