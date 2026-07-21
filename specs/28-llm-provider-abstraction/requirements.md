---
spec: 28-llm-provider-abstraction
status: design-only
completed: null
superseded_by: null
depends_on: []
deferred: []
---

# Feature: LLM Provider Abstraction (multi-provider layer)

**Spec**: `28-llm-provider-abstraction`
**Status**: 📝 design only — do not implement without an explicit decision
**Depends on**: `ADR-001` (reopens the "Bedrock-direct, no framework" decision)
**Related**: `27-bedrock-cost-attribution`, `11-bedrock-resilience-cost`,
`steering/efficiency-cost.md`

---

## Objective

Allow AIgent-squad to use **multiple LLM providers** (Bedrock today; Anthropic
direct, OpenAI, Gemini in the future) behind a single interface, without
rewriting the agents or losing what we already built (circuit breaker, retry,
per-agent cost metrics, AIP cost-attribution).

Today `src/core/bedrock.py::BedrockClient` is the only implementation, coupled to
boto3 + the Anthropic-on-Bedrock format. Callers (GenericAgent, Classifier) call
`bedrock.invoke(...)` directly.

## ⚠️ Licensing (clean-room — MANDATORY)

This abstraction may be inspired by patterns from studied open-source projects
(HolmesGPT uses `litellm`), BUT:
- **DO NOT copy code** from any third-party repo (Apache-2.0/MIT/etc.).
  Copyright protects expression, not the idea. Implementation is **from scratch**.
- If `litellm` is adopted, it is a **declared dependency** (via package manager,
  license respected at the dependency level) — not a source copy.
- Any new dependency has its license verified and declared before adoption.

## User Stories

WHEN an agent invokes the LLM THEN it SHALL use a single, provider-agnostic
`LLMProvider` interface, with the same signature as today
(`messages`, `system_prompt`, `max_tokens`, `temperature`, `agent_id`).

WHEN the provider is switched (via config/env) THEN the agents SHALL NOT require
code changes.

WHEN any provider is used THEN the circuit breaker, retry, and **token/cost
metrics labeled by `agent_id` + `model`** SHALL keep working (preserve spec 27).

WHEN the provider is Bedrock with an Application Inference Profile THEN
cost-attribution via AIP SHALL keep working (the AIP ARN is passed as the model
identifier).

## Acceptance Criteria

- [ ] `LLMProvider` interface (Protocol) with `invoke(...)` — current signature.
- [ ] `BedrockProvider` = refactor of the current `BedrockClient`, **identical
      behavior** (same tests pass with no change in expectations).
- [ ] Circuit breaker, retry, and metrics live in the **common layer** (above the
      provider), not duplicated per implementation.
- [ ] Provider selection via env var (`LLM_PROVIDER=bedrock|...`).
- [ ] The second provider is a separate decision (litellm vs hand-written — see design).
- [ ] Cost-attribution (spec 27) validated with the selected provider.
- [ ] ≥90% coverage on new code; `BedrockProvider` tests = the current ones.
- [ ] ADR-001 updated (or a new ADR) recording the change and the trade-off.

## Out of scope

- Implementing N providers at once — start with the abstraction + Bedrock; the
  second provider is a separate delivery.
- Agent orchestration via a framework (LangGraph/Strands) — still rejected by
  ADR-001; this spec is only **LLM transport**, not orchestration.
