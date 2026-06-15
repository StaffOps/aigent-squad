"""Tests for KbStore (asyncpg client)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.kb.store import KbStore
from src.core.kb.models import KbItem


class AsyncContextManagerMock:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return None


def _make_store_with_pool(mock_conn):
    store = KbStore()
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncContextManagerMock(mock_conn))
    store._pool = mock_pool
    return store, mock_pool


def _fake_row(**overrides):
    defaults = {
        "id": "row-id-1",
        "type": "troubleshooting",
        "title": "OOM fix",
        "content": "Increase memory",
        "tags": ["k8s"],
        "service_name": "my-svc",
        "metadata": '{"key": "val"}',
        "confidence_score": 0.9,
        "status": "active",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_connect_handles_failure():
    store = KbStore()
    with patch("src.core.kb.store.asyncpg.create_pool", new=AsyncMock(side_effect=Exception("connection refused"))):
        await store.connect()
    assert store._pool is None


@pytest.mark.asyncio
async def test_connect_skips_if_pool_exists():
    store = KbStore()
    store._pool = MagicMock()
    with patch("src.core.kb.store.asyncpg.create_pool", new=AsyncMock()) as mock_create:
        await store.connect()
    mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_close_closes_pool():
    store = KbStore()
    mock_pool = AsyncMock()
    store._pool = mock_pool
    await store.close()
    mock_pool.close.assert_awaited_once()
    assert store._pool is None


@pytest.mark.asyncio
async def test_insert_returns_none_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.insert(KbItem(title="x", content="y"))
    assert result is None


@pytest.mark.asyncio
async def test_insert_with_pool_calls_fetchrow():
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"id": "abc-123"})
    store, _ = _make_store_with_pool(mock_conn)

    item = KbItem(title="test", content="content", embedding=[0.1] * 1024)
    result = await store.insert(item)
    assert result == "abc-123"
    mock_conn.fetchrow.assert_awaited()


@pytest.mark.asyncio
async def test_search_similar_returns_empty_when_pool_none():
    store = KbStore()
    store._pool = None
    result = await store.search_similar([0.1] * 1024)
    assert result == []


@pytest.mark.asyncio
async def test_search_similar_returns_items_above_threshold():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {**_fake_row(id="r1"), "similarity": 0.9},
        {**_fake_row(id="r2"), "similarity": 0.8},
    ])
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.search_similar([0.1] * 1024, threshold=0.75)
    assert len(result) == 2
    assert all(isinstance(r, KbItem) for r in result)
    assert result[0].id == "r1"


@pytest.mark.asyncio
async def test_search_similar_filters_by_threshold():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        {**_fake_row(id="r1"), "similarity": 0.9},
        {**_fake_row(id="r2"), "similarity": 0.6},
    ])
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.search_similar([0.1] * 1024, threshold=0.75)
    assert len(result) == 1
    assert result[0].id == "r1"


@pytest.mark.asyncio
async def test_get_returns_kbitem():
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=_fake_row(id="item-1", title="Found"))
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.get("item-1")
    assert isinstance(result, KbItem)
    assert result.id == "item-1"
    assert result.title == "Found"


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found():
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.get("missing-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_none_when_pool_none():
    store = KbStore()
    store._pool = None
    assert await store.get("any") is None


@pytest.mark.asyncio
async def test_update_status_returns_true_on_update():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.update_status("item-1", "active")
    assert result is True


@pytest.mark.asyncio
async def test_update_status_returns_false_on_no_rows():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 0")
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.update_status("missing-id", "active")
    assert result is False


@pytest.mark.asyncio
async def test_update_status_returns_false_when_pool_none():
    store = KbStore()
    store._pool = None
    assert await store.update_status("x", "active") is False


@pytest.mark.asyncio
async def test_list_pending_review_returns_items():
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[
        _fake_row(id="pending-1", status="pending_review"),
    ])
    store, _ = _make_store_with_pool(mock_conn)

    result = await store.list_pending_review()
    assert len(result) == 1
    assert result[0].status == "pending_review"


@pytest.mark.asyncio
async def test_list_pending_returns_empty_when_pool_none():
    store = KbStore()
    store._pool = None
    assert await store.list_pending_review() == []


def test_embedding_to_pgvector_format():
    store = KbStore()
    assert store._embedding_to_pgvector([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"


def test_embedding_to_pgvector_none():
    store = KbStore()
    assert store._embedding_to_pgvector(None) is None
