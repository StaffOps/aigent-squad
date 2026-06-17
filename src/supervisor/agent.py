import asyncio
from typing import Dict
import time
from datetime import datetime, timezone
from opentelemetry import trace
from src.core.classifier import Classifier, ClassifierResult
from src.core.state_store import storage, ConversationMessage
from src.core.logger import logger, log_request, log_response, log_error
from src.core.metrics import request_counter, error_counter, request_duration, fanout_calls, fanout_agents_consulted, fanout_agents_failed
from src.core.registry import AgentRegistry
from src.core.generic_agent import GenericAgent
from src.core.adapters import create_adapters
from src.core.skills import SkillRegistry
from src.core.triage import should_investigate
from src.supervisor.synthesizer import synthesizer
from src.supervisor.investigation import run_investigation
from src.supervisor.distillation import distill_rca

tracer = trace.get_tracer(__name__)


class SupervisorAgent:
    """Supervisor that routes to in-process specialist agents using intelligent classifier"""

    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.classifier = Classifier(registry)
        self.max_agents = 3

        # Load global skills once (lazy-selected per query — spec 26)
        self.skill_registry = SkillRegistry()
        self.skill_registry.discover()

        # Create in-process agent instances
        self.agents: dict[str, GenericAgent] = {}
        for config in registry.list_agents():
            adapters = create_adapters(config.datasources)
            prompt = registry.get_prompt(config.name)
            self.agents[config.name] = GenericAgent(config, prompt, adapters, skill_registry=self.skill_registry)

        logger.info(f"Supervisor initialized with {len(self.agents)} agents: {list(self.agents.keys())}")

    async def process_request(
        self,
        user_input: str,
        user_id: str,
        session_id: str,
        mode: str = "query",
    ) -> Dict:
        """Process user request with intelligent routing and optional fan-out"""

        start_time = time.time()

        with tracer.start_as_current_span("supervisor.process_request") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            span.set_attribute("input_length", len(user_input))

            log_request("supervisor", user_id, session_id, user_input)

            try:
                # 0. Check if this warrants RCA investigation
                force_investigate = mode == "investigate"
                if should_investigate(user_input, force=force_investigate):
                    rca = await run_investigation(
                        symptom=user_input,
                        agents=self.agents,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    asyncio.create_task(distill_rca(rca))
                    duration_ms = (time.time() - start_time) * 1000
                    request_counter.add(1, {"agent_id": "investigation"})
                    request_duration.record(duration_ms, {"agent_id": "investigation"})
                    return {
                        "agent": "investigation",
                        "response": rca.hypothesis,
                        "confidence": rca.confidence,
                        "rca": rca.to_dict(),
                    }

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
                    "reasoning": classification.reasoning,
                    "agent_count": len(classification.agents),
                })

                if not classification.agents or classification.selected_agent == "unknown":
                    return {
                        "agent": "supervisor",
                        "response": "I'm not sure how to help with that. Could you please rephrase your question?",
                        "confidence": classification.confidence
                    }

                # 3. Apply max_agents cap and filter to known agents
                agents = [
                    a for a in classification.agents[:self.max_agents]
                    if a.agent in self.agents
                ]

                if not agents:
                    return {
                        "agent": "supervisor",
                        "response": "I'm not sure how to help with that. Could you please rephrase your question?",
                        "confidence": 0.0
                    }

                # 4. Save user message (tagged to primary agent)
                primary_agent = agents[0].agent
                user_message = ConversationMessage(
                    role="user",
                    content=user_input,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_id=primary_agent
                )
                await storage.save_chat_message(
                    user_id, session_id, primary_agent, user_message
                )

                # 5. Single agent fast-path (current behavior)
                if len(agents) == 1:
                    return await self._single_agent_call(
                        agents[0].agent, classification, user_input,
                        user_id, session_id, start_time
                    )

                # 6. Fan-out to multiple agents
                return await self._fan_out(
                    agents, classification, user_input,
                    user_id, session_id, start_time
                )

            except Exception as e:
                log_error("supervisor", e, user_id=user_id, session_id=session_id)
                return {
                    "agent": "supervisor",
                    "response": "An unexpected error occurred. Please try again.",
                    "confidence": 0.0,
                    "error": str(e)
                }

    async def _single_agent_call(
        self, agent_name: str, classification: ClassifierResult,
        user_input: str, user_id: str, session_id: str, start_time: float
    ) -> Dict:
        """Fast-path: route to a single agent."""
        agent_history = await storage.fetch_chat(user_id, session_id, agent_name)
        agent = self.agents[agent_name]

        with tracer.start_as_current_span("agent.process") as agent_span:
            agent_span.set_attribute("agent_id", agent_name)

            try:
                result = await agent.process_request(
                    input_text=user_input,
                    user_id=user_id,
                    session_id=session_id,
                    chat_history=agent_history,
                )
            except Exception as e:
                logger.error("Agent processing error", extra={
                    "agent_id": agent_name, "error": str(e)
                })
                error_counter.add(1, {"agent_id": agent_name, "error_type": "internal"})
                return {
                    "agent": agent_name,
                    "response": f"Error processing request in {agent_name} agent.",
                    "confidence": 0.0,
                    "error": str(e)
                }

        response_text = result.content
        self._record_metrics(agent_name, response_text, user_id, session_id, start_time)
        await self._save_assistant_message(user_id, session_id, agent_name, response_text)

        return {
            "agent": agent_name,
            "response": response_text,
            "confidence": classification.confidence,
            "reasoning": classification.reasoning
        }

    async def _fan_out(
        self, agents, classification: ClassifierResult,
        user_input: str, user_id: str, session_id: str, start_time: float
    ) -> Dict:
        """Fan-out: call multiple agents in parallel, then synthesize."""
        with tracer.start_as_current_span("supervisor.fan_out") as span:
            span.set_attribute("agent_count", len(agents))

            tasks = [
                self.agents[a.agent].process_request(
                    input_text=user_input,
                    user_id=user_id,
                    session_id=session_id,
                    chat_history=[],
                )
                for a in agents
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            ok: list[tuple[str, str]] = []
            failed: list[str] = []
            for a, r in zip(agents, results):
                if isinstance(r, Exception):
                    failed.append(a.agent)
                    logger.error("Fan-out agent failed", extra={
                        "agent_id": a.agent, "error": str(r)
                    })
                    error_counter.add(1, {"agent_id": a.agent, "error_type": "fan_out"})
                else:
                    ok.append((a.agent, r.content))

            final_response = await synthesizer.synthesize(user_input, ok, failed)

            # Fan-out metrics
            fanout_calls.add(1)
            fanout_agents_consulted.record(len(agents))
            if failed:
                fanout_agents_failed.add(len(failed))

            # Record metrics for primary agent
            primary = agents[0].agent
            self._record_metrics(primary, final_response, user_id, session_id, start_time)
            await self._save_assistant_message(user_id, session_id, primary, final_response)

            return {
                "agent": "supervisor",
                "response": final_response,
                "agents_consulted": [a.agent for a in agents],
                "agents_failed": failed,
                "confidence": min(a.confidence for a in agents),
            }

    def _record_metrics(self, agent_name: str, response_text: str, user_id: str, session_id: str, start_time: float):
        duration_ms = (time.time() - start_time) * 1000
        agent_attrs = {"agent_id": agent_name}
        request_counter.add(1, agent_attrs)
        request_duration.record(duration_ms, agent_attrs)
        log_response("supervisor", user_id, session_id, len(response_text), duration_ms)

    async def _save_assistant_message(self, user_id: str, session_id: str, agent_name: str, content: str):
        assistant_message = ConversationMessage(
            role="assistant",
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_name
        )
        await storage.save_chat_message(user_id, session_id, agent_name, assistant_message)

    async def close(self):
        """No-op — agents are in-process, no connections to close."""
        pass


# Initialize registry and supervisor
registry = AgentRegistry()
registry.discover()
supervisor = SupervisorAgent(registry)
