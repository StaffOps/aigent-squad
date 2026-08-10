---
spec: 41-calibrated-honesty-structured
status: done-with-deferrals
completed: "2026-08-08"
superseded_by: null
depends_on: ["35-quality-eval-harness"]
deferred: ["T3 histogram bucket boundaries (default SDK buckets — explicit boundaries need a View in the otel_helper MeterProvider; not settable via opentelemetry-api 1.29.0 create_histogram)"]
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
        ├─ _find_defects(response)                → structural defects → BLOCK (raise)
        ├─ _find_ungrounded_resource_ids(...)      → [ids] → BLOCK (raise, existing guardrail)
        ├─ (non-blocking) _check_numeric_groundedness(...) → [nums]  (metric-only today)
        └─ NEW: assemble QualityAssessment(confidence, unverified_claims) from [nums] ← return it
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

**Weighting refinement (user decision 2026-07-24 — option A, safety-first)**: ungrounded
**resource IDs continue to BLOCK** (existing guardrail — a fabricated `i-…`/ARN the user
might act on is never surfaced, even tagged low-confidence). They are NOT downgraded to a
low-confidence assessment. The assessment therefore runs only on responses that pass the
block, and `confidence` derives purely from the ungrounded **numeric-claim** count
(0→high, 1–2→medium, ≥3→low). Numeric ungroundedness was already non-blocking (metric-only);
Phase-2 turns it into a structured signal. Documented + tested.

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

### Decision 3: `run_agentic_loop` returns `(text, messages)` (M1 side-effect)
**Choice**: change the internal `run_agentic_loop` signature from `-> str` to
`-> tuple[str, list[dict]]` so `generic_agent` can build `effective_infra_data` from the
loop's `toolResult` blocks (M1). Only prod caller (`generic_agent`) is updated to unpack;
the streaming variant (`run_agentic_loop_streaming`) is unchanged (its groundedness is a
separate follow-up). Internal function (not a public API); test call sites updated to the
tuple. Reasonable evolution — explicit return beats a hidden out-param or callback.

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
