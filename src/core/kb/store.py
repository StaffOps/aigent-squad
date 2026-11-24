"""KbStore: async PostgreSQL client for kb_items (spec 21).

Fail-open throughout: a Postgres outage disables the KB instead of failing a
request. ``KB_ENABLED=false`` turns the store off entirely — it never connects
and every method is a no-op (F-029).

Retention (F-029) is enforced here, without a cron: a bounded batch-delete sweep
runs once at connect and then at most once per ``KB_RETENTION_INTERVAL_SECONDS``,
started in the background by KB reads and writes.
"""
import asyncio
import contextlib
import json
import time
from typing import Any, Optional

import asyncpg

from src.core.config import reveal, settings
from src.core.kb.models import KbItem, KbProvenance
from src.core.logger import logger
from src.core.metrics import kb_provenance_written, kb_retention_deleted, kb_retention_errors

# Rejected / superseded items: age is measured from the last status change.
_SELECT_EXPIRED_REJECTED = """
    SELECT id FROM kb_items
    WHERE status IN ('rejected', 'superseded')
      AND updated_at < now() - make_interval(days => $1)
    ORDER BY updated_at
    LIMIT $2
    FOR UPDATE SKIP LOCKED"""

# Every other item (active, pending review): age is measured from creation.
_SELECT_EXPIRED_MAX_AGE = """
    SELECT id FROM kb_items
    WHERE status IN ('active', 'pending_review')
      AND created_at < now() - make_interval(days => $1)
    ORDER BY created_at
    LIMIT $2
    FOR UPDATE SKIP LOCKED"""

# kb_items.superseded_by has no ON DELETE action, so a row that still points at
# a doomed row would make the DELETE fail. Clear those pointers first (rows in
# the same batch are deleted together and need no update).
_UNLINK_SUPERSEDED_BY = """
    UPDATE kb_items SET superseded_by = NULL
    WHERE superseded_by = ANY($1::uuid[]) AND NOT (id = ANY($1::uuid[]))"""

# kb_provenance rows go with their item (ON DELETE CASCADE).
_DELETE_BY_IDS = "DELETE FROM kb_items WHERE id = ANY($1::uuid[])"


class KbStore:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        # The first sweep runs at connect(); later ones once the interval passed.
        self._last_retention_run: float = time.monotonic()
        self._retention_task: Optional["asyncio.Task[int]"] = None

    def _active_pool(self) -> Optional[asyncpg.Pool]:
        """The pool, or None when the KB is disabled or not connected."""
        if not settings.kb_enabled:
            return None
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return
        if not settings.kb_enabled:
            logger.info("KB disabled (KB_ENABLED=false); not connecting to Postgres")
            return
        try:
            self._pool = await asyncpg.create_pool(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=reveal(settings.postgres_password),
                database=settings.postgres_db,
                min_size=1,
                max_size=5,
            )
        except Exception as e:
            logger.warning("Postgres unavailable (KB disabled)", extra={"error": str(e)})
            self._pool = None
            return
        self._last_retention_run = time.monotonic()
        await self.enforce_retention()

    async def close(self) -> None:
        task = self._retention_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._retention_task = None
        if self._pool:
            await self._pool.close()
            self._pool = None

    def _embedding_to_pgvector(self, emb: Optional[list[float]]) -> Optional[str]:
        if emb is None:
            return None
        return "[" + ",".join(str(x) for x in emb) + "]"

    async def insert(self, item: KbItem, provenance: Optional[KbProvenance] = None) -> Optional[str]:
        """Insert an item, plus its ``kb_provenance`` row when given (one transaction)."""
        pool = self._active_pool()
        if pool is None:
            return None
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """INSERT INTO kb_items (id, type, title, content, tags, service_name, embedding, metadata, confidence_score, status)
                           VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8, $9, $10) RETURNING id""",
                        item.id, item.type, item.title, item.content, item.tags, item.service_name,
                        self._embedding_to_pgvector(item.embedding),
                        json.dumps(item.metadata), item.confidence_score, item.status,
                    )
                    if provenance is not None:
                        await conn.execute(
                            """INSERT INTO kb_provenance
                                   (kb_item_id, source_investigation_id, confidence, extractor_model, enricher_model)
                               VALUES ($1, $2, $3, $4, $5)""",
                            row["id"], provenance.source_investigation_id, provenance.confidence,
                            provenance.extractor_model, provenance.enricher_model,
                        )
            if provenance is not None:
                kb_provenance_written.add(1)
            self._schedule_retention()
            return str(row["id"])
        except Exception as e:
            logger.warning("KB insert failed (fail-open)", extra={"error": str(e)})
            return None

    async def search_similar(
        self, embedding: list[float], top_k: int = 3,
        threshold: float = 0.75, service_name: Optional[str] = None,
    ) -> list[KbItem]:
        """Search by cosine similarity. Returns items with similarity >= threshold."""
        pool = self._active_pool()
        if pool is None:
            return []
        self._schedule_retention()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, type, title, content, tags, service_name, metadata, confidence_score, status,
                              1 - (embedding <=> $1::vector) AS similarity
                       FROM kb_items
                       WHERE status = 'active' AND embedding IS NOT NULL
                         AND ($3::text IS NULL OR service_name = $3 OR service_name IS NULL)
                       ORDER BY embedding <=> $1::vector
                       LIMIT $2""",
                    self._embedding_to_pgvector(embedding), top_k, service_name,
                )
                return [
                    self._row_to_item(r)
                    for r in rows if r["similarity"] >= threshold
                ]
        except Exception as e:
            logger.warning("KB search failed (fail-open)", extra={"error": str(e)})
            return []

    async def get(self, item_id: str) -> Optional[KbItem]:
        pool = self._active_pool()
        if pool is None:
            return None
        try:
            async with pool.acquire() as conn:
                r = await conn.fetchrow(
                    "SELECT id, type, title, content, tags, service_name, metadata, confidence_score, status FROM kb_items WHERE id = $1",
                    item_id,
                )
                return self._row_to_item(r) if r else None
        except Exception as e:
            logger.warning("KB get failed", extra={"error": str(e)})
            return None

    async def update_status(self, item_id: str, status: str) -> bool:
        pool = self._active_pool()
        if pool is None:
            return False
        try:
            async with pool.acquire() as conn:
                r = await conn.execute(
                    "UPDATE kb_items SET status = $1 WHERE id = $2", status, item_id
                )
                return "UPDATE 1" in r
        except Exception as e:
            logger.warning("KB update_status failed", extra={"error": str(e)})
            return False

    async def list_pending_review(self, limit: int = 50) -> list[KbItem]:
        pool = self._active_pool()
        if pool is None:
            return []
        self._schedule_retention()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, type, title, content, tags, service_name, metadata, confidence_score, status FROM kb_items WHERE status = 'pending_review' ORDER BY created_at DESC LIMIT $1",
                    limit,
                )
                return [self._row_to_item(r) for r in rows]
        except Exception:
            return []

    # ── Retention (F-029) ────────────────────────────────────────────────

    async def enforce_retention(self) -> int:
        """Delete expired items in bounded batches. Returns how many were deleted.

        Fail-open: any error is logged, counted in ``aigent.kb.retention.errors``
        and swallowed; what was deleted before the error stays deleted.
        """
        pool = self._active_pool()
        if pool is None:
            return 0
        deleted = 0
        try:
            deleted += await self._delete_expired(
                pool, "rejected_superseded", _SELECT_EXPIRED_REJECTED, settings.kb_rejected_retention_days,
            )
            max_age = settings.kb_item_max_age_days
            if max_age is not None:
                deleted += await self._delete_expired(pool, "max_age", _SELECT_EXPIRED_MAX_AGE, max_age)
        except Exception as e:
            kb_retention_errors.add(1)
            logger.warning("KB retention sweep failed (fail-open)", extra={"error": str(e)})
        if deleted:
            logger.info("KB retention sweep deleted items", extra={"deleted": deleted})
        return deleted

    async def _delete_expired(self, pool: asyncpg.Pool, rule: str, select_sql: str, days: int) -> int:
        """Run one rule: at most ``max_batches`` transactions of ``batch_size`` rows."""
        batch_size = settings.kb_retention_batch_size
        deleted = 0
        for _ in range(settings.kb_retention_max_batches):
            async with pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch(select_sql, days, batch_size)
                    ids = [r["id"] for r in rows]
                    if not ids:
                        return deleted
                    await conn.execute(_UNLINK_SUPERSEDED_BY, ids)
                    await conn.execute(_DELETE_BY_IDS, ids)
            deleted += len(ids)
            kb_retention_deleted.add(len(ids), {"rule": rule})
            if len(ids) < batch_size:
                break
        return deleted

    def _schedule_retention(self) -> None:
        """Start a background sweep if the interval passed and none is running."""
        task = self._retention_task
        if task is not None and not task.done():
            return
        now = time.monotonic()
        if now - self._last_retention_run < settings.kb_retention_interval_seconds:
            return
        self._last_retention_run = now
        try:
            self._retention_task = asyncio.get_running_loop().create_task(self.enforce_retention())
        except RuntimeError:  # no running loop — nothing to schedule on
            self._retention_task = None

    def _row_to_item(self, r: dict[str, Any]) -> KbItem:
        meta = r["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return KbItem(
            id=str(r["id"]),
            type=r["type"],
            title=r["title"],
            content=r["content"],
            tags=list(r["tags"] or []),
            service_name=r["service_name"],
            metadata=meta or {},
            confidence_score=float(r["confidence_score"]),
            status=r["status"],
        )


# Singleton
kb_store = KbStore()
