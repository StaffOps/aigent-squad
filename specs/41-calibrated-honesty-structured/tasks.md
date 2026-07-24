---
spec: 41-calibrated-honesty-structured
status: design-only
completed: null
superseded_by: null
depends_on: ["35-quality-eval-harness"]
deferred: []
---

# Tasks: Structured calibrated honesty (B-16 Phase-2)

- [ ] T1: Add `QualityAssessment` dataclass (`confidence: str`, `unverified_claims: list[str]`) to `response_quality.py`.
- [ ] T2: Change `ResponseQualityGuard.scan(...)` to assemble + RETURN a `QualityAssessment` — `confidence` = **`low` if ANY ungrounded resource ID**, else from the ungrounded-numeric count (0→high, 1–2→medium, ≥3→low); `unverified_claims` = deduped ungrounded numeric claims + resource IDs, capped at 20. (depends on: T1)
- [ ] **T2b (M1 — prerequisite, fatal without it):** build `effective_infra_data` = concatenation of the agentic loop's `toolResult` content blocks (the agentic path has `infra_data=""`; without this the scan finds nothing → always `high`, a no-op that would falsely bless hallucinated answers). Pass it to `scan`. (depends on: T2)
- [ ] T3: Add `quality_confidence` counter (`aigent.quality.confidence{level}`) + `quality_unverified_claims` histogram (`aigent.quality.unverified_claims_per_response`, buckets [0,1,2,3,5,10,20], unit `{claims}`) to `metrics.py`; emit from `scan` (single site, non-blocking). (depends on: T2)
- [ ] T4: Thread the assessment from `generic_agent` up through the supervisor answer result (new optional field on the internal result contract). (depends on: T2, T2b)
- [ ] T5: Add optional `x_aigent: Optional[dict]` to `ChatCompletionResponse` (`openai_compat.py`), nested `x_aigent.quality`; populate from the assessment; **NON-STREAMING responses only**; `content` unchanged. (depends on: T4)
- [ ] T6: Feature-flag: whole path gated on `response_quality_enabled` (assessment `None` + no metrics when off).
- [ ] T7 (independent author): tests — deterministic confidence thresholds, unverified_claims assembly + dedup + cap, x_aigent presence/shape, content byte-equality regression, flag-off path, non-blocking on scan error. ≥90% coverage. (verification-independence: different session than the implementer)
- [ ] T8: code-review (harness) + coverage gate ≥90%.
- [ ] T9: docs — `docs/LIBRECHAT.md` (document `x_aigent`), `docs/METRICS.md` + site (2 new metrics), CHANGES, spec status → done.

## Phase-1 status (what already exists — do NOT rebuild)
| Piece | State |
|-------|-------|
| `<calibrated_honesty>` prompt instruction | ✅ shipped (Phase-1, `settings.calibrated_honesty_instruction`) |
| `ResponseQualityGuard.scan` + `_find_ungrounded_resource_ids` + `_check_numeric_groundedness` | ✅ exists (scan returns None today; numeric path is metric-only) |
| `aigent.response_quality.numeric_claims_ungrounded` metric | ✅ exists |
| Structured `confidence` + `unverified_claims[]` return + contract surfacing | ⬜ this spec |
