"""KbStore: async PostgreSQL client for kb_items."""
import os
import json
from typing import Optional

import asyncpg

from src.core.kb.models import KbItem, KbStatus
from src.core.logger import logger


class KbStore:
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if self._pool is not None:
            return
        try:
            self._pool = await asyncpg.create_pool(
                host=os.getenv("POSTGRES_HOST", "postgres"),
                port=int(os.getenv("POSTGRES_PORT", "5432")),
                user=os.getenv("POSTGRES_USER", "aigent"),
                password=os.getenv("POSTGRES_PASSWORD", "changeme"),
                database=os.getenv("POSTGRES_DB", "aigent_kb"),
                min_size=1,
                max_size=5,
            )
        except Exception as e:
            logger.warning("Postgres unavailable (KB disabled)", extra={"error": str(e)})
            self._pool = None

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    def _embedding_to_pgvector(self, emb: Optional[list[float]]) -> Optional[str]:
        if emb is None:
            return None
        return "[" + ",".join(str(x) for x in emb) + "]"

    async def insert(self, item: KbItem) -> Optional[str]:
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO kb_items (id, type, title, content, tags, service_name, embedding, metadata, confidence_score, status)
                       VALUES ($1, $2, $3, $4, $5, $6, $7::vector, $8, $9, $10) RETURNING id""",
                    item.id, item.type, item.title, item.content, item.tags, item.service_name,
                    self._embedding_to_pgvector(item.embedding),
                    json.dumps(item.metadata), item.confidence_score, item.status,
                )
                return str(row["id"])
        except Exception as e:
            logger.warning("KB insert failed (fail-open)", extra={"error": str(e)})
            return None

    async def search_similar(
        self, embedding: list[float], top_k: int = 3,
        threshold: float = 0.75, service_name: Optional[str] = None,
    ) -> list[KbItem]:
        """Search by cosine similarity. Returns items with similarity >= threshold."""
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
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
        if not self._pool:
            return None
        try:
            async with self._pool.acquire() as conn:
                r = await conn.fetchrow(
                    "SELECT id, type, title, content, tags, service_name, metadata, confidence_score, status FROM kb_items WHERE id = $1",
                    item_id,
                )
                return self._row_to_item(r) if r else None
        except Exception as e:
            logger.warning("KB get failed", extra={"error": str(e)})
            return None

    async def update_status(self, item_id: str, status: str) -> bool:
        if not self._pool:
            return False
        try:
            async with self._pool.acquire() as conn:
                r = await conn.execute(
                    "UPDATE kb_items SET status = $1 WHERE id = $2", status, item_id
                )
                return "UPDATE 1" in r
        except Exception as e:
            logger.warning("KB update_status failed", extra={"error": str(e)})
            return False

    async def list_pending_review(self, limit: int = 50) -> list[KbItem]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, type, title, content, tags, service_name, metadata, confidence_score, status FROM kb_items WHERE status = 'pending_review' ORDER BY created_at DESC LIMIT $1",
                    limit,
                )
                return [self._row_to_item(r) for r in rows]
        except Exception:
            return []

    def _row_to_item(self, r) -> KbItem:
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
