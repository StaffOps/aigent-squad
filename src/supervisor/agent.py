from typing import Dict
import time
from datetime import datetime, timezone
from opentelemetry import trace
from src.core.classifier import Classifier, ClassifierResult
from src.core.state_store import storage, ConversationMessage
from src.core.logger import logger, log_request, log_response, log_error
from src.core.metrics import request_counter, error_counter, request_duration
from src.core.registry import AgentRegistry
from src.core.generic_agent import GenericAgent
from src.core.adapters import create_adapters

tracer = trace.get_tracer(__name__)


class SupervisorAgent:
    """Supervisor that routes to in-process specialist agents using intelligent classifier"""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.classifier = Classifier(registry)

        # Create in-process agent instances
        self.agents: dict[str, GenericAgent] = {}
        for config in registry.list_agents():
            adapters = create_adapters(config.datasources)
            prompt = registry.get_prompt(config.name)
            self.agents[config.name] = GenericAgent(config, prompt, adapters)

        logger.info(f"Supervisor initialized with {len(self.agents)} agents: {list(self.agents.keys())}")

    async def process_request(
        self,
        user_input: str,
        user_id: str,
        session_id: str
    ) -> Dict:
        """Process user request with intelligent routing"""

        start_time = time.time()

        with tracer.start_as_current_span("supervisor.process_request") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            span.set_attribute("input_length", len(user_input))

            log_request("supervisor", user_id, session_id, user_input)

            try:
                # 1. Get global conversation history for classifier
                chat_history = await storage.fetch_all_chats(user_id, session_id)

                # 2. Classify intent
                with tracer.start_as_current_span("classifier.classify"):
                    classification: ClassifierResult = await self.classifier.classify(
                        user_input,
                        chat_history
                    )

                logger.info("Intent classified", extra={
                    "selected_agent": classification.selected_agent,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning
                })

                if classification.selected_agent == "unknown":
                    return {
                        "agent": "supervisor",
                        "response": "I'm not sure how to help with that. Could you please rephrase your question?",
                        "confidence": classification.confidence
                    }

                # 3. Save user message
                user_message = ConversationMessage(
                    role="user",
                    content=user_input,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_id=classification.selected_agent
                )
                await storage.save_chat_message(
                    user_id,
                    session_id,
                    classification.selected_agent,
                    user_message
                )

                # 4. Get agent-specific history
                agent_history = await storage.fetch_chat(
                    user_id,
                    session_id,
                    classification.selected_agent
                )

                # 5. Call specialist agent in-process
                agent = self.agents[classification.selected_agent]

                with tracer.start_as_current_span("agent.process") as agent_span:
                    agent_span.set_attribute("agent_id", classification.selected_agent)

                    try:
                        result = await agent.process_request(
                            input_text=user_input,
                            user_id=user_id,
                            session_id=session_id,
                            chat_history=agent_history,
                        )
                    except Exception as e:
                        logger.error("Agent processing error", extra={
                            "agent_id": classification.selected_agent,
                            "error": str(e)
                        })
                        error_counter.add(1, {"agent_id": classification.selected_agent, "error_type": "internal"})
                        return {
                            "agent": classification.selected_agent,
                            "response": f"Error processing request in {classification.selected_agent} agent.",
                            "confidence": 0.0,
                            "error": str(e)
                        }

                response_text = result.content

                # Record RED metrics
                agent_attrs = {"agent_id": classification.selected_agent}
                request_counter.add(1, agent_attrs)
                request_duration.record((time.time() - start_time) * 1000, agent_attrs)

                # 6. Save assistant message
                assistant_message = ConversationMessage(
                    role="assistant",
                    content=response_text,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_id=classification.selected_agent
                )
                await storage.save_chat_message(
                    user_id,
                    session_id,
                    classification.selected_agent,
                    assistant_message
                )

                duration_ms = (time.time() - start_time) * 1000
                log_response("supervisor", user_id, session_id, len(response_text), duration_ms)

                return {
                    "agent": classification.selected_agent,
                    "response": response_text,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning
                }

            except Exception as e:
                log_error("supervisor", e, user_id=user_id, session_id=session_id)
                return {
                    "agent": "supervisor",
                    "response": "An unexpected error occurred. Please try again.",
                    "confidence": 0.0,
                    "error": str(e)
                }

    async def close(self):
        """No-op — agents are in-process, no connections to close."""
        pass


# Initialize registry and supervisor
registry = AgentRegistry()
registry.discover()
supervisor = SupervisorAgent(registry)
