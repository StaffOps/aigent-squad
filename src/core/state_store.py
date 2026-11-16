import boto3
from typing import Any, List, Optional, TYPE_CHECKING
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from src.core.config import settings
from src.core.logger import logger

if TYPE_CHECKING:
    from src.core.response_quality import QualityAssessment


@dataclass
class ConversationMessage:
    """Standard message format"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    agent_id: Optional[str] = None
    # Spec 41: optional structured quality assessment (not persisted to DynamoDB)
    quality_assessment: Optional["QualityAssessment"] = field(default=None, repr=False)


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


class ChatStorage:
    """DynamoDB conversation storage with fail-open resilience"""

    def __init__(self) -> None:
        self.dynamodb = dynamodb_resource()
        self.table = self.dynamodb.Table(settings.dynamodb_sessions_table)

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
            ttl = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())

            self.table.put_item(
                Item={
                    'pk': pk,
                    'sk': sk,
                    'user_id': user_id,
                    'session_id': session_id,
                    'agent_id': agent_id,
                    'role': message.role,
                    'content': message.content,
                    'timestamp': message.timestamp,
                    'ttl': ttl
                }
            )
        except Exception as e:
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

            response = self.table.query(
                KeyConditionExpression='pk = :pk AND begins_with(sk, :agent)',
                ExpressionAttributeValues={
                    ':pk': pk,
                    ':agent': agent_id
                },
                ScanIndexForward=False,
                Limit=max_messages
            )

            messages = []
            for item in reversed(response.get('Items', [])):
                messages.append(ConversationMessage(
                    role=item['role'],
                    content=item['content'],
                    timestamp=item['timestamp'],
                    agent_id=item['agent_id']
                ))
            return messages
        except Exception as e:
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

            response = self.table.query(
                KeyConditionExpression='pk = :pk',
                ExpressionAttributeValues={':pk': pk},
                ScanIndexForward=False,
                Limit=max_messages
            )

            messages = []
            for item in reversed(response.get('Items', [])):
                messages.append(ConversationMessage(
                    role=item['role'],
                    content=item['content'],
                    timestamp=item['timestamp'],
                    agent_id=item.get('agent_id')
                ))
            return messages
        except Exception as e:
            logger.warning("DynamoDB fetch_all_chats failed (fail-open)", extra={
                "error": str(e), "user_id": user_id, "session_id": session_id
            })
            return []


storage = ChatStorage()
