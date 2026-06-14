import boto3
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from src.core.config import settings
from src.core.logger import logger


@dataclass
class ConversationMessage:
    """Standard message format"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    agent_id: Optional[str] = None


class ChatStorage:
    """DynamoDB conversation storage with fail-open resilience"""

    def __init__(self):
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=settings.aws_region,
            endpoint_url=settings.dynamodb_endpoint
        )
        self.table = self.dynamodb.Table(settings.dynamodb_sessions_table)

    async def save_chat_message(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        message: ConversationMessage,
        max_history: int = 20
    ):
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
