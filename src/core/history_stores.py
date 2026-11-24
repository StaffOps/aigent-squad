"""Off-AWS conversation history backends (spec 46): Redis (default) and Postgres.

Both mirror the DynamoDB store's contract (``src.core.state_store.HistoryStore``):
per-agent isolated history for ``user#session``, 24 h retention per message,
oldest-first results, fail-open on every error.

Unlike the DynamoDB store, "all agents" reads are ordered by time across
agents (DynamoDB orders that query by sort key, i.e. agent name first).
"""
import asyncio
import hashlib
import itertools
import json
import os
import ssl
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

import redis.asyncio as aioredis

from src.core.config import reveal, settings
from src.core.logger import logger
from src.core.conversation import HISTORY_TTL_SECONDS, ConversationMessage, cap_history_content
from src.core.conversation import record_history_error as _record_error

# Upper bound of messages kept per Redis key. Readers ask for at most 50; the
# cap bounds memory for long sessions without changing what readers see.
REDIS_MAX_MESSAGES_PER_KEY = 200


def _pk(user_id: str, session_id: str) -> str:
    # JSON-encoded pair, not "user#session": ids containing "#" must not collide
    # (("a#b", "c") vs ("a", "b#c")). The DynamoDB store keeps its legacy key.
    return json.dumps([user_id, session_id], separators=(",", ":"))


# Per-process write sequence: tie-break for writes that share a timestamp (or a
# clock step back) so oldest-first order is preserved within a replica.
_SEQ = itertools.count()


def _order_prefix() -> str:
    return f"{next(_SEQ):016d}"


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
        # {hash tag}: both keys of a session land in the same Redis Cluster slot,
        # so the MULTI/EXEC write below is valid in cluster mode too.
        return f"aigent:history:{{{_digest(pk)}}}"

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
                # First field: on equal scores Redis orders members lexicographically,
                # so this prefix keeps write order; it also keeps identical messages distinct.
                "seq": _order_prefix(),
                "id": uuid.uuid4().hex,
                "agent_id": agent_id,
                "role": message.role,
                "content": cap_history_content(message.content),
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

# Expired rows are purged in bounded batches, so no cron is needed and no request
# ever pays for a large backlog (spec 46 Decision 3): one batch on every write,
# up to PG_STARTUP_PURGE_BATCHES once the schema is ready (a replica that only
# reads, or restarts after an idle period, still cleans up), and one batch on
# read at most every PG_READ_PURGE_INTERVAL_SECONDS (BACKLOG F-025 a).
PG_PURGE_BATCH = 100
_PG_PURGE = f"""
DELETE FROM aigent_history WHERE ctid IN (
    SELECT ctid FROM aigent_history WHERE expires_at < now() LIMIT {PG_PURGE_BATCH}
)
"""
_PG_TABLE_EXISTS = "SELECT to_regclass('aigent_history')"
PG_STARTUP_PURGE_BATCHES = 10
PG_READ_PURGE_INTERVAL_SECONDS = 300.0


def _deleted_rows(status: Any) -> int:
    # asyncpg's execute() returns the command tag, e.g. "DELETE 100".
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0

# Seconds to wait before retrying a failed pool connect (fail-open meanwhile).
PG_RECONNECT_SECONDS = 30.0
# Connect timeout, kept below the readiness check timeout (2 s). The connect
# critical section is only create_pool + the table check (+ DDL on first boot);
# the startup purge runs afterwards in the background, outside the lock, so a
# /ready probe never waits on (or cancels) it.
PG_CONNECT_TIMEOUT_SECONDS = 1.5

_PG_VERIFY_MODES = ("verify-ca", "verify-full")


class PostgresTLSConfigError(RuntimeError):
    """POSTGRES_SSLMODE=verify-* without a CA bundle that resolves to a file."""


def _pg_ca_file() -> Optional[str]:
    """CA bundle for verify-*: POSTGRES_SSLROOTCERT, PGSSLROOTCERT, ~/.postgresql/root.crt.

    An explicitly configured path that is not a file is an error, never a
    silent fall-through to the next candidate.
    """
    explicit = settings.postgres_sslrootcert or os.environ.get("PGSSLROOTCERT") or None
    if explicit:
        if not Path(explicit).is_file():
            raise PostgresTLSConfigError(
                f"POSTGRES_SSLMODE={settings.postgres_sslmode}: CA bundle {explicit!r} is not a readable file"
            )
        return explicit
    try:
        default = Path.home() / ".postgresql" / "root.crt"
    except RuntimeError:  # no resolvable home directory (e.g. a bare UID)
        return None
    return str(default) if default.is_file() else None


def _pg_ssl_kwargs() -> dict[str, Any]:
    """``ssl=`` argument for asyncpg, from POSTGRES_SSLMODE / POSTGRES_SSLROOTCERT.

    ``require`` and below are passed through as asyncpg mode strings (``require``
    encrypts but does not verify the server). ``verify-ca`` / ``verify-full``
    get an explicit SSLContext loaded with the resolved CA bundle, so they work
    with a mounted bundle (e.g. the cloud provider's RDS CA) instead of only
    ``~/.postgresql/root.crt``; no CA -> :class:`PostgresTLSConfigError`.
    """
    mode = settings.postgres_sslmode
    if not mode:
        return {}
    if mode not in _PG_VERIFY_MODES:
        return {"ssl": mode}
    cafile = _pg_ca_file()
    if cafile is None:
        raise PostgresTLSConfigError(
            f"POSTGRES_SSLMODE={mode} needs a CA bundle: set POSTGRES_SSLROOTCERT to a mounted PEM file"
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = mode == "verify-full"
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cafile=cafile)
    return {"ssl": ctx}
# Serializes schema creation across replicas starting together (any constant).
_PG_SCHEMA_LOCK_KEY = 4646046


class PostgresHistoryStore:
    """Plain relational history table (no pgvector) in the KB's Postgres.

    Privileges: when ``aigent_history`` does not exist yet, the first connect
    creates it and its indexes (``_PG_SCHEMA``), which needs CREATE on the target
    schema. To run with a DML-only role (SELECT/INSERT/DELETE), pre-create the
    table and indexes with a migration role: an existing table is used as-is and
    no DDL runs. TLS: ``POSTGRES_SSLMODE`` (+ ``POSTGRES_SSLROOTCERT`` for verify-*).
    """

    backend = "postgres"

    def __init__(self, pool: Optional[Any] = None) -> None:
        self._pool = pool
        self._lock: Optional[asyncio.Lock] = None
        self._last_failure: float = 0.0
        # monotonic time of the last read-path purge (0 = never)
        self._last_read_purge: float = 0.0
        # background startup purge (kept referenced so it is not GC'd mid-run)
        self._purge_task: Optional[asyncio.Task[None]] = None

    async def _purge_on_ready(self, pool: Any) -> None:
        """Bounded backlog purge once the schema is ready; never fails anything.

        Runs as a background task started after the pool is published, outside
        the connect lock, on its own connection: requests never wait for it.
        """
        try:
            async with pool.acquire() as conn:
                for _ in range(PG_STARTUP_PURGE_BATCHES):
                    if _deleted_rows(await conn.execute(_PG_PURGE)) < PG_PURGE_BATCH:
                        break
        except Exception as e:
            _record_error("postgres", "purge")
            logger.warning("Postgres history startup purge failed (fail-open)", extra={"error": str(e)})

    def _start_startup_purge(self, pool: Any) -> None:
        task = asyncio.create_task(self._purge_on_ready(pool), name="aigent-history-startup-purge")
        self._purge_task = task

        def _done(t: "asyncio.Task[None]") -> None:
            if self._purge_task is t:
                self._purge_task = None
            if not t.cancelled() and t.exception() is not None:  # _purge_on_ready catches Exception
                _record_error("postgres", "purge")
                logger.warning("Postgres history startup purge crashed (fail-open)", extra={
                    "error_type": type(t.exception()).__name__,
                })

        task.add_done_callback(_done)

    async def _maybe_purge_on_read(self, conn: Any) -> None:
        """One bounded batch at most every PG_READ_PURGE_INTERVAL_SECONDS."""
        now = time.monotonic()
        if self._last_read_purge and now - self._last_read_purge < PG_READ_PURGE_INTERVAL_SECONDS:
            return
        self._last_read_purge = now
        try:
            await conn.execute(_PG_PURGE)
        except Exception as e:
            # Never cost the read its result: the rows were already fetched.
            _record_error("postgres", "purge")
            logger.warning("Postgres history read purge failed (fail-open)", extra={"error": str(e)})

    def _in_backoff(self) -> bool:
        return bool(self._last_failure) and time.monotonic() - self._last_failure < PG_RECONNECT_SECONDS

    async def _get_pool(self) -> Optional[Any]:
        if self._pool is not None:
            return self._pool
        if self._in_backoff():
            return None
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # Re-check both after waiting: a queued request must not repeat a
            # connect that just failed (or redo one that just succeeded).
            if self._pool is not None:
                return self._pool
            if self._in_backoff():
                return None
            pool = None
            try:
                import asyncpg
                ssl_kwargs = _pg_ssl_kwargs()
                pool = await asyncpg.create_pool(
                    host=settings.postgres_host,
                    port=settings.postgres_port,
                    user=settings.postgres_user,
                    password=reveal(settings.postgres_password),
                    database=settings.postgres_db,
                    min_size=1,
                    max_size=5,
                    timeout=PG_CONNECT_TIMEOUT_SECONDS,
                    **ssl_kwargs,
                )
                async with pool.acquire() as conn:
                    # A table pre-created by a migration role is used as-is: no
                    # DDL runs, so the runtime role needs no CREATE privilege.
                    if await conn.fetchval(_PG_TABLE_EXISTS) is None:
                        await conn.execute("SELECT pg_advisory_lock($1)", _PG_SCHEMA_LOCK_KEY)
                        try:
                            await conn.execute(_PG_SCHEMA)
                        finally:
                            await conn.execute("SELECT pg_advisory_unlock($1)", _PG_SCHEMA_LOCK_KEY)
                self._pool, pool = pool, None
            except BaseException as e:
                # Never leak a half-initialized pool (e.g. no CREATE privilege on
                # the schema): it would hold connections on every retry.
                if pool is not None:
                    try:
                        await pool.close()
                    except Exception:
                        terminate = getattr(pool, "terminate", None)
                        if callable(terminate):
                            terminate()
                if not isinstance(e, Exception):
                    raise  # cancellation: propagate, but the pool is closed
                self._last_failure = time.monotonic()
                _record_error("postgres", "connect")
                if isinstance(e, PostgresTLSConfigError):
                    # Misconfiguration, not an outage: say so loudly (still fail-open).
                    logger.error("Postgres history TLS misconfigured (fail-open)", extra={"error": str(e)})
                else:
                    logger.warning("Postgres history unavailable (fail-open)", extra={"error": str(e)})
                return None
        self._start_startup_purge(self._pool)
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
                    _pk(user_id, session_id), sk, agent_id, message.role, cap_history_content(message.content),
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
            await self._maybe_purge_on_read(conn)
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
