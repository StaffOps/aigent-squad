---
spec: 41-calibrated-honesty-structured
status: design-only
completed: null
superseded_by: null
depends_on: ["35-quality-eval-harness"]
deferred: []
---

# Feature: Structured calibrated honesty (B-16 Phase-2)

Phase-1 (shipped) is a **prompt instruction** (`<calibrated_honesty>`): the model is
told to separate VERIFIED from INFERRED facts and to end every answer with a
free-text confidence line + a list of claims it could not verify. That output is
**free text embedded in the message content** — not machine-readable, not validated
against what actually came back from tools.

Phase-2 makes it **structured and evidence-backed**: at answer time, derive a
machine-readable `confidence` level + `unverified_claims[]` **deterministically from
the existing groundedness check** (`ResponseQualityGuard`, already invoked in
`generic_agent.py`), expose them on the response contract + as metrics, and keep the
human-readable line intact.

## User Stories

WHEN an agent produces an answer THEN the system SHALL compute a structured
`confidence ∈ {high, medium, low}` derived from the groundedness scan of that answer
against the tool/datasource evidence collected this turn.

WHEN the groundedness scan finds ungrounded numeric claims or resource IDs (values
present in the answer but absent from `infra_data`) THEN the system SHALL list them
in `unverified_claims[]`.

WHEN a consumer reads the OpenAI-compatible response THEN it SHALL find the structured
assessment under a namespaced extension key (`x_aigent`) WITHOUT any change to the
standard `choices[].message.content` (Phase-1 free-text line stays).

WHEN an answer is produced THEN the system SHALL emit `aigent.response.confidence{level}`
(counter) and `aigent.response.unverified_claims` (histogram of count) for observability.

## Acceptance Criteria
- [ ] `ResponseQualityGuard.scan(...)` returns a structured `QualityAssessment{confidence, unverified_claims}` (today it returns nothing; the numeric-groundedness path already computes the raw signal).
- [ ] `confidence` derivation is **deterministic** (no extra LLM call): `high` = 0 ungrounded claims, `medium` = 1–2, `low` = ≥3 (thresholds documented + tested).
- [ ] `unverified_claims[]` = the ungrounded numeric claims + ungrounded resource IDs found by the scan (deduplicated, bounded to a max N to avoid unbounded payloads).
- [ ] OpenAI response carries `x_aigent: {confidence, unverified_claims}` (additive, namespaced); standard OpenAI clients that ignore unknown fields are unaffected; `content` is byte-identical to today.
- [ ] Two new metrics emitted, bounded cardinality (`level` = 3 values); non-blocking (a scan error never fails the answer).
- [ ] `response_quality_enabled=false` disables the whole path (assessment absent, no metrics) — feature-flagged like the existing guard.
- [ ] ≥90% coverage on the new/changed code; tests authored independently (verification-independence).

## Out of scope
- Changing the Phase-1 prompt instruction (kept as-is — human-readable line stays).
- **Blocking** or altering an answer based on low confidence (signal-only, like the existing groundedness metric — see `response_quality.py` module comment).
- Parsing the model's own free-text confidence line (deterministic groundedness is the source of truth; model self-report is explicitly NOT trusted — that is the whole point of "calibrated").
- Cross-turn / conversation-level confidence aggregation.
- Structured confidence for the RCA/investigation path (already has `RCAResult.confidence`).
