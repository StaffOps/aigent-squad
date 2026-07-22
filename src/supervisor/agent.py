import asyncio
from typing import Dict
import time
from datetime import datetime, timezone
from opentelemetry import trace
from src.core.classifier import Classifier, ClassifierResult, AgentMatch
from src.core.guardrail import GuardrailBlockedError
from src.core.input_scanner import InputScanner
from src.core.state_store import storage, ConversationMessage
from src.core.logger import logger, log_request, log_response, log_error
from src.core.metrics import request_counter, error_counter, request_duration, fanout_calls, fanout_agents_consulted, fanout_agents_failed
from src.core.registry import AgentRegistry
from src.core.generic_agent import GenericAgent
from src.core.adapters import create_adapters
from src.core.skills import SkillRegistry
from src.core.triage import should_investigate
from src.core.token_budget import budget_tracker, TokenBudgetExceeded
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
            adapters = create_adapters(
                config.datasources,
                cache_ttl=config.cache.ttl,
                cache_namespace=config.cache.namespace,
            )
            prompt = registry.get_prompt(config.name)
            self.agents[config.name] = GenericAgent(config, prompt, adapters, skill_registry=self.skill_registry)

        logger.info(f"Supervisor initialized with {len(self.agents)} agents: {list(self.agents.keys())}")

    async def process_request(
        self,
        user_input: str,
        user_id: str,
        session_id: str,
        mode: str = "query",
        force_agent: str | None = None,
    ) -> Dict:
        """Process user request with intelligent routing and optional fan-out.

        When ``force_agent`` is set (and known), the classifier is bypassed and
        the request is routed directly to that specialist — used by the OpenAI
        bridge's per-agent models (spec 29).
        """

        start_time = time.time()

        with tracer.start_as_current_span("supervisor.process_request") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)
            span.set_attribute("input_length", len(user_input))

            log_request("supervisor", user_id, session_id, user_input)

            try:
                # Token budget hard cap (spec 11 T4): refuse before spending if
                # the session is already over budget. Clear message, no invoke.
                try:
                    budget_tracker.check_budget(session_id)
                except TokenBudgetExceeded as budget_err:
                    logger.warning("Session token budget exceeded", extra={
                        "session_id": session_id,
                        "used": budget_err.used,
                        "limit": budget_err.limit,
                    })
                    return {
                        "agent": "supervisor",
                        "response": (
                            "This session has reached its token budget. "
                            "Please start a new session to continue."
                        ),
                        "confidence": 0.0,
                        "error": "token_budget_exceeded",
                    }

                # L2 Input Scanner at the trust-boundary entry (spec 14 finding B):
                # normalize (homoglyph fold, zero-width strip) + cheap reject
                # BEFORE any routing decision — forced agent, investigation, or
                # classify. Homoglyph obfuscation empirically evaded the
                # classifier's L1 when scanned only at the worker. Fail-closed:
                # GuardrailBlockedError propagates → 403. The worker-side scan
                # in generic_agent stays (no layer trusts the previous one).
                user_input = InputScanner().scan(
                    user_input,
                    agent_id="supervisor",
                    user_id=user_id,
                    session_id=session_id,
                )

                # G-6 fix: single ingress INPUT guardrail — guard the GENUINE
                # end-user question ONCE here at the trust boundary, before any
                # routing/classify/agent call. This replaces the per-stage INPUT
                # scans that previously ran inside bedrock.invoke()/converse()
                # on ASSEMBLED prompts (classifier agent-catalog + agent
                # instructions), which false-positived PROMPT_ATTACK because the
                # squad's own framing contains verbs like "manage/delete/execute".
                #
                # Root cause (proven with apply-guardrail): the bare user phrase
                # passes cleanly; a real injection still blocks. The per-stage
                # calls were scanning trusted framing as if it were user input.
                #
                # Security invariant: fail-closed. A real injection in the user
                # text raises GuardrailBlockedError → 403. The OUTPUT guardrail,
                # tool-args guardrail (B3), tool-result guardrail (B3), and
                # guardContent server-side tagging all remain active downstream.
                from src.core.guardrail import guardrail
                guardrail.apply(
                    user_input,
                    source="INPUT",
                    agent_id="ingress",
                    user_id=user_id,
                    session_id=session_id,
                )

                # Forced agent (OpenAI bridge per-agent model): bypass classifier.
                if force_agent and force_agent in self.agents:
                    direct = ClassifierResult(
                        agents=[AgentMatch(agent=force_agent, confidence=1.0)],
                        reasoning=f"forced to {force_agent}",
                    )
                    user_message = ConversationMessage(
                        role="user",
                        content=user_input,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        agent_id=force_agent,
                    )
                    await storage.save_chat_message(user_id, session_id, force_agent, user_message)
                    return await self._single_agent_call(
                        force_agent, direct, user_input, user_id, session_id, start_time
                    )

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
                        chat_history,
                        user_id=user_id,
                        session_id=session_id,
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

            except GuardrailBlockedError:
                # Fail-closed (spec 14): propagate so the entrypoint returns 403.
                # The audit log was already emitted inside the guardrail client.
                raise
            except Exception as e:
                log_error("supervisor", e, user_id=user_id, session_id=session_id)
                return {
                    "agent": "supervisor",
                    "response": "An unexpected error occurred. Please try again.",
                    "confidence": 0.0,
                    "error": str(e)
                }

    async def process_request_streaming(
        self,
        user_input: str,
        user_id: str,
        session_id: str,
        mode: str = "query",
        force_agent: str | None = None,
    ):
        """Streaming-aware process (Phase 3.5, S1 + G-4 auto-route).

        Returns an async generator of AgenticStepEvent if the request can be
        served by a single agentic agent. Covers both the forced-agent path
        (OpenAI bridge per-agent models) AND the auto-route path (G-4) when the
        classifier resolves to exactly one agentic agent.

        Returns None (fall back to non-streaming) for:
          - Multi-agent/fan-out classifications
          - Investigation/RCA mode
          - Resolved agent that is NOT agentic (has_agentic_tools() false)
          - Token budget exceeded
          - Unknown/unclassifiable input
        """
        from src.core.agentic_loop_streaming import StepRouting, run_agentic_loop_streaming

        start_time = time.time()

        with tracer.start_as_current_span("supervisor.process_request_streaming") as span:
            span.set_attribute("user_id", user_id)
            span.set_attribute("session_id", session_id)

            # Budget check
            try:
                budget_tracker.check_budget(session_id)
            except TokenBudgetExceeded:
                return None  # fall back to non-streaming for budget error

            # Input scanner
            user_input = InputScanner().scan(
                user_input,
                agent_id="supervisor",
                user_id=user_id,
                session_id=session_id,
            )

            # G-6 fix: single ingress INPUT guardrail for streaming path
            # (same logic as process_request — guard genuine user question once).
            from src.core.guardrail import guardrail
            guardrail.apply(
                user_input,
                source="INPUT",
                agent_id="ingress",
                user_id=user_id,
                session_id=session_id,
            )

            # ------------------------------------------------------------------
            # Path A: Forced agent (OpenAI bridge per-agent model)
            # ------------------------------------------------------------------
            if force_agent and force_agent in self.agents:
                agent = self.agents[force_agent]

                if not agent.has_agentic_tools():
                    return None  # legacy non-agentic agent → fall back

                # Save user message
                user_message = ConversationMessage(
                    role="user",
                    content=user_input,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    agent_id=force_agent,
                )
                await storage.save_chat_message(user_id, session_id, force_agent, user_message)

                # Get the streaming generator from the agent
                step_gen = await agent.process_request_streaming(
                    input_text=user_input,
                    user_id=user_id,
                    session_id=session_id,
                    chat_history=await storage.fetch_chat(user_id, session_id, force_agent),
                )
                return step_gen

            # ------------------------------------------------------------------
            # Path B: Auto-route (G-4) — classify and stream if single agentic
            # ------------------------------------------------------------------

            # Investigation mode → fall back (produces synthesized answer, not steps)
            force_investigate = mode == "investigate"
            if should_investigate(user_input, force=force_investigate):
                return None

            # Classify intent (reuses the same classifier as process_request)
            chat_history = await storage.fetch_all_chats(user_id, session_id)
            classification: ClassifierResult = await self.classifier.classify(
                user_input,
                chat_history,
                user_id=user_id,
                session_id=session_id,
            )

            # No match or unknown → fall back
            if not classification.agents or classification.selected_agent == "unknown":
                return None

            # Filter to known agents, cap at max_agents
            agents = [
                a for a in classification.agents[:self.max_agents]
                if a.agent in self.agents
            ]
            if not agents:
                return None

            # Multi-agent (fan-out) → fall back (needs synthesis, not streaming)
            if len(agents) > 1:
                return None

            # Single agent resolved — check if it's agentic
            resolved_name = agents[0].agent
            resolved_agent = self.agents[resolved_name]

            if not resolved_agent.has_agentic_tools():
                return None  # non-agentic agent → fall back to non-streaming path

            # B-14: use focused sub_query when available; fallback to raw input.
            agent_input = user_input
            sq = agents[0].sub_query
            if sq:
                agent_input = sq

            # Save user message tagged to the resolved agent
            user_message = ConversationMessage(
                role="user",
                content=user_input,
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_id=resolved_name,
            )
            await storage.save_chat_message(user_id, session_id, resolved_name, user_message)

            # Build a wrapper generator that emits StepRouting first, then the
            # agent's agentic loop steps.
            agent_step_gen = await resolved_agent.process_request_streaming(
                input_text=agent_input,
                user_id=user_id,
                session_id=session_id,
                chat_history=await storage.fetch_chat(user_id, session_id, resolved_name),
            )

            async def _routed_stream():
                """Yield a routing step, then proxy all agent loop steps."""
                yield StepRouting(
                    agent=resolved_name,
                    confidence=agents[0].confidence,
                    reasoning=classification.reasoning or "",
                )
                async for event in agent_step_gen:
                    yield event

            return _routed_stream()

    async def _single_agent_call(
        self, agent_name: str, classification: ClassifierResult,
        user_input: str, user_id: str, session_id: str, start_time: float
    ) -> Dict:
        """Fast-path: route to a single agent."""
        agent_history = await storage.fetch_chat(user_id, session_id, agent_name)
        agent = self.agents[agent_name]

        # B-14: use the focused sub_query when available; fallback to raw input.
        agent_input = user_input
        if classification.agents:
            sq = classification.agents[0].sub_query
            if sq:
                agent_input = sq

        with tracer.start_as_current_span("agent.process") as agent_span:
            agent_span.set_attribute("agent_id", agent_name)

            try:
                result = await agent.process_request(
                    input_text=agent_input,
                    user_id=user_id,
                    session_id=session_id,
                    chat_history=agent_history,
                )
            except GuardrailBlockedError:
                # Fail-closed (spec 14): propagate to the entrypoint (→ 403).
                raise
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
                    # B-14: each agent gets its focused sub_query; fall back to
                    # raw user_input when sub_query is absent/empty.
                    input_text=a.sub_query if a.sub_query else user_input,
                    user_id=user_id,
                    session_id=session_id,
                    chat_history=[],
                )
                for a in agents
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Fail-closed (spec 14): a GuardrailBlockedError from ANY agent is a
            # security refusal, not a transient failure to route around. Refuse
            # the whole request (→ 403) rather than synthesizing partial results.
            for r in results:
                if isinstance(r, GuardrailBlockedError):
                    raise r

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

            final_response = await synthesizer.synthesize(
                user_input, ok, failed, user_id=user_id, session_id=session_id,
            )

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
