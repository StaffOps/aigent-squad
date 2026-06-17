"""Tests for src/core/state_store.py — DynamoDB ChatStorage."""
import pytest
from unittest.mock import patch, MagicMock

from src.core.state_store import ChatStorage, ConversationMessage


@pytest.fixture
def chat_storage():
    with patch("src.core.state_store.boto3") as mock_boto3:
        mock_table = MagicMock()
        mock_boto3.resource.return_value.Table.return_value = mock_table
        store = ChatStorage()
        store.table = mock_table
    return store, mock_table


@pytest.mark.asyncio
async def test_save_chat_message_writes_to_dynamodb(chat_storage):
    store, mock_table = chat_storage
    msg = ConversationMessage(role="user", content="hello", timestamp="2026-01-01T00:00:00Z", agent_id="aws")

    await store.save_chat_message("user1", "sess1", "aws", msg)

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args[1]["Item"]
    assert item["pk"] == "user1#sess1"
    assert item["sk"].startswith("aws#")
    assert item["role"] == "user"
    assert item["content"] == "hello"


@pytest.mark.asyncio
async def test_save_chat_message_swallows_exception(chat_storage):
    store, mock_table = chat_storage
    mock_table.put_item.side_effect = Exception("DynamoDB down")
    msg = ConversationMessage(role="user", content="hi", timestamp="2026-01-01T00:00:00Z")

    # Should not raise
    await store.save_chat_message("u1", "s1", "aws", msg)


@pytest.mark.asyncio
async def test_fetch_chat_returns_messages(chat_storage):
    store, mock_table = chat_storage
    mock_table.query.return_value = {
        "Items": [
            {"role": "user", "content": "q1", "timestamp": "2026-01-01T00:00:00Z", "agent_id": "aws"},
            {"role": "assistant", "content": "a1", "timestamp": "2026-01-01T00:00:01Z", "agent_id": "aws"},
        ]
    }

    result = await store.fetch_chat("user1", "sess1", "aws")
    assert len(result) == 2
    assert all(isinstance(m, ConversationMessage) for m in result)
    assert result[0].role == "assistant"  # reversed order
    assert result[1].role == "user"


@pytest.mark.asyncio
async def test_fetch_chat_returns_empty_on_error(chat_storage):
    store, mock_table = chat_storage
    mock_table.query.side_effect = Exception("timeout")

    result = await store.fetch_chat("u1", "s1", "aws")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_all_chats_aggregates_across_agents(chat_storage):
    store, mock_table = chat_storage
    mock_table.query.return_value = {
        "Items": [
            {"role": "user", "content": "q1", "timestamp": "t1", "agent_id": "aws"},
            {"role": "assistant", "content": "a1", "timestamp": "t2", "agent_id": "obs"},
        ]
    }

    result = await store.fetch_all_chats("user1", "sess1")
    assert len(result) == 2
    assert result[0].agent_id == "obs"
    assert result[1].agent_id == "aws"


@pytest.mark.asyncio
async def test_fetch_all_chats_returns_empty_on_error(chat_storage):
    store, mock_table = chat_storage
    mock_table.query.side_effect = Exception("fail")

    result = await store.fetch_all_chats("u1", "s1")
    assert result == []
