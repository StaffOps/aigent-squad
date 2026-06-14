from typing import List, Dict, Optional
from dataclasses import dataclass
from src.core.bedrock import bedrock
from src.core.state_store import ConversationMessage


@dataclass
class ClassifierResult:
    """Result from intent classification"""
    selected_agent: str
    confidence: float
    reasoning: Optional[str] = None


class Classifier:
    """Intelligent intent classifier for agent routing"""

    SYSTEM_PROMPT = """You are AgentMatcher, an intelligent assistant that analyzes user queries and routes them to the most suitable specialist agent.

**CRITICAL**: The user's input may be a follow-up response to a previous interaction. The conversation history shows which agent was previously selected. If the user's input is a continuation (e.g., "yes", "ok", "tell me more", "1", "no", "again"), select the SAME agent as before.

Analyze the user's input and categorize it into one of the following agents:

<agents>
{agent_descriptions}
</agents>

**Guidelines**:
1. **Follow-ups**: For short responses like "yes", "ok", "more", "1", "no" → use the same agent from history
2. **Context switching**: If user explicitly changes topic → select new appropriate agent
3. **Confidence**: 
   - High (0.9+): Clear requests or obvious follow-ups
   - Medium (0.6-0.9): Some ambiguity but likely classification
   - Low (<0.6): Vague or multi-faceted requests

**Conversation History** (most recent last):
<history>
{history}
</history>

**Response Format** (JSON only, no preamble):
{{
  "selected_agent": "agent-name",
  "confidence": 0.95,
  "reasoning": "Brief explanation"
}}

If unable to classify, use "unknown" as selected_agent."""

    def __init__(self, registry):
        from src.core.registry import AgentRegistry
        self._registry: AgentRegistry = registry
        self._agent_names = registry.agent_names()
        self.agent_descriptions = "\n".join(
            f"- {agent.name}: {agent.description}"
            for agent in registry.list_agents()
        )

    async def classify(
        self,
        user_input: str,
        chat_history: List[ConversationMessage]
    ) -> ClassifierResult:
        """Classify user intent and select appropriate agent"""

        history_text = self._format_history(chat_history)

        prompt = self.SYSTEM_PROMPT.format(
            agent_descriptions=self.agent_descriptions,
            history=history_text
        )

        response = bedrock.invoke(
            messages=[{"role": "user", "content": user_input}],
            system_prompt=prompt,
            temperature=0.3,
            use_cache=True
        )

        import json
        try:
            result = json.loads(response)
            return ClassifierResult(
                selected_agent=result.get("selected_agent", "unknown"),
                confidence=result.get("confidence", 0.5),
                reasoning=result.get("reasoning")
            )
        except json.JSONDecodeError:
            for agent_name in self._agent_names:
                if agent_name in response.lower():
                    return ClassifierResult(
                        selected_agent=agent_name,
                        confidence=0.5,
                        reasoning="Fallback parsing"
                    )

            return ClassifierResult(
                selected_agent="unknown",
                confidence=0.0,
                reasoning="Failed to parse classifier response"
            )

    def _format_history(self, messages: List[ConversationMessage]) -> str:
        """Format conversation history for prompt"""
        if not messages:
            return "No previous conversation"

        lines = []
        for msg in messages[-10:]:
            agent_info = f" [{msg.agent_id}]" if msg.agent_id else ""
            lines.append(f"{msg.role}{agent_info}: {msg.content}")

        return "\n".join(lines)


# Will be initialized after registry discovery
classifier = None
