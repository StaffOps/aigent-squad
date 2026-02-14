import boto3
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from src.core.config import settings

@dataclass
class ConversationMessage:
    """Standard message format"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    agent_id: Optional[str] = None

class ChatStorage:
    """DynamoDB conversation storage with agent isolation"""
    
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
        """Save message for specific agent"""
        pk = f"{user_id}#{session_id}"
        sk = f"{agent_id}#{datetime.utcnow().isoformat()}"
        ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())
        
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
    
    async def fetch_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        max_messages: int = 20
    ) -> List[ConversationMessage]:
        """Fetch conversation history for SPECIFIC agent only"""
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
    
    async def fetch_all_chats(
        self,
        user_id: str,
        session_id: str,
        max_messages: int = 50
    ) -> List[ConversationMessage]:
        """Fetch GLOBAL conversation history (all agents) for classifier"""
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

# Singleton
storage = ChatStorage()
