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
| litellm (candidate) | multi-provider transport | MIT (verify on adoption) — **not adopted** (Decision 4) |
| httpx | OpenAI-compatible transport (Decision 4) | BSD-3-Clause (already pinned) |
| respx (tests only) | httpx mocking | BSD-3-Clause (already in the CI/dev test set) |

---

## Implementation decisions (2026-11-15 — Phase 1 + Phase 2 implemented)

The owner settled the open questions on 2026-11-15: the second provider is a
**hand-written httpx client for OpenAI-compatible `POST {base_url}/chat/completions`**
(no litellm, no new runtime dependency), provider selection is **global only**, and
the Bedrock Guardrail (L1) is **switched off automatically** for non-Bedrock
providers. The numbered decisions below record how that is built. Decisions 1–3
above still hold; Decision 2 is now resolved (hand-written, see Decision 4).

### Shape of the code

```
src/core/llm/
  types.py            LLMProvider (Protocol), LLMResult, LLMProviderError, error classes
  service.py          LLMService — the common layer (template method)
  openai_provider.py  OpenAICompatClient(LLMService) — httpx transport + format translation
  factory.py          build_llm_service() — LLM_PROVIDER selection + startup validation
  __init__.py         `llm` — the process-wide, provider-selected service
src/core/bedrock.py   BedrockClient(LLMService) — boto3 transport (behavior unchanged)
```

### Decision 4: hand-written OpenAI-compatible client (resolves Decision 2)

**Choice**: `OpenAICompatClient` speaks the Chat Completions wire format over
`httpx` (already a pinned dependency). A configurable `OPENAI_BASE_URL` + API key
covers OpenAI, OpenAI-compatible Azure endpoints, vLLM, Ollama, OpenRouter and a
LiteLLM proxy.

**Justification, in order of strength**:
1. One wire format reaches every target listed above; litellm would add a large
   transitive dependency tree for a single POST.
2. Cost/metric ownership stays in `LLMService` (Decision 3) — no second
   cost-tracking system to reconcile, `agent_id` label preserved.
3. Zero new runtime dependency; tests use `respx` (already in the CI test set).

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| No native Gemini/Cohere wire formats | Reachable through an OpenAI-compatible proxy (LiteLLM proxy, OpenRouter) |
| We own the format translation | ~250 lines, parity-tested against the Bedrock contract |

**When it would be wrong**: if we need ≥2 non-OpenAI wire formats natively, a
library starts to pay off.

### Decision 5: template method, not composition, for the common layer

**Choice**: `LLMService` is a base class that owns the policy (circuit breaker,
concurrency semaphore + queue metrics, retry/backoff with jitter, app-level
guardrail calls, token/cost/latency metrics, session token budget, language
directive, role→model resolution). Each provider subclasses it and implements
the transport hooks `_complete`, `_converse_transport` and `_classify_error`,
plus a `provider_name`. `LLMProvider` is the runtime-checkable `Protocol` naming
exactly that contract; parity tests assert both providers satisfy it.

**Justification**:
1. `BedrockClient`'s surface (`client`, `_cache_supported`, `circuit_breaker`,
   `max_retries`, static helpers, `__new__`-constructed test doubles) is
   load-bearing in ~10 test modules. Subclassing keeps every attribute where it
   already is — same tests, same expectations.
2. The policy is still written once: neither provider re-implements retry,
   breaker or accounting.

**Trade-offs accepted**:
| Cost | Reality |
|------|---------|
| Inheritance couples provider to the service lifecycle | Providers are leaf classes with 3 small hooks |
| Test patch targets for the moved policy names change module | `src.core.bedrock.<name>` → `src.core.llm.service.<name>` for accounting/guardrail/model-resolution names; assertions untouched |

### Decision 6: the canonical message formats stay the current ones

**Choice**: `invoke()` keeps taking Anthropic Messages-style messages (string or
`[{"type":"text"}]` content) and `converse()` keeps taking Bedrock Converse-style
messages + `toolConfig`, returning the existing normalized dict
(`stop_reason`, `content=[{type: text|tool_use}]`, `usage`). Non-Bedrock providers
translate **at their edge**:

| Internal (Converse) | Chat Completions |
|---|---|
| `system_prompt` | first `{"role":"system"}` message |
| user `[{"text"}]` / `{"guardContent"}` | user message, text joined |
| user `[{"toolResult":{toolUseId, content, status}}]` | `{"role":"tool","tool_call_id"}` (emitted before any user text of the same turn); `status=error` prefixed `[tool error]` |
| assistant `[{"text"}, {"toolUse"}]` | assistant `content` + `tool_calls[{id, type:function, function:{name, arguments: json}}]` |
| `toolConfig.tools[].toolSpec{name, description, inputSchema.json}` | `tools[{type:function, function:{name, description, parameters}}]` |
| `finish_reason` `tool_calls` / `stop` / `length` / `content_filter` | `stop_reason` `tool_use` / `end_turn` / `max_tokens` / `content_filtered` (a response carrying `tool_calls` is always `tool_use`, for servers that report `stop`) |
| `usage.prompt_tokens` / `completion_tokens` / `prompt_tokens_details.cached_tokens` | `input_tokens` / `output_tokens` / `cache_read_tokens` |

Malformed tool `arguments` JSON → `input={}` + a WARNING (the agentic loop is
fail-open; the tool's server rejects bad args). A response with no `choices` is
a non-retryable `LLMProviderError` (`malformed`).

### Decision 7: provider selection is global, defaulting to Bedrock

**Choice**: `LLM_PROVIDER=bedrock|openai`, default `bedrock`. With the default the
singleton `llm` IS `src.core.bedrock.bedrock` (one breaker, one semaphore) and
behavior is byte-identical to before. No per-role provider override. An unknown
value fails at import (Pydantic `Literal` validation).

Callers import `from src.core.llm import llm as bedrock`: the module-local name
`bedrock` is kept deliberately so the ~190 existing `patch("<caller>.bedrock")`
test targets keep working; the import source is the provider-neutral package.

### Decision 8: model ids per role — neutral vars with backward-compatible fallback

| Role | Bedrock (`LLM_PROVIDER=bedrock`) | OpenAI-compatible |
|---|---|---|
| agent | `LLM_MODEL_ID` → `BEDROCK_AGENT_MODEL_ID` | `LLM_MODEL_ID` (**required**) |
| classifier | `LLM_CLASSIFIER_MODEL_ID` → `BEDROCK_CLASSIFIER_MODEL_ID` | `LLM_CLASSIFIER_MODEL_ID` → `LLM_MODEL_ID` |
| synthesis | `LLM_SYNTHESIS_MODEL_ID` → `BEDROCK_SYNTHESIS_MODEL_ID` | `LLM_SYNTHESIS_MODEL_ID` → `LLM_MODEL_ID` |
| tier fast / standard / deep (spec 38) | `BEDROCK_TIER_*` (unchanged) | classifier model / agent model / agent model |

Startup validation (`validate_tier_models_at_startup`, called from the supervisor
lifespan) stays loud: for Bedrock it runs the existing tier-family check
unchanged; for OpenAI it refuses to boot without `OPENAI_BASE_URL` (http/https),
an API key (`OPENAI_API_KEY` or a readable `OPENAI_API_KEY_FILE`) and
`LLM_MODEL_ID`. The `us.` inference-profile rule is Bedrock-only. Collapsing the
three tiers to two models under OpenAI is a conscious simplification; per-tier
neutral vars are a follow-up if tier routing ever matters there.

### Decision 9: pricing for non-Bedrock models — configured or zero, never invented

**Choice**: under `openai`, `compute_cost` uses `LLM_PRICE_INPUT_PER_1M` /
`LLM_PRICE_OUTPUT_PER_1M` when both are set (cached input billed at the input
rate — overestimates, never under). When unset, cost is `0.0` and startup logs a
WARNING that `aigent.cost.estimated` will read zero. Bedrock pricing is unchanged.
The admission-time daily budget guard (`rate_limiter.estimate_cost`) keeps its
own pessimistic Sonnet-priced estimate, so it is NOT blinded by a zero price.

### Decision 10: the Bedrock Guardrail (L1) is off automatically for non-Bedrock providers — a stated exception

**Choice**: `GuardrailClient.enabled = GUARDRAIL_ENABLED and LLM_PROVIDER == "bedrock"`.
Under `openai`, every `guardrail.apply(...)` (ingress, per-stage, tool
args/results, output) is a no-op, and the Converse-side `guardrailConfig` is
Bedrock-transport-only so it is never built.

**Justification**: the guardrail is an AWS Bedrock API (`ApplyGuardrail`). Forcing
Bedrock access on a deployment that chose a non-Bedrock LLM defeats the provider
switch, and keeping it fail-closed would 403 every request.

**This is a deliberate exception to invariant 6 (fail-closed security)**, in the
same style as the canary F-005 exception, and it is never silent:
- startup WARNING (`llm_guardrail_l1_inactive`) naming the provider;
- `aigent.llm.guardrail_l1_active{provider}` = 0 (1 when active);
- `/ready` exposes `checks.llm = {ok: true, provider, guardrail_l1: active|inactive}`
  (informational, never fails readiness);
- documented in AGENTS.md invariant 6, `docs/SECURITY.md` and ADR-0009.

L2–L6 (InputScanner, output filter, canary, response-quality guard, read-only
posture) are provider-independent and run unchanged.

### Decision 11: errors, retries, timeouts, secrets

- **Retryable**: HTTP 429, 500/502/503/504, `httpx` timeouts and transport
  (connect) errors. Same budget as Bedrock (3 attempts, `1s·2^n + U(0,1)` jitter).
- **Non-retryable**: every other 4xx → `LLMProviderError` carrying only the status
  and the server's error `type`/`code` — never the request body or the key.
- On exhaustion the **original** exception is re-raised (Bedrock: the boto3
  `ClientError`, exactly as today), so existing error handling is untouched.
- **Timeouts** reuse `BEDROCK_READ_TIMEOUT_SECONDS` / `BEDROCK_CONNECT_TIMEOUT_SECONDS`
  (historical names; they bound the LLM call for either provider and must stay
  below the gateway job timeout). `BEDROCK_MAX_CONCURRENT` likewise bounds
  in-flight calls for either provider; the `aigent.bedrock.queue_*` metrics keep
  their names.
- **API key**: `OPENAI_API_KEY`, or `OPENAI_API_KEY_FILE` (file-mounted secret,
  preferred per 12-factor steering; wins when both are set). Never logged; error
  messages never include it. `OPENAI_API_KEY_HEADER` (default `Authorization` →
  `Bearer <key>`; e.g. `api-key` for Azure). `OPENAI_MAX_TOKENS_FIELD` (default
  `max_tokens`; `max_completion_tokens` for reasoning models). No hardcoded URL.

**Review hardening (2026-11-15, independent review + test author):**
- Server error `code`/`type` passes `safe_error_code`: sanitized, and replaced by
  `redacted` when it contains the loaded key (or a ≥8-char fragment of it) or looks
  like a credential (`sk-…`-style prefix, a ≥8-char letters+digits segment, a
  segment >32 chars). A leading `Bearer ` in the configured key is stripped.
- Plain `http://` with the key is refused at startup unless the host is local or
  in-cluster: `localhost`, `127.0.0.1`, `::1`, `*.svc`, `*.svc.cluster.local`,
  single-label names (compose/K8s service names).
- The httpx client uses `trust_env=False` (HTTP(S)_PROXY / NETRC not honored — the
  key goes exactly to `OPENAI_BASE_URL`) and does not follow redirects (3xx →
  `client_error`, code `redirect`). It is closed in the supervisor lifespan.
- A base URL with a query string is supported (`?api-version=` for Azure): the
  path gets `/chat/completions`, the query is kept.
- Missing `usage` → char-based estimate (`estimate_tokens`, input from the
  serialized request messages, output from the content) + one-shot WARNING
  `llm_usage_missing`, so the session budget and `MAX_LOOP_TOKENS` stay live.
- Tool calls without an `id` get `call_{i}_{hex8}` so parallel results correlate.
- `/ready` and the L1 metric report the enforced `guardrail.enabled`, not settings.
- Bedrock retry/failure logs carry `error_code` again (logs only, not a label).

### Decision 12: prompt caching is Bedrock-only

`cache_control` blocks are a Bedrock/Anthropic feature; the OpenAI client ignores
`use_cache` (no-op). If the server reports `prompt_tokens_details.cached_tokens`
it is passed as `cache_read_tokens` to `compute_cost` and logged.

### Out of scope (explicitly)

- **KB embeddings** (`src/core/kb/embedder.py`, Titan via Bedrock) — Bedrock-only;
  the KB is disabled by default. Enabling the KB with `LLM_PROVIDER=openai` still
  needs Bedrock access for embeddings.
- **Streaming token output** — none of the LLM calls stream tokens today; not added.
- **Per-role provider override** (e.g. Haiku on Bedrock + agents on OpenAI).
- **Live cost-attribution check against a real OpenAI-compatible endpoint** —
  verified with mocks only (no endpoint/credentials on the build machine).

### New metrics

| Metric | Type | Labels | Cardinality |
|---|---|---|---|
| `aigent.llm.provider_errors` | counter | `provider` (2), `error_class` (throttled / server_error / timeout / client_error / malformed) | ≤10 |
| `aigent.llm.guardrail_l1_active` | up-down counter (0/1) | `provider` (2) | 2 |

### Verification

- New `tests/test_llm_*.py`: OpenAI translation/usage/error mapping (respx),
  factory selection, model resolution + startup validation, guardrail auto-off,
  provider parity (the same contract run against `BedrockClient` with a mocked
  boto3 client and `OpenAICompatClient` with respx).
- Existing Bedrock suites pass with unchanged assertions (only the patch targets
  of the moved policy names are retargeted to `src.core.llm.service`).
