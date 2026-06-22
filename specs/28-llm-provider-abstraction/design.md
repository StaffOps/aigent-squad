# Design: LLM Provider Abstraction

## Architecture

```
        GenericAgent / Classifier
                  │  invoke(messages, system_prompt, ..., agent_id)
                  ▼
        ┌─────────────────────────────┐
        │  LLMService (common layer)   │  ← circuit breaker, retry/backoff,
        │                             │     token/cost metrics {model,agent_id},
        │                             │     prompt-caching policy (spec 11)
        └──────────────┬──────────────┘
                       │  delegates transport to
            ┌──────────▼───────────┐
            │   LLMProvider (Protocol) │
            └──────────┬───────────┘
        ┌──────────────┼───────────────┐
        ▼              ▼               ▼
 BedrockProvider   (future)        (future)
 (boto3, today)    LiteLLMProvider  AnthropicProvider
```

**Principle**: what is **cross-cutting** (resilience, metrics, cost) lives in
`LLMService` — ONCE, for all providers. The `LLMProvider` only does the
**transport** (build request, call, parse response → text + usage).

## Interface (draft)

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[dict],
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        model: str,            # model id OR the AIP ARN (Bedrock)
    ) -> LLMResult: ...        # text + usage(input/output tokens) + actual model
```

`LLMResult` carries `input_tokens`/`output_tokens`/`model` so `LLMService` can
emit the metrics (preserves spec 27). `agent_id` is a label applied by
`LLMService`, not the provider's responsibility.

## Rationale (decisions)

### Decision 1: our own abstraction with pluggable providers (not litellm in the core)

**Choice**: introduce our `LLMProvider` (Protocol); `litellm`, if adopted, is
ONE implementation underneath — not the replacement for `BedrockClient`.

**Justification, in order of strength**:
1. **Preserves what we built**: circuit breaker, retry, and — critically —
   per-`agent_id` cost metrics (spec 27) and AIP cost-attribution. If litellm
   became the core, we'd have to re-wire that into its callbacks (it has its own
   cost-tracking system, different from ours).
2. **Decouples without committing to a lib**: the interface holds even if we
   never adopt litellm (we could write `AnthropicProvider` by hand).
3. **Safe refactor**: `BedrockProvider` = current code moved, identical
   behavior, same tests.

**Accepted trade-offs**:
| Cost | Reality |
|------|---------|
| One more layer (LLMService + Provider) | Small; isolates transport from policy |
| We don't "get litellm for free" in the core | On purpose — the core is ours |

**When it would be wrong**: if maintaining multiple hand-written providers grows
a lot, litellm-in-the-core starts to pay off (but then re-wiring metrics
consciously).

### Decision 2: litellm is a candidate, not a prerequisite

**Choice**: the abstraction comes first (Decision 1). litellm vs a hand-written
client is the SECOND implementation's decision, separate.

**Justification**: step 1 (extract the interface) already delivers decoupling and
is low risk. Choosing the 2nd implementation without rush allows a real
evaluation of litellm (MIT license — dependency OK; maturity; overhead).

### Decision 3: cost-attribution is an invariant to preserve (do not regress)

**Choice**: any provider MUST allow `LLMService` to emit
`tokens{model,agent_id,direction}` and `cost{model,agent_id}`.

**Justification**: spec 27 is the product's FinOps foundation. Switching
transport cannot blind cost. **Known trap**: litellm has its own cost-tracking;
if we delegated to it, we'd lose the `agent_id` label. That's why the metric
stays in `LLMService`, fed by the `usage` the provider returns.

## Invariants

- Per-`agent_id`+`model` cost metrics NEVER regress (spec 27).
- AIP cost-attribution (Bedrock) continues: the AIP ARN goes in as `model`.
- `BedrockProvider` keeps behavior identical to the current `BedrockClient`.
- Resilience (circuit breaker/retry) belongs to `LLMService`, not duplicated.

## Licensing (clean-room)

- Implementation **from scratch**; no code copied from HolmesGPT/Aurora/etc.
- litellm (if adopted) = declared dependency (MIT), not a source copy.
- Conceptual inspiration (e.g. "pluggable provider") is free; expression is not copied.

## External dependencies (potential)

| Service/lib | Purpose | License |
|-------------|---------|---------|
| boto3 | Bedrock (current) | Apache-2.0 (already in use) |
| litellm (candidate) | multi-provider transport | MIT (verify on adoption) |
