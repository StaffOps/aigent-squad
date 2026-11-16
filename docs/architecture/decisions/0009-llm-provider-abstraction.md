# ADR-0009: Pluggable LLM transport (Bedrock or any OpenAI-compatible API), and L1 off for non-Bedrock

| Field | Value |
|---|---|
| **Status** | accepted (implemented 2026-11-15; independent review + live endpoint validation pending — spec 28) |
| **Date** | 2026-11-15 |
| **Deciders** | Carlos Felipe Gomes |
| **Related to** | spec 28; **amends** `specs/ADR-001-bedrock-direct-vs-strands.md` for LLM *transport* only (orchestration stays framework-free); ADR-0004 (fail-closed security — this ADR adds a stated exception); ADR-0008 (agentic tool loop — covered by both providers) |

## Context

Every LLM call went through `src/core/bedrock.py::BedrockClient` (boto3,
Anthropic-on-Bedrock format). The platform has to run where Bedrock is not
available or not wanted, against any OpenAI / Chat-Completions-compatible API
(OpenAI, Azure OpenAI-compatible endpoints, vLLM, Ollama, OpenRouter, LiteLLM proxy).

## Decision

1. **Common layer + pluggable transport.** `LLMService` (`src/core/llm/service.py`)
   owns circuit breaker, concurrency semaphore, retry/backoff, the app-level
   guardrail calls, token/cost/latency metrics `{model, agent_id, direction}`
   and the session token budget. Providers implement only transport hooks
   (`LLMProvider` Protocol). `BedrockClient` = `LLMService` + boto3 transport,
   behavior unchanged.
2. **Second provider is our own httpx client** for
   `POST {OPENAI_BASE_URL}/chat/completions`, covering plain completions and the
   agentic Converse tool loop (format translation at the provider edge). No
   litellm, no new runtime dependency.
3. **Selection is global**: `LLM_PROVIDER=bedrock|openai`, default `bedrock`.
   No per-role provider override.
4. **The Bedrock Guardrail (spec 14 L1) is switched off automatically when
   `LLM_PROVIDER != bedrock`.** This is a **deliberate exception to the
   fail-closed rule (ADR-0004, AGENTS.md invariant 6)**, like the canary F-005
   exception. Points that become no-ops: ingress input guard, per-stage
   INPUT/OUTPUT guards in `invoke()`/`converse()`, tool-argument and
   tool-result guards in the agentic loop, and the Converse-side
   `guardrailConfig` + guardContent tagging (Bedrock transport only). It is
   never silent: startup WARNING `llm_guardrail_l1_inactive`,
   `aigent.llm.guardrail_l1_active{provider}=0`, and `/ready` reports
   `checks.llm.guardrail_l1: inactive`. L2–L6 (InputScanner, output filter,
   canary, response-quality guard, read-only posture) are unchanged.

## Consequences

- **Positive**: runs without Bedrock; cost attribution per `agent_id`+`model`
  preserved for both providers; agentic loop budgets/allowlist untouched
  (they live in the provider-neutral loop).
- **Negative**: with `openai`, prompt-injection defense loses its independent
  model-side layer (L1) — the remaining defense is L2 heuristics + L4/L5 output
  checks + the read-only boundary. Operators who need L1 must stay on Bedrock.
- **Negative**: non-Bedrock cost reads 0 unless `LLM_PRICE_*` is configured
  (startup WARNING); the admission daily budget keeps its own pessimistic estimate.
- **Out of scope**: KB embeddings (Titan, Bedrock-only), token streaming,
  per-role providers.

## Alternatives considered

- **litellm in the core** — rejected: large dependency for one wire format and
  a second cost-tracking system that drops our `agent_id` label (spec 28 Decision 3).
- **Keep L1 fail-closed under `openai`** — rejected: L1 is an AWS Bedrock API;
  it would either demand Bedrock access (defeating the switch) or 403 every request.

Full rationale: `specs/28-llm-provider-abstraction/design.md` (Decisions 4–12).
