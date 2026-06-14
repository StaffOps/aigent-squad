import json
from typing import List, Optional
from dataclasses import dataclass
from src.core.bedrock import bedrock
from src.core.state_store import ConversationMessage
from src.core.logger import logger


@dataclass
class ClassifierResult:
    """Result from intent classification"""
    selected_agent: str
    confidence: float
    reasoning: Optional[str] = None


class Classifier:
    """Intelligent intent classifier with keyword fallback"""

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
        """Classify user intent; falls back to keyword matching on LLM failure"""
        history_text = self._format_history(chat_history)

        prompt = self.SYSTEM_PROMPT.format(
            agent_descriptions=self.agent_descriptions,
            history=history_text
        )

        try:
            response = await bedrock.invoke(
                messages=[{"role": "user", "content": user_input}],
                system_prompt=prompt,
                temperature=0.3,
                use_cache=True
            )
        except Exception as e:
            logger.warning("Classifier LLM failed, using keyword fallback", extra={"error": str(e)})
            return self._keyword_fallback(user_input)

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

    def _keyword_fallback(self, user_input: str) -> ClassifierResult:
        """Route by matching routing_keywords from agent configs"""
        lower = user_input.lower()
        best_agent = None
        best_score = 0

        for config in self._registry.list_agents():
            score = sum(1 for kw in config.routing_keywords if kw in lower)
            if score > best_score:
                best_score = score
                best_agent = config.name

        if best_agent and best_score > 0:
            return ClassifierResult(
                selected_agent=best_agent,
                confidence=0.5,
                reasoning="keyword fallback (LLM unavailable)"
            )
        return ClassifierResult(
            selected_agent="unknown",
            confidence=0.0,
            reasoning="keyword fallback: no match"
        )

    def _format_history(self, messages: List[ConversationMessage]) -> str:
        if not messages:
            return "No previous conversation"

        lines = []
        for msg in messages[-10:]:
            agent_info = f" [{msg.agent_id}]" if msg.agent_id else ""
            lines.append(f"{msg.role}{agent_info}: {msg.content}")
        return "\n".join(lines)


# Will be initialized after registry discovery
classifier = None
