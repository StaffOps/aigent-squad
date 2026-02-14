from abc import ABC, abstractmethod
from typing import List, Optional, AsyncIterable, Union
from pathlib import Path
from src.core.state_store import ConversationMessage

class Agent(ABC):
    """Base class for all specialist agents"""
    
    def __init__(self, agent_id: str, name: str, description: str):
        self.id = agent_id
        self.name = name
        self.description = description
        self.streaming = False
        self.save_chat = True
    
    def _load_prompt(self) -> str:
        """Load system prompt from prompt.md in agent directory"""
        prompt_path = Path(__file__).parent / "prompt.md"
        if prompt_path.exists():
            return prompt_path.read_text()
        return f"You are {self.name}. {self.description}"
    
    @abstractmethod
    async def process_request(
        self,
        input_text: str,
        user_id: str,
        session_id: str,
        chat_history: List[ConversationMessage],
        additional_params: Optional[dict] = None
    ) -> Union[ConversationMessage, AsyncIterable[str]]:
        """
        Process user request with conversation history.
        
        Args:
            input_text: User's query
            user_id: User identifier
            session_id: Session identifier
            chat_history: Agent's own conversation history (isolated)
            additional_params: Optional parameters
        
        Returns:
            ConversationMessage for non-streaming
            AsyncIterable[str] for streaming responses
        """
        pass
    
    def is_streaming_enabled(self) -> bool:
        """Check if agent supports streaming"""
        return self.streaming
