# Tasks: Bedrock Cost & Model Tiering

> Depends on 06 (bedrock async). Model per role comes from config (19/22).

- [x] T1: Model resolver in `bedrock.py` — role (`classifier`/`agent`/`synthesis`) → model from config
- [x] T2: Classifier now uses the `classifier` model (Haiku) (depends on: T1)
- [x] T3: Re-enable prompt caching (`cache_control: ephemeral`) + validate support at startup, degrade if unavailable (depends on: T1)
- [x] T4: Token budget per session (configurable hard cap) + cut with clear message
- [x] T5: History truncation by tokens (not by message count) (depends on: T4)
- [x] T6: Emit cost/token metric (input/output per agent+model) — hook for spec 10
- [x] T7 (test-author DIFFERENT from author): pytest ≥90% — model per role, caching in body, budget cuts, truncation (depends on: T1–T5)
- [x] T8: Independent review (`code-review` + `finops`): correct tiering, effective caching, hard budget cap (depends on: T7)

## Status (2026-07-02)
Implemented via pipeline (dev → test-author → code-review + finops), 100% coverage on `model_tier.py` + `token_budget.py` (62 tests). Remediations applied:
- **finops**: Haiku pricing corrected for 4.5 (`$1/$5/$0.10`); `compute_cost` now prices cache-WRITE at 1.25x (in addition to cache-read at 0.1x); `cache_creation_input_tokens` passed from `bedrock.py`.
- **code-review**: budget tracker WIRED into the flow (was inert) — `record_usage` on each Bedrock call (`bedrock._invoke_sync`), `check_budget` (hard cut) in `SupervisorAgent.process_request`. Enricher kept at Sonnet (tier `synthesis`) with Opus documented as a promotion trigger (design.md), not a silent deferral.
- Model IDs per role: `bedrock_classifier_model_id` (Haiku), `bedrock_agent_model_id`/`bedrock_synthesis_model_id` (Sonnet) in `config.py`. ⚠️ operator must confirm the exact Haiku inference-profile in the account.

## Suggested order
T1→T2/T3; T4→T5; T6; T7→T8.

## Notes
- This isn't "over-saving": it's not wasting on routing + cutting latency (Haiku) without losing response quality (Sonnet).
- Cross-region failover out of scope (over-engineering pre-MVP).
- Verification pipeline (`verification-independence.md`): T1–T6 author; T7 test-author; T8 code-review.
