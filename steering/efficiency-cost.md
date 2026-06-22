# Efficiency & Cost — Project Principle

Efficiency is a **first-class pillar** of AIgent-squad, at the same level as
security and correctness. It does not mean just speed/latency — it means
**cost per result**: tokens, LLM calls, and resources spent to deliver a useful
RCA/response.

> Golden rule: **the cheapest result that is still correct and secure wins.**
> Low latency that burns tokens needlessly is NOT efficient.

---

## Why this matters (not optional)

Bedrock charges **per token** (input + output, output ~5x more expensive). Every
unnecessary context token, every avoidable LLM round, every blind retry is
**money**. In a multi-agent system with fan-out + multi-round RCA, cost scales
fast. Cost efficiency is a product requirement, not late tuning.

## CRITICAL: think about cost BEFORE implementing

Before adding any path that invokes the LLM or builds context, ask:
1. **Does all this context need to go into the prompt?** (input tokens = recurring cost)
2. **Can a cheaper model solve it?** (classifier/triage ≠ synthesis)
3. **Can it be cached / short-circuited before the LLM?**
4. **How many LLM rounds does this trigger in the worst case?** (cap required)

---

## Mandatory practices

### Context (input tokens)
- **Truncate/summarize tool/adapter/MCP output before the prompt.** Large infra
  data (e.g. event lists, logs) explodes input tokens. HolmesGPT pattern:
  server-side filtering + spill-to-disk + summary transformer with a fast model.
  The `McpAdapter` already truncates per tool (4000 chars) — extend that
  discipline to all adapters.
- **Lazy injection.** Skills only enter the prompt when the query matches (done —
  spec 26). Same rule for any optional knowledge/context.
- **Bounded history.** Inject only the N relevant messages, not the whole session.

### Model (tiering)
- **Cheap model for cheap tasks.** Classifier/triage/routing should use a fast/
  cheap model (e.g. Haiku); synthesis/RCA use the expensive one (Sonnet/Opus).
  Do not use the premium model to decide routing. (See spec 11.)
- **Do not oversize output `max_tokens`** — caps output cost.

### Rounds and retries
- **Mandatory round cap** in every multi-round flow (RCA, fan-out). No cap =
  unbounded cost under failure. (See per-level limits in the ROADMAP.)
- **Retry with backoff, not blind.** A retry that re-invokes the LLM multiplies
  cost — only on transient errors, with a ceiling.
- **Anti-loop**: block identical repeated tool calls (HolmesGPT pattern
  `prevent_overly_repeated_tool_call`).

### Cache
- **Cache infra data** (deterministic, TTL) — not the LLM response (leaks across
  users, breaks multi-turn — see project.md).
- Evaluate **Bedrock prompt caching** for repeated system prompt + skills (large
  discount on cached tokens). (See spec 11.)

### Measurement (you can't optimize what you don't measure)
- **Cost/tokens per agent** already instrumented (`aigent.tokens.total`,
  `aigent.cost.estimated` labeled by `agent_id` — spec 27). Every feature that
  changes LLM usage patterns must observe the impact on these metrics.
- **Per user/session budget cap** (spec 14) protects against abuse AND cost.

---

## Trade-off with the other dimensions

Efficiency does **not** override security or correctness:
- Fail-closed security (spec 14) has a latency/evaluation cost — **accepted**.
- A correct RCA with 1 extra round > a cheap, wrong RCA.
- Priority order: **correct > secure > efficient > fast**. Efficiency comes
  before raw speed, but after correctness and security.

---

## Anti-patterns

- ❌ Injecting a raw adapter/MCP dump into the prompt without truncating/summarizing
- ❌ Using the premium model for classification/routing
- ❌ Multi-round flow without a round cap
- ❌ Blind retry that re-invokes the LLM without a ceiling
- ❌ Caching the LLM response per query (leaks + incorrect)
- ❌ Optimizing latency by burning tokens (that is not efficiency)
- ❌ Adding a feature that changes LLM usage without looking at `aigent.cost.estimated`
