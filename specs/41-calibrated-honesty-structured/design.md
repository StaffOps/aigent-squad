---
spec: 41-calibrated-honesty-structured
status: design-only
completed: null
superseded_by: null
depends_on: ["35-quality-eval-harness"]
deferred: []
---

# Design: Structured calibrated honesty (B-16 Phase-2)

## Architecture

```
generic_agent.answer()
  └─ NEW (M1): after the agentic loop, build effective_infra_data =
        concatenation of all toolResult content blocks from the Bedrock messages
        array (the agentic path has infra_data="" — the evidence lives in the
        loop's tool results, so without this the scan finds nothing → no-op).
  └─ (existing) ResponseQualityGuard().scan(response, effective_infra_data, agent_id)
        ├─ _find_ungrounded_resource_ids(response, infra_data)   → [ids]   (exists)
        ├─ _check_numeric_groundedness(response, infra_data)     → [nums]  (exists, metric-only today)
        └─ NEW: assemble QualityAssessment(confidence, unverified_claims)  ← return it
  └─ NEW: emit aigent.quality.confidence{level} + aigent.quality.unverified_claims_per_response
  └─ NEW: attach assessment to the supervisor result → openai_compat renders x_aigent.quality
        (NON-STREAMING responses only — the streaming SSE format has no slot for it; Phase-3 trigger below)
```

No new evidence collection, no extra LLM call: the scan already computes the raw
ungrounded signals; Phase-2 only **returns + shapes + surfaces** them.

## Components
| Component | Responsibility |
|-----------|----------------|
| `QualityAssessment` (new dataclass in `response_quality.py`) | `confidence: str`, `unverified_claims: list[str]` |
| `ResponseQualityGuard.scan` (changed) | now RETURNS `QualityAssessment` (was `None`); emission of the two new metrics stays here (single site) |
| `generic_agent` (changed) | pass the assessment up through the answer result |
| `openai_compat.ChatCompletionResponse` (changed) | optional `x_aigent: Optional[dict]` top-level field |
| `metrics.py` (changed) | `quality_confidence` counter (`aigent.quality.confidence{level}`) + `quality_unverified_claims` histogram (`aigent.quality.unverified_claims_per_response`, buckets [0,1,2,3,5,10,20], unit `{claims}`) |

## Rationale

### Decision 1: confidence is derived DETERMINISTICALLY from groundedness, not from the model's self-report
**Choice**: compute `confidence` from the count of ungrounded claims the scan finds, not by parsing the model's free-text "confiança: alta/média/baixa" line.

**Justificativa, em ordem de força**:
1. A model self-reporting "high confidence" while hallucinating a metric is the exact failure "calibrated honesty" exists to catch — trusting its self-report re-introduces the bug.
2. Deterministic derivation is testable + stable; free-text parsing breaks the moment the model rephrases the line (and it's bilingual PT/EN).
3. The groundedness scan already runs at answer time — zero extra cost/latency.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| Groundedness only catches numeric/resource-ID ungroundedness, not semantic hallucination | It's the strongest cheap signal we have; semantic checking is an LLM-judge job (spec 35 eval harness), not answer-time |
| Thresholds (0 / 1–2 / ≥3) are heuristic | Documented + tested + easy to tune; a metric lets us calibrate against real traffic |

**Quando estaria errado**: if consumers explicitly need the model's *stated* confidence (not the evidence-based one) — then add a second field rather than replacing this one.

**Weighting refinement (harness M-open-1, adopted)**: a flat count treats a
hallucinated resource ID (`i-0abc…`) the same as an ungrounded `$12.34`. Since acting
on a non-existent resource is the most dangerous false-positive, **any ungrounded
resource ID forces `confidence: low` immediately**; ungrounded *numeric* claims use the
count heuristic (0→high, 1–2→medium, ≥3→low). Documented + tested.

### Decision 2: structured fields live in a NAMESPACED `x_aigent.quality` extension, non-streaming only
**Choice**: add `x_aigent: {quality: {confidence, unverified_claims}}` at the top level of `ChatCompletionResponse` (nested `quality` key so future extensions don't proliferate top-level fields); leave `choices[].message.content` byte-identical (keeps the Phase-1 human line). **Scope: non-streaming (`stream: false`) only** — the streaming SSE chunk format has no slot for a trailing structured object. **Phase-3 trigger**: when a consumer needs it on the streaming path, evaluate a final SSE event or a `/v1/assessments/{id}` side channel rather than forcing it into delta chunks.

**Justificativa**:
1. The OpenAI contract is an external surface (LibreChat + any OpenAI client) — mutating `message` or adding non-namespaced fields risks strict clients; a namespaced top-level key is ignored by clients that don't know it.
2. Humans still get the readable line in `content`; machines get the structured mirror — no duplication of source of truth (both derive from the same scan).
3. Additive + optional (`None` when the guard is disabled) → backward compatible.

**Trade-offs aceitos**:
| Custo | Realidade |
|-------|-----------|
| `x_aigent` is non-standard | Namespaced + optional; documented in `docs/LIBRECHAT.md` |

**Alternativas descartadas**: (a) append a JSON block to `content` — pollutes the human answer + double source of truth; (b) metrics-only — loses per-answer detail for consumers; (c) mutate `message` schema — breaks strict OpenAI clients.

## Invariants
- 100% read-only; no new external calls; no extra LLM invocation.
- Non-blocking: any exception in the assessment path is swallowed (answer still returns) — matches the existing groundedness guard.
- Deterministic given (response, infra_data).
- Bounded cardinality: `confidence.level ∈ {high, medium, low}` (3); `unverified_claims` capped at N (e.g. 20) items.
- `content` unchanged (regression-locked by a byte-equality test).

## Dependências externas
| Serviço | Propósito |
|---------|-----------|
| (none new) | reuses the in-process groundedness scan + OTel meter |
