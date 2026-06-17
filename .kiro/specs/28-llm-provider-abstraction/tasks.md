# Tasks: LLM Provider Abstraction

Status: **design only**. Do not implement without an explicit decision to prioritize.

## Phase 1 — Abstraction (safe refactor, valuable on its own)
- [ ] Task 1: Define `LLMProvider` (Protocol) + `LLMResult` (text + usage + model)
- [ ] Task 2: `LLMService` (common layer): move circuit breaker, retry/backoff,
      and emission of {model, agent_id, direction} metrics here
- [ ] Task 3: `BedrockProvider` = current `BedrockClient` refactored, identical
      behavior (same `test_bedrock.py` tests pass)
- [ ] Task 4: Callers (GenericAgent, Classifier) use `LLMService`; env-based selection
- [ ] Task 5: Validate cost-attribution (spec 27) + AIP ARN still work
- [ ] Task 6: Tests ≥90%; ADR-001 updated / new ADR

## Phase 2 — Second provider (separate decision)
- [ ] Task 7: Decide litellm (MIT dep) vs hand-written `AnthropicProvider` —
      evaluate maturity, overhead, license, impact on metrics
- [ ] Task 8: Implement the chosen provider as an `LLMProvider`
- [ ] Task 9: Parity tests (same contract, both providers)

## Notes
- **Clean-room**: implementation from scratch; litellm only as a dependency if chosen.
- **Not orchestration**: LLM transport only. LangGraph/Strands stay out (ADR-001).
- Trap: do not delegate cost-tracking to litellm (would lose the agent_id label).
