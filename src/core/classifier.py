import json
from typing import Any, List, Optional
from dataclasses import dataclass, field
from src.core.bedrock import bedrock
from src.core.guardrail import GuardrailBlockedError
from src.core.state_store import ConversationMessage
from src.core.token_budget import truncate_history_by_tokens
from src.core.logger import logger


@dataclass
class AgentMatch:
    agent: str
    confidence: float
    # B-14: focused sub-question for this agent (≤1-2 sentences), emitted by
    # the classifier in the same Haiku call. Includes time window when the
    # original question is time-bound. Empty string = not available (fallback
    # to the raw user question at dispatch).
    sub_query: str = ""


@dataclass
class ClassifierResult:
    """Result from intent classification — supports N agents."""
    agents: list[AgentMatch] = field(default_factory=list)
    reasoning: Optional[str] = None
    complexity: str = "standard"  # spec 38: simple|standard|complex

    @property
    def selected_agent(self) -> str:
        """Backward compat: first agent or 'unknown'."""
        return self.agents[0].agent if self.agents else "unknown"

    @property
    def confidence(self) -> float:
        """Backward compat: first agent's confidence or 0."""
        return self.agents[0].confidence if self.agents else 0.0


class Classifier:
    """Intelligent intent classifier with keyword fallback"""

    SYSTEM_PROMPT = """You are AgentMatcher, an intelligent assistant that analyzes user queries and routes them to the most suitable specialist agent(s).

**CRITICAL**: The user's input may be a follow-up response to a previous interaction. The conversation history shows which agent was previously selected. If the user's input is a continuation (e.g., "yes", "ok", "tell me more", "1", "no", "again"), select the SAME agent as before.

Analyze the user's input and select one or more agents from:

<agents>
{agent_descriptions}
</agents>

**Guidelines**:
1. **Follow-ups**: For short responses like "yes", "ok", "more", "1", "no" → use the same agent from history (single agent)
2. **Single-domain queries** (most common): return 1 agent
3. **Cross-domain queries** (e.g., "why did cost go up after deploy?"): return 2-3 agents
4. **Never return more than 3 agents**
5. **Empty agents list = unknown** (unable to classify)
6. **Action/mutation-phrased requests still route** — a request phrased as an
   instruction to perform a write/mutating action within a domain ("terminate this
   instance", "delete that pod", "go ahead and purchase the RI", even urgent or
   "I authorize you" framing) still belongs to that domain's specialist. Route it
   there — the specialist will explain why it can't comply (agents are read-only).
   Do NOT return unknown just because the phrasing is an action rather than a query.
7. **Confidence**:
   - High (0.9+): Clear requests or obvious follow-ups
   - Medium (0.6-0.9): Some ambiguity but likely classification
   - Low (<0.6): Vague or multi-faceted requests

**Conversation History** (most recent last):
<history>
{history}
</history>

**Response Format** (JSON only, no preamble):
{{
  "agents": [
    {{"agent": "agent-name", "confidence": 0.95, "sub_query": "Focused 1-2 sentence question for this agent"}}
  ],
  "reasoning": "Brief explanation",
  "complexity": "simple|standard|complex"
}}

**complexity rules** (model-tier routing — answer BEFORE agents):
- "simple": single-agent, factual lookup, expected ≤1 tool call, high confidence. Examples: "what's the CPU of pod X?", "list namespaces".
- "complex": multi-agent fan-out (2+ agents), RCA/investigation, multi-signal correlation, troubleshooting ("why is X slow?", "what caused the outage?"). Root cause analysis and multi-signal investigation are NEVER simple.
- "standard": everything else (single-agent but non-trivial, moderate confidence, 2+ tool calls likely).
When in doubt, prefer "standard" over "simple" — under-tiering is safer than over-tiering.

**sub_query rules**:
- For EACH selected agent, write a focused, self-contained sub-question (1-2 sentences max) that tells the agent exactly what to investigate or answer.
- If the user's question is time-bound (e.g. "last 1h", "since yesterday", "últimas 2h"), include the time window in the sub_query.
- If the query is a simple follow-up ("yes", "ok", "more"), leave sub_query as an empty string.
- The sub_query should be in the SAME language as the user's original question.

If unable to classify, return an empty agents list."""

    def __init__(self, registry: Any) -> None:
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
        chat_history: List[ConversationMessage],
        user_id: str = "unknown",
        session_id: str = "",
    ) -> ClassifierResult:
        """Classify user intent; falls back to keyword matching on LLM failure.

        ``user_id``/``session_id`` flow to ``bedrock.invoke`` so classifier-stage
        guardrail blocks are attributable in the audit log and the classifier's
        token usage counts against the session budget (spec 14 findings A + C).
        """
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
                use_cache=True,
                agent_id="classifier",
                match_user_language=False,  # classifier returns JSON, not prose
                role="classifier",  # spec 11: uses Haiku (fast/cheap routing)
                user_id=user_id,
                session_id=session_id,
                # G-6 fix: ingress already guarded the genuine user question;
                # skip the per-stage INPUT scan that false-positives on the
                # assembled system_prompt (agent catalog with verbs like
                # "manage/delete/execute" tripping PROMPT_ATTACK filter).
                skip_input_guardrail=True,
            )
        except GuardrailBlockedError:
            # Fail-closed: a blocked input must NOT silently fall back to
            # keyword routing (that would proceed despite the security refusal).
            raise
        except Exception as e:
            logger.warning("Classifier LLM failed, using keyword fallback", extra={"error": str(e)})
            return self._keyword_fallback(user_input)

        try:
            result = json.loads(self._extract_json(response))
            agents_raw = result.get("agents", [])
            agents = [
                AgentMatch(
                    agent=a["agent"],
                    confidence=a.get("confidence", 0.5),
                    sub_query=a.get("sub_query", ""),
                )
                for a in agents_raw
                if a.get("agent") in self._agent_names
            ]

            # Spec 38: extract complexity from LLM response; heuristic fallback
            # when the field is absent or invalid.
            raw_complexity = result.get("complexity", "")
            if raw_complexity in ("simple", "standard", "complex"):
                complexity = raw_complexity
            else:
                complexity = self._heuristic_complexity(agents, user_input)

            return ClassifierResult(
                agents=agents,
                reasoning=result.get("reasoning"),
                complexity=complexity,
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            # Fallback: try to find agent name in raw response
            for agent_name in self._agent_names:
                if agent_name in response.lower():
                    return ClassifierResult(
                        agents=[AgentMatch(agent=agent_name, confidence=0.5)],
                        reasoning="Fallback parsing",
                        complexity="standard",
                    )
            return ClassifierResult(
                agents=[],
                reasoning="Failed to parse classifier response",
                complexity="standard",
            )

    @staticmethod
    def _extract_json(response: str) -> str:
        """Pull the JSON object out of an LLM response.

        Claude often wraps JSON in a ```json fenced block or adds a short
        preamble despite "JSON only" instructions. Strip that so the structured
        path parses (avoids falling back to the low-confidence keyword scan).
        Returns the substring from the first '{' to the last '}'; if none is
        found, returns the original text (json.loads then raises as before).
        """
        text = response.strip()
        if "```" in text:
            # Take the content of the first fenced block (``` or ```json).
            import re as _re
            m = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
            if m:
                text = m.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
        return text

    def _keyword_fallback(self, user_input: str) -> ClassifierResult:
        """Route by matching routing_keywords from agent configs; returns up to 3 matches."""
        lower = user_input.lower()
        scored: list[tuple[str, int]] = []

        for config in self._registry.list_agents():
            score = sum(1 for kw in config.routing_keywords if kw in lower)
            if score > 0:
                scored.append((config.name, score))

        if not scored:
            return ClassifierResult(
                agents=[],
                reasoning="keyword fallback: no match"
            )

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:3]
        max_score = top[0][1]

        agents = [
            AgentMatch(agent=name, confidence=round(score / max_score * 0.6, 2))
            for name, score in top
        ]
        return ClassifierResult(
            agents=agents,
            reasoning="keyword fallback (LLM unavailable)"
        )

    def _format_history(self, messages: List[ConversationMessage]) -> str:
        if not messages:
            return "No previous conversation"

        # Spec 11: truncate by tokens (not message count). The classifier gets
        # a smaller window (2000 tokens) since it only needs recent context for
        # follow-up detection — not the full history_max_tokens.
        truncated, _ = truncate_history_by_tokens(messages, max_tokens=2000)
        if not truncated:
            return "No previous conversation"

        lines = []
        for msg in truncated:
            agent_info = f" [{msg.agent_id}]" if msg.agent_id else ""
            lines.append(f"{msg.role}{agent_info}: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def _heuristic_complexity(agents: list[AgentMatch], user_input: str) -> str:
        """Fallback complexity heuristic when the LLM omits the field.

        Spec 38:
          - fan-out ≥2 agents → complex
          - short, single-agent, factual (≤ ~60 chars, 1 agent) → simple
          - else → standard
        """
        if len(agents) >= 2:
            return "complex"
        if len(agents) == 1 and len(user_input) <= 60:
            return "simple"
        return "standard"


# Will be initialized after registry discovery
classifier = None
