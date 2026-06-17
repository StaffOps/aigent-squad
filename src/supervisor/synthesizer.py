"""Synthesizer: fuses N agent responses into a single coherent answer."""
from otel_helper import get_tracer
from src.core.bedrock import bedrock
from src.core.metrics import synthesizer_calls

tracer = get_tracer(__name__)

SYNTHESIZER_PROMPT = """You are a response synthesizer. You receive a user query and N specialist agent responses, and produce a single coherent answer.

RULES:
1. Preserve attribution: mention which agent contributed what when relevant
2. Merge complementary information; don't repeat the same fact from multiple agents
3. If agents disagree, surface the disagreement
4. Keep it concise but complete
5. Don't add information not present in the agent responses

If any agents are listed as FAILED, include a brief note that the response is partial."""


class Synthesizer:
    async def synthesize(self, query: str, responses: list[tuple[str, str]], failed: list[str]) -> str:
        """
        responses: list of (agent_name, response_text)
        failed: list of agent names that failed
        """
        with tracer.start_as_current_span("synthesizer.synthesize") as span:
            span.set_attribute("agent_count", len(responses))
            span.set_attribute("failed_count", len(failed))

            if not responses:
                return "All agents failed to respond. Please try again."

            synthesizer_calls.add(1, {"has_failures": str(bool(failed))})

            agent_blocks = "\n\n".join(
                f"<agent name=\"{name}\">\n{resp}\n</agent>"
                for name, resp in responses
            )
            failed_note = f"\n\nFAILED agents: {', '.join(failed)}" if failed else ""

            user_msg = f"""User query:
<user_query>
{query}
</user_query>

Agent responses:
{agent_blocks}{failed_note}

Synthesize a single answer for the user."""

            response = await bedrock.invoke(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=SYNTHESIZER_PROMPT,
                temperature=0.3,
            )
            return response


synthesizer = Synthesizer()
