"""Resilience pattern tests — contract/behavior focused.

Tests verify fail-open semantics, circuit breaker behavior,
and keyword fallback without depending on implementation details.
"""
import asyncio
import inspect
import time

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.core.circuit_breaker import CircuitBreaker, CircuitState


# --- Bedrock async contract ---


def test_bedrock_invoke_is_async():
    """bedrock.invoke must be a coroutine function (async)."""
    from src.core.bedrock import BedrockClient

    assert inspect.iscoroutinefunction(BedrockClient.invoke)


# --- Circuit breaker behavior ---


@pytest.mark.asyncio
async def test_bedrock_circuit_breaker_opens():
    """After N failures, circuit breaker opens and rejects immediately."""
    with patch("src.core.bedrock.settings") as mock_settings, \
         patch("src.core.bedrock.boto3"):
        mock_settings.aws_region = "us-east-1"
        mock_settings.bedrock_model_id = "test-model"

        from src.core.bedrock import BedrockClient

        client = BedrockClient.__new__(BedrockClient)
        client.client = MagicMock()
        client.model_id = "test-model"
        client.max_retries = 1
        client.base_delay = 0
        client.circuit_breaker = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)

        # Patch _invoke_sync to always raise
        client._invoke_sync = MagicMock(side_effect=Exception("bedrock down"))

        # Trip the breaker with 3 failures
        for _ in range(3):
            with pytest.raises(Exception):
                await client.invoke([], "system", max_tokens=100)

        assert client.circuit_breaker.state == CircuitState.OPEN

        # Reset mock call count to verify no further calls
        client._invoke_sync.reset_mock()

        # Next call should fail immediately with circuit breaker message
        with pytest.raises(Exception, match="circuit breaker"):
            await client.invoke([], "system", max_tokens=100)

        # _invoke_sync should NOT have been called (breaker rejected)
        client._invoke_sync.assert_not_called()


@pytest.mark.asyncio
async def test_bedrock_circuit_breaker_recovers():
    """After recovery_timeout, breaker transitions to half-open and allows a retry."""
    with patch("src.core.bedrock.settings") as mock_settings, \
         patch("src.core.bedrock.boto3"):
        mock_settings.aws_region = "us-east-1"
        mock_settings.bedrock_model_id = "test-model"

        from src.core.bedrock import BedrockClient

        client = BedrockClient.__new__(BedrockClient)
        client.client = MagicMock()
        client.model_id = "test-model"
        client.max_retries = 1
        client.base_delay = 0
        client.circuit_breaker = CircuitBreaker("test", failure_threshold=3, recovery_timeout=0.1)

        client._invoke_sync = MagicMock(side_effect=Exception("bedrock down"))

        # Trip the breaker
        for _ in range(3):
            with pytest.raises(Exception):
                await client.invoke([], "system", max_tokens=100)

        assert client.circuit_breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Now _invoke_sync should be attempted again (half-open)
        client._invoke_sync.reset_mock()
        client._invoke_sync.side_effect = Exception("still down")

        with pytest.raises(Exception, match="still down"):
            await client.invoke([], "system", max_tokens=100)

        # Confirms the call was attempted (not rejected by breaker)
        client._invoke_sync.assert_called_once()


# --- Cache fail-open ---


def test_cache_failopen_get():
    """cache.get returns None when Redis raises ConnectionError."""
    with patch("src.core.cache.settings") as mock_settings:
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_ssl = False
        mock_settings.redis_password = None

        from src.core.cache import CacheStore

        store = CacheStore.__new__(CacheStore)
        store.redis = MagicMock()
        store.redis.get.side_effect = ConnectionError("connection refused")

        result = store.get("some-key")
        assert result is None


def test_cache_failopen_set():
    """cache.set does not raise when Redis is down."""
    with patch("src.core.cache.settings") as mock_settings:
        mock_settings.redis_host = "localhost"
        mock_settings.redis_port = 6379
        mock_settings.redis_ssl = False
        mock_settings.redis_password = None

        from src.core.cache import CacheStore

        store = CacheStore.__new__(CacheStore)
        store.redis = MagicMock()
        store.redis.setex.side_effect = ConnectionError("connection refused")

        # Should not raise
        store.set("key", {"data": 1}, ttl=60)


def test_cache_lazy_connection_failure():
    """CacheStore init with unreachable Redis should not crash."""
    with patch("src.core.cache.settings") as mock_settings, \
         patch("src.core.cache.redis.Redis", side_effect=Exception("unreachable")):
        mock_settings.redis_host = "unreachable-host"
        mock_settings.redis_port = 6379
        mock_settings.redis_ssl = False
        mock_settings.redis_password = None

        from src.core.cache import CacheStore

        # Should not raise
        store = CacheStore()
        assert store.redis is None


# --- State store fail-open ---


@pytest.mark.asyncio
async def test_state_store_failopen_fetch():
    """fetch_chat returns [] when DynamoDB raises."""
    with patch("src.core.state_store.settings") as mock_settings, \
         patch("src.core.state_store.boto3") as mock_boto:
        mock_settings.aws_region = "us-east-1"
        mock_settings.dynamodb_endpoint = None
        mock_settings.dynamodb_sessions_table = "sessions"

        mock_table = MagicMock()
        mock_table.query.side_effect = Exception("DynamoDB unavailable")
        mock_boto.resource.return_value.Table.return_value = mock_table

        from src.core.state_store import ChatStorage

        store = ChatStorage()
        result = await store.fetch_chat("user1", "session1", "agent1")
        assert result == []


@pytest.mark.asyncio
async def test_state_store_failopen_save():
    """save_chat_message does not raise when DynamoDB is down."""
    with patch("src.core.state_store.settings") as mock_settings, \
         patch("src.core.state_store.boto3") as mock_boto:
        mock_settings.aws_region = "us-east-1"
        mock_settings.dynamodb_endpoint = None
        mock_settings.dynamodb_sessions_table = "sessions"

        mock_table = MagicMock()
        mock_table.put_item.side_effect = Exception("DynamoDB unavailable")
        mock_boto.resource.return_value.Table.return_value = mock_table

        from src.core.state_store import ChatStorage, ConversationMessage

        store = ChatStorage()
        msg = ConversationMessage(role="user", content="hello", timestamp="2026-01-01T00:00:00Z")

        # Should not raise
        await store.save_chat_message("user1", "session1", "agent1", msg)


# --- Classifier keyword fallback ---


@pytest.mark.asyncio
async def test_classifier_keyword_fallback():
    """When Bedrock fails, classifier uses keyword matching from agent configs."""
    mock_registry = MagicMock()
    mock_config = MagicMock()
    mock_config.name = "aws"
    mock_config.description = "AWS agent"
    mock_config.routing_keywords = ["ec2", "instance", "s3", "bucket"]
    mock_registry.list_agents.return_value = [mock_config]
    mock_registry.agent_names.return_value = ["aws"]

    from src.core.classifier import Classifier

    clf = Classifier(mock_registry)

    with patch("src.core.classifier.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(side_effect=Exception("Bedrock circuit breaker is OPEN"))

        result = await clf.classify("list ec2 instances", [])

        assert result.selected_agent == "aws"


@pytest.mark.asyncio
async def test_classifier_keyword_fallback_no_match():
    """When Bedrock fails and no keywords match, return 'unknown'."""
    mock_registry = MagicMock()
    mock_config = MagicMock()
    mock_config.name = "aws"
    mock_config.description = "AWS agent"
    mock_config.routing_keywords = ["ec2", "instance", "s3", "bucket"]
    mock_registry.list_agents.return_value = [mock_config]
    mock_registry.agent_names.return_value = ["aws"]

    from src.core.classifier import Classifier

    clf = Classifier(mock_registry)

    with patch("src.core.classifier.bedrock") as mock_bedrock:
        mock_bedrock.invoke = AsyncMock(side_effect=Exception("Bedrock circuit breaker is OPEN"))

        result = await clf.classify("hello", [])

        assert result.selected_agent == "unknown"
