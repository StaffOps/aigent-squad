"""RCA investigation orchestrator: fan-out evidence collection + synthesis."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.core.generic_agent import GenericAgent
import asyncio
import json
import os
import time as time_mod

from otel_helper import get_tracer

from src.core.bedrock import bedrock
from src.core.guardrail import GuardrailBlockedError
from src.core.input_scanner import InputScanner
from src.core.investigation import (
    Evidence, RCAResult, InvestigationState,
    build_timeline, correlate, score_confidence,
    count_independent, validate_temporal_order,
)
from src.core.kb.rag import inject_similar_cases
from src.core.logger import logger, log_request, log_response
from src.core.metrics import (
    investigation_started, investigation_completed,
    investigation_duration, investigation_evidence_count,
    investigation_rounds, investigation_fanout_errors,
)

tracer = get_tracer(__name__)

# Cost cap (configurable via env)
MAX_AGENTS_PER_INVESTIGATION = int(os.getenv("RCA_MAX_AGENTS", "5"))

# Entry-stage L2 scanner (spec 14 Finding F): investigations can be triggered
# outside process_request (e.g. /alerts/incoming builds a symptom from
# attacker-influenceable Alertmanager labels), so the entry scanner is applied
# here as the single choke point for every caller. Idempotent on the
# already-scanned process_request path (defense-in-depth).
_scanner = InputScanner()

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
    agents: dict[str, "GenericAgent"],  # {name: GenericAgent}
    user_id: str = "investigator",
    session_id: str = "",
    relevant_agent_names: list[str] | None = None,
    model_id_override: str | None = None,
) -> RCAResult:
    """Run a single-round RCA investigation.

    1. Pick agents (config-driven via capabilities, fallback: all)
    2. Fan-out evidence collection (parallel)
    3. Parse responses into Evidence objects
    4. Build timeline + correlate
    5. Synthesize RCA via Bedrock
    """
    # Finding F: normalize + scan the symptom before it reaches any agent or the
    # RCA synthesizer. Fail-closed → GuardrailBlockedError propagates to 403.
    symptom = _scanner.scan(
        symptom, agent_id="investigation", user_id=user_id, session_id=session_id,
    )
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
                # Derived session_id keeps evidence-collection audit/log entries
                # isolated from the parent chat session (log correlation, not
                # DynamoDB history — GenericAgent never persists here). Finding
                # E2: budget_session_id points back at the REAL session so this
                # spend still counts against the cap check_budget() enforces at
                # the supervisor entrypoint, instead of a fresh per-investigation
                # bucket nothing ever reads.
                session_id=f"{session_id}-inv-{state.id[:8]}",
                chat_history=[],
                budget_session_id=session_id,
                model_id_override=model_id_override,
            )
            for name in chosen
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Fail-closed (spec 14): a guardrail refusal on the (shared) symptom is a
        # security decision — propagate it (→ 403) instead of producing an empty
        # RCA from zero evidence.
        for result in results:
            if isinstance(result, GuardrailBlockedError):
                raise result

        # Parse responses into Evidence
        for name, result in zip(chosen, results):
            if isinstance(result, Exception):
                state.agents_failed.append(name)
                logger.warning("Evidence collection failed", extra={
                    "agent": name, "error": str(result)
                })
                # FIX 3: emit per-agent error counter for investigation fan-out failures
                investigation_fanout_errors.add(1, {"agent_id": name})
                continue
            evidence_items = _parse_evidence(
                result.content,  # type: ignore[union-attr]  # narrowed by isinstance+continue above
                source_agent=name,
            )
            state.evidence.extend(evidence_items)

        state.rounds_completed = 1

        # Build timeline + correlate
        timeline = build_timeline(state.evidence)

        # Synthesize (Finding E1: attribute + budget the synthesis Sonnet call)
        rca = await _synthesize_rca(
            symptom, state.evidence, timeline, user_id=user_id, session_id=session_id,
        )

        log_response("investigation", user_id, session_id, len(rca.hypothesis), 0.0)

        # Investigation completion metrics
        duration_ms = (time_mod.time() - t0) * 1000
        # rca.confidence is ALREADY a bounded string (alta|media|baixa) — safe, low-cardinality
        # label as-is. (The earlier "bucketize float" fix was based on a wrong assumption:
        # confidence is not a float here. English-normalization is a tracked backlog item.)
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


async def _synthesize_rca(
    symptom: str,
    evidence: list[Evidence],
    timeline: list[Evidence],
    user_id: str = "unknown",
    session_id: str = "",
) -> RCAResult:
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
        role="synthesis",  # spec 11: uses Sonnet (synthesis tier)
        agent_id="rca-synthesizer",
        user_id=user_id,
        session_id=session_id,
        # G-6: ingress already guarded the user question; this prompt is our
        # framing + trusted evidence/timeline — do not re-scan (FP source).
        skip_input_guardrail=True,
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

    # T13: LLM confidence as ceiling — the evidence model computes the maximum
    # achievable confidence; the LLM (via its contradicting_indices) can only lower.
    confidence_level, confidence_track = score_confidence(evidence)
    independent_count = count_independent(evidence)
    temporal_violations = validate_temporal_order(evidence)

    # Legacy correlate for backward-compat metrics (alta/media/baixa)
    confidence = correlate(evidence, contradicting)

    # Ceiling enforcement: if the LLM identified contradictions that lower
    # confidence below the model's ceiling, the lower value wins.
    # Map: alta=HIGH, media=MEDIUM, baixa=LOW
    _LEVEL_RANK = {"baixa": 0, "media": 1, "alta": 2}
    _MODEL_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    _RANK_TO_LEGACY = {0: "baixa", 1: "media", 2: "alta"}

    model_rank = _MODEL_RANK.get(confidence_level, 0)
    llm_rank = _LEVEL_RANK.get(confidence, 0)

    # Ceiling: cap LLM at model level (LLM can lower, never raise)
    effective_rank = min(model_rank, llm_rank)
    confidence = _RANK_TO_LEGACY[effective_rank]

    if effective_rank < llm_rank:
        logger.info(
            "confidence_ceiling_applied",
            extra={
                "event": "confidence_ceiling",
                "model_level": confidence_level,
                "llm_level": _RANK_TO_LEGACY[llm_rank],
                "effective": confidence,
                "track": confidence_track,
            },
        )

    return RCAResult(
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=evidence,
        timeline=timeline,
        contradicting=contradicting,
        prevention=prevention,
        confidence_level=confidence_level,
        confidence_track=confidence_track,
        independent_signal_count=independent_count,
        temporal_violations=temporal_violations,
    )
