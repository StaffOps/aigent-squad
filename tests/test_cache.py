"""Tests for src/core/cache.py"""
import json
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_redis():
    """Patch redis.Redis so CacheStore.__init__ doesn't connect."""
    mock_instance = MagicMock()
    with patch("src.core.cache.redis.Redis", return_value=mock_instance):
        from src.core.cache import CacheStore
        store = CacheStore()
    return store, mock_instance


def test_cache_set_get(mock_redis):
    store, redis_mock = mock_redis

    store.set("mykey", {"data": 42}, ttl=60, namespace="ns")
    redis_mock.setex.assert_called_once_with("ns:mykey", 60, json.dumps({"data": 42}))

    redis_mock.get.return_value = json.dumps({"data": 42})
    result = store.get("mykey", namespace="ns")
    redis_mock.get.assert_called_once_with("ns:mykey")
    assert result == {"data": 42}


def test_cache_get_miss(mock_redis):
    store, redis_mock = mock_redis
    redis_mock.get.return_value = None

    result = store.get("nonexistent", namespace="ns")
    assert result is None


def test_cache_delete(mock_redis):
    store, redis_mock = mock_redis

    store.delete("mykey", namespace="ns")
    redis_mock.delete.assert_called_once_with("ns:mykey")
