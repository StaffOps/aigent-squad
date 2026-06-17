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


def test_cache_set_serializes_value_to_json(mock_redis):
    store, redis_mock = mock_redis
    store.set("mykey", {"data": 42}, ttl=60, namespace="ns")
    redis_mock.setex.assert_called_once_with("ns:mykey", 60, json.dumps({"data": 42}))


def test_cache_get_deserializes_json(mock_redis):
    store, redis_mock = mock_redis
    redis_mock.get.return_value = json.dumps({"data": 42})
    result = store.get("mykey", namespace="ns")
    redis_mock.get.assert_called_once_with("ns:mykey")
    assert result == {"data": 42}


def test_cache_get_miss(mock_redis):
    store, redis_mock = mock_redis
    redis_mock.get.return_value = None
    result = store.get("nonexistent", namespace="ns")
    assert result is None


def test_cache_delete_calls_redis_delete(mock_redis):
    store, redis_mock = mock_redis
    store.delete("mykey", namespace="ns")
    redis_mock.delete.assert_called_once_with("ns:mykey")


def test_cache_exists_returns_true(mock_redis):
    store, redis_mock = mock_redis
    redis_mock.exists.return_value = 1
    assert store.exists("mykey", namespace="ns") is True
    redis_mock.exists.assert_called_once_with("ns:mykey")


def test_cache_exists_returns_false(mock_redis):
    store, redis_mock = mock_redis
    redis_mock.exists.return_value = 0
    assert store.exists("mykey", namespace="ns") is False


def test_cache_get_returns_none_when_redis_none():
    with patch("src.core.cache.redis.Redis", side_effect=Exception("conn fail")):
        from src.core.cache import CacheStore
        store = CacheStore()
    assert store.redis is None
    assert store.get("any") is None


def test_cache_set_noop_when_redis_none():
    with patch("src.core.cache.redis.Redis", side_effect=Exception("conn fail")):
        from src.core.cache import CacheStore
        store = CacheStore()
    # Should not raise
    store.set("key", "val", ttl=60)


def test_cache_delete_noop_when_redis_none():
    with patch("src.core.cache.redis.Redis", side_effect=Exception("conn fail")):
        from src.core.cache import CacheStore
        store = CacheStore()
    store.delete("key")


def test_cache_exists_returns_false_when_redis_none():
    with patch("src.core.cache.redis.Redis", side_effect=Exception("conn fail")):
        from src.core.cache import CacheStore
        store = CacheStore()
    assert store.exists("key") is False
