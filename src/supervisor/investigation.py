"""RCA investigation orchestrator: fan-out evidence collection + synthesis."""
import asyncio
import json
import os
import time as time_mod

from otel_helper import get_tracer

from src.core.bedrock import bedrock
from src.core.investigation import (
    Evidence, RCAResult, InvestigationState,
    build_timeline, correlate,
)
from src.core.kb.rag import inject_similar_cases
from src.core.logger import logger, log_request, log_response
from src.core.metrics import (
    investigation_started, investigation_completed,
    investigation_duration, investigation_evidence_count,
    investigation_rounds,
)

tracer = get_tracer(__name__)

# Cost cap (configurable via env)
MAX_AGENTS_PER_INVESTIGATION = int(os.getenv("RCA_MAX_AGENTS", "5"))

RCA_SYNTHESIZER_PROMPT = """You are an RCA (Root Cause Analysis) synthesizer.

You receive:
1. A symptom description
2. Evidence collected from N specialist agents (each with timestamp, strength, summary)
3. A timeline of temporal events

Produce a structured RCA result as JSON:
{
  "hypothesis": "single-sentence root cause hypothesis",
  "reasoning": "how the evidence supports the hypothesis",
  "contradicting_evidence_indices": [list of indices in evidence array that CONTRADICT the hypothesis],
  "prevention": ["actionable item 1", "actionable item 2", ...]
}

RULES:
- If evidence is insufficient, say so in hypothesis (e.g., "Insufficient evidence; needs X to confirm")
- Identify contradicting evidence honestly (don't ignore it)
- Prevention items should be concrete: alert/test/guardrail/runbook
- Output ONLY valid JSON, no preamble."""


async def run_investigation(
    symptom: str,
    agents: dict,  # {name: GenericAgent}
    user_id: str = "investigator",
    session_id: str = "",
    relevant_agent_names: list[str] | None = None,
) -> RCAResult:
    """Run a single-round RCA investigation.

    1. Pick agents (config-driven via capabilities, fallback: all)
    2. Fan-out evidence collection (parallel)
    3. Parse responses into Evidence objects
    4. Build timeline + correlate
    5. Synthesize RCA via Bedrock
    """
    state = InvestigationState(symptom=symptom)
    log_request("investigation", user_id, session_id, symptom)
    investigation_started.add(1)
    t0 = time_mod.time()

    with tracer.start_as_current_span("investigation.run") as span:
        span.set_attribute("investigation.id", state.id)
        span.set_attribute("symptom", symptom[:200])

        # Pick agents
        chosen = relevant_agent_names or list(agents.keys())
        chosen = chosen[:MAX_AGENTS_PER_INVESTIGATION]
        state.agents_consulted = chosen

        # Fan-out evidence collection
        evidence_query = (
            "Collect evidence for the following symptom. Return your findings as a JSON list "
            "of evidence items, each with fields: signal_type (metric|log|trace|event|deploy|infra), "
            "timestamp (ISO8601, '' if not applicable), strength (forte|media|fraca), summary. "
            f"Symptom: {symptom}"
        )

        tasks = [
            agents[name].process_request(
                input_text=evidence_query,
                user_id=user_id,
                session_id=f"{session_id}-inv-{state.id[:8]}",
                chat_history=[],
            )
            for name in chosen
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Parse responses into Evidence
        for name, result in zip(chosen, results):
            if isinstance(result, Exception):
                state.agents_failed.append(name)
                logger.warning("Evidence collection failed", extra={
                    "agent": name, "error": str(result)
                })
                continue
            evidence_items = _parse_evidence(result.content, source_agent=name)
            state.evidence.extend(evidence_items)

        state.rounds_completed = 1

        # Build timeline + correlate
        timeline = build_timeline(state.evidence)

        # Synthesize
        rca = await _synthesize_rca(symptom, state.evidence, timeline)

        log_response("investigation", user_id, session_id, len(rca.hypothesis), 0.0)

        # Investigation completion metrics
        duration_ms = (time_mod.time() - t0) * 1000
        investigation_duration.record(duration_ms, {"confidence": rca.confidence})
        investigation_completed.add(1, {"confidence": rca.confidence})
        investigation_evidence_count.record(len(rca.evidence))
        investigation_rounds.record(state.rounds_completed)

        return rca


def _parse_evidence(text: str, source_agent: str) -> list[Evidence]:
    """Best-effort extraction of structured evidence from agent response.

    Tries JSON first; if fails, falls back to wrapping the whole response as 1 fraca evidence.
    """
    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            items = json.loads(text[start:end + 1])
            return [
                Evidence(
                    source_agent=source_agent,
                    signal_type=item.get("signal_type", "unknown"),
                    timestamp=item.get("timestamp", ""),
                    strength=item.get("strength", "fraca"),
                    summary=item.get("summary", ""),
                )
                for item in items if isinstance(item, dict)
            ]
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: whole response as 1 evidence
    return [Evidence(
        source_agent=source_agent,
        signal_type="unknown",
        timestamp="",
        strength="fraca",
        summary=text[:500],
    )]


async def _synthesize_rca(symptom: str, evidence: list[Evidence], timeline: list[Evidence]) -> RCAResult:
    """Single Bedrock call to fuse evidence into RCAResult."""
    rag_block = await inject_similar_cases(symptom)

    evidence_block = "\n".join(
        f"[{i}] {e.source_agent} | {e.signal_type} | {e.strength} | {e.timestamp} | {e.summary}"
        for i, e in enumerate(evidence)
    ) or "(no evidence collected)"

    timeline_block = "\n".join(
        f"{e.timestamp} | {e.source_agent} | {e.summary}"
        + (" *** CAUSAL CANDIDATE ***" if e.is_causal_candidate else "")
        for e in timeline
    ) or "(no temporal evidence)"

    user_msg = f"""<symptom>
{symptom}
</symptom>

{rag_block}<evidence>
{evidence_block}
</evidence>

<timeline>
{timeline_block}
</timeline>

Produce the RCA JSON."""

    response = await bedrock.invoke(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=RCA_SYNTHESIZER_PROMPT,
        temperature=0.2,
    )

    # Parse JSON
    contradicting_indices: list[int] = []
    try:
        start = response.find("{")
        end = response.rfind("}")
        parsed = json.loads(response[start:end + 1]) if start >= 0 else {}
        hypothesis = parsed.get("hypothesis", "Insufficient evidence to form hypothesis")
        contradicting_indices = parsed.get("contradicting_evidence_indices", []) or []
        prevention = parsed.get("prevention", []) or []
    except (json.JSONDecodeError, ValueError):
        hypothesis = response[:500]
        prevention = []

    contradicting = [evidence[i] for i in contradicting_indices if 0 <= i < len(evidence)]
    confidence = correlate(evidence, contradicting)

    return RCAResult(
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=evidence,
        timeline=timeline,
        contradicting=contradicting,
        prevention=prevention,
    )
