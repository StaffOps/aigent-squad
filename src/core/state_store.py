"""Conversation history store (spec 46).

``storage`` is the process-wide history store, chosen by ``HISTORY_BACKEND``:
``redis`` (default), ``dynamodb`` (AWS) or ``postgres``. Every backend is
fail-open — a store outage yields empty history, never a failed request
(AGENTS.md invariant 6) — and keeps per-agent isolation (invariant 2).
"""
import asyncio

import boto3
from typing import Any, List, Optional, Protocol
from datetime import datetime, timedelta, timezone
from src.core.config import settings
from src.core.conversation import (  # re-exported: existing imports use state_store
    HISTORY_TTL_SECONDS,
    ConversationMessage,
    cap_history_content,
    record_history_error as _record_error,
)
from src.core.logger import logger

__all__ = [
    "ChatStorage", "ConversationMessage", "DynamoDBHistoryStore", "HISTORY_TTL_SECONDS",
    "HistoryStore", "build_history_store", "dynamodb_resource", "storage",
]


def dynamodb_resource() -> Any:
    """The one place a DynamoDB resource is built (history store + /ready probe).

    With ``DYNAMODB_ENDPOINT`` set and ``DYNAMODB_LOCAL_STATIC_CREDENTIALS=true``
    (docker-compose only) requests are signed with fixed dummy credentials, so an
    expired AWS SSO token — needed only for Bedrock — does not break local history.
    """
    kwargs: dict[str, Any] = {
        "region_name": settings.aws_region,
        "endpoint_url": settings.dynamodb_endpoint,
    }
    if settings.dynamodb_endpoint and settings.dynamodb_local_static_credentials:
        # DynamoDB Local accepts any key; these are not secrets (local dev only).
        kwargs["aws_access_key_id"] = "local"
        kwargs["aws_secret_access_key"] = "local"  # nosec B106 — DynamoDB Local dummy
    return boto3.resource("dynamodb", **kwargs)


# Upper bound on DynamoDB query pages per per-agent history read (F-027): each page
# evaluates up to ``max_messages`` items before the ttl filter drops expired ones.
_MAX_QUERY_PAGES = 3

# fetch_all_chats reads the whole session (sk = "<agent>#<iso>" sorts by agent
# name, not time, so no Limit/order trick yields the newest across agents). A
# session is bounded by the 24 h retention; these caps bound a pathological one.
_MAX_SESSION_PAGES = 10
_MAX_SESSION_ITEMS = 2000

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _item_time(item: dict[str, Any]) -> tuple[datetime, str]:
    """Sort key for a history item: the stored ``timestamp``, else the write time in ``sk``."""
    sk = str(item.get("sk", ""))
    when = _parse_iso(item.get("timestamp")) or _parse_iso(sk.partition("#")[2]) or _EPOCH
    return when, sk


class HistoryStore(Protocol):
    """What the supervisor needs from a history backend (spec 46 AC 1)."""

    backend: str

    async def save_chat_message(
        self, user_id: str, session_id: str, agent_id: str,
        message: ConversationMessage, max_history: int = 20,
    ) -> None: ...

    async def fetch_chat(
        self, user_id: str, session_id: str, agent_id: str, max_messages: int = 20,
    ) -> List[ConversationMessage]: ...

    async def fetch_all_chats(
        self, user_id: str, session_id: str, max_messages: int = 50,
    ) -> List[ConversationMessage]: ...

    async def aclose(self) -> None:
        """Release background tasks and connections at shutdown (may be a no-op)."""
        ...


class DynamoDBHistoryStore:
    """DynamoDB conversation storage with fail-open resilience"""

    backend = "dynamodb"

    def __init__(self) -> None:
        self.dynamodb = dynamodb_resource()
        self.table = self.dynamodb.Table(settings.dynamodb_sessions_table)

    @staticmethod
    def _ttl_filter(values: dict[str, Any]) -> dict[str, Any]:
        return {
            # 'ttl' is a DynamoDB reserved word → aliased via ExpressionAttributeNames.
            'FilterExpression': '#ttl > :now',
            'ExpressionAttributeNames': {'#ttl': 'ttl'},
            'ExpressionAttributeValues': {
                **values, ':now': int(datetime.now(timezone.utc).timestamp()),
            },
        }

    def _query_unexpired(
        self, key_condition: str, values: dict[str, Any], max_messages: int,
    ) -> List[dict[str, Any]]:
        """Newest-first items for ``key_condition`` whose ``ttl`` is still ahead (F-027).

        Only for a key condition whose sort-key order IS time order (one agent's
        ``<agent>#<iso>`` prefix). DynamoDB deletes expired items asynchronously
        (up to days later), so the read filters on ``ttl`` itself. ``Limit`` caps
        items *evaluated* before the filter, so a page can come back short while
        expired items remain; follow ``LastEvaluatedKey`` until ``max_messages``
        unexpired items are collected, bounded by ``_MAX_QUERY_PAGES`` so a
        session full of expired rows cannot turn one read into an unbounded scan.
        Blocking (boto3): call it through ``asyncio.to_thread``.
        """
        if max_messages <= 0:
            return []
        kwargs: dict[str, Any] = {
            'KeyConditionExpression': key_condition,
            **self._ttl_filter(values),
            'ScanIndexForward': False,
            'Limit': max_messages,
        }
        items: List[dict[str, Any]] = []
        for _ in range(_MAX_QUERY_PAGES):
            response = self.table.query(**kwargs)
            items.extend(response.get('Items', []))
            last_key = response.get('LastEvaluatedKey')
            if len(items) >= max_messages or not last_key:
                break
            kwargs['ExclusiveStartKey'] = last_key
        return items[:max_messages]

    def _query_session(self, pk: str, max_messages: int) -> List[dict[str, Any]]:
        """The newest ``max_messages`` unexpired items of a session, ACROSS agents, oldest-first.

        The sort key is ``<agent>#<iso>``, so key order is agent-name order: a
        newest-first ``Limit`` read would return one agent's items and can come
        back empty (after the ttl filter) while another agent has fresh ones. So
        the session is read whole (bounded by ``_MAX_SESSION_PAGES`` /
        ``_MAX_SESSION_ITEMS``), filtered, and ordered by the stored timestamp.
        The stored key layout is unchanged. Blocking: use ``asyncio.to_thread``.
        """
        if max_messages <= 0:
            return []
        kwargs: dict[str, Any] = {
            'KeyConditionExpression': 'pk = :pk',
            **self._ttl_filter({':pk': pk}),
        }
        items: List[dict[str, Any]] = []
        last_key: Any = None
        for _ in range(_MAX_SESSION_PAGES):
            response = self.table.query(**kwargs)
            items.extend(response.get('Items', []))
            last_key = response.get('LastEvaluatedKey')
            if not last_key or len(items) >= _MAX_SESSION_ITEMS:
                break
            kwargs['ExclusiveStartKey'] = last_key
        if last_key:  # stopped at a cap with items left unread
            logger.warning("DynamoDB session history read truncated at the cap", extra={
                "items_read": len(items), "max_pages": _MAX_SESSION_PAGES,
                "max_items": _MAX_SESSION_ITEMS,
            })
        items.sort(key=_item_time)
        return items[-max_messages:]

    async def save_chat_message(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        message: ConversationMessage,
        max_history: int = 20
    ) -> None:
        try:
            pk = f"{user_id}#{session_id}"
            sk = f"{agent_id}#{datetime.now(timezone.utc).isoformat()}"
            ttl = int((datetime.now(timezone.utc) + timedelta(seconds=HISTORY_TTL_SECONDS)).timestamp())

            await asyncio.to_thread(
                self.table.put_item,
                Item={
                    'pk': pk,
                    'sk': sk,
                    'user_id': user_id,
                    'session_id': session_id,
                    'agent_id': agent_id,
                    'role': message.role,
                    'content': cap_history_content(message.content),
                    'timestamp': message.timestamp,
                    'ttl': ttl
                }
            )
        except Exception as e:
            _record_error("dynamodb", "save")
            logger.warning("DynamoDB save failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })

    async def fetch_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        max_messages: int = 20
    ) -> List[ConversationMessage]:
        try:
            pk = f"{user_id}#{session_id}"

            # The '#' delimiter: agent "aws" must not match "aws-cost#...".
            items = await asyncio.to_thread(
                self._query_unexpired,
                'pk = :pk AND begins_with(sk, :agent)',
                {':pk': pk, ':agent': f"{agent_id}#"},
                max_messages,
            )

            messages = []
            for item in reversed(items):
                messages.append(ConversationMessage(
                    role=item['role'],
                    content=item['content'],
                    timestamp=item['timestamp'],
                    agent_id=item['agent_id']
                ))
            return messages
        except Exception as e:
            _record_error("dynamodb", "fetch")
            logger.warning("DynamoDB fetch_chat failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def fetch_all_chats(
        self,
        user_id: str,
        session_id: str,
        max_messages: int = 50
    ) -> List[ConversationMessage]:
        try:
            pk = f"{user_id}#{session_id}"

            items = await asyncio.to_thread(self._query_session, pk, max_messages)

            messages = []
            for item in items:  # already oldest-first
                messages.append(ConversationMessage(
                    role=item['role'],
                    content=item['content'],
                    timestamp=item['timestamp'],
                    agent_id=item.get('agent_id')
                ))
            return messages
        except Exception as e:
            _record_error("dynamodb", "fetch")
            logger.warning("DynamoDB fetch_all_chats failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []

    async def aclose(self) -> None:
        """No-op: boto3 holds no background work to cancel."""


# Back-compat name: the DynamoDB store was the only one before spec 46.
ChatStorage = DynamoDBHistoryStore


def build_history_store(backend: Optional[str] = None) -> HistoryStore:
    """Factory: one backend per process, chosen at startup (spec 46 Decision 5)."""
    name = backend or settings.history_backend
    if name == "dynamodb":
        store: HistoryStore = DynamoDBHistoryStore()
    elif name == "redis":
        from src.core.history_stores import RedisHistoryStore
        store = RedisHistoryStore()
    elif name == "postgres":
        from src.core.history_stores import PostgresHistoryStore
        store = PostgresHistoryStore()
    else:  # Settings' Literal already rejects this; guard direct callers too.
        raise ValueError(f"unknown HISTORY_BACKEND: {name!r} (redis|dynamodb|postgres)")
    logger.info("history_backend selected", extra={"history_backend": name})
    return store


storage: HistoryStore = build_history_store()
