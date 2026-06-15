"""Tests for KbStore (asyncpg client)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.kb.store import KbStore
from src.core.kb.models import KbItem


@pytest.mark.asyncio
async def test_connect_handles_failure():
    """If postgres is unavailable, KbStore degrades to disabled (pool=None)."""
    store = KbStore()
    with patch("src.core.kb.store.asyncpg.create_pool", new=AsyncMock(side_effect=Exception("connection refused"))):
        await store.connect()
    assert store._pool is None  # fail-open


@pytest.mark.asyncio
async def test_insert_returns_none_when_pool_none():
    store = KbStore()
    store._pool = None
    item = KbItem(title="x", content="y")
    result = await store.insert(item)
    assert result is None


@pytest.mark.asyncio
async def test_search_similar_returns_empty_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.search_similar([0.1] * 1024)
    assert result == []


@pytest.mark.asyncio
async def test_get_returns_none_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.get("any-id")
    assert result is None


@pytest.mark.asyncio
async def test_update_status_returns_false_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.update_status("any-id", "active")
    assert result is False


@pytest.mark.asyncio
async def test_list_pending_returns_empty_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.list_pending_review()
    assert result == []


def test_embedding_to_pgvector_format():
    store = KbStore()
    result = store._embedding_to_pgvector([0.1, 0.2, 0.3])
    assert result == "[0.1,0.2,0.3]"


def test_embedding_to_pgvector_none():
    store = KbStore()
    assert store._embedding_to_pgvector(None) is None


@pytest.mark.asyncio
async def test_insert_with_pool_calls_fetchrow():
    store = KbStore()
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"id": "abc-123"})
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
    store._pool = mock_pool

    item = KbItem(title="test", content="content", embedding=[0.1] * 1024)
    result = await store.insert(item)
    assert result == "abc-123"
    mock_conn.fetchrow.assert_awaited()


class AsyncContextManagerMock:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return None
