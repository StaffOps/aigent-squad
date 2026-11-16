# Tasks: LLM Provider Abstraction

Status: **in progress** — Phase 1 + Phase 2 implemented 2026-11-15 (owner decision:
hand-written OpenAI-compatible client, global `LLM_PROVIDER`, Bedrock Guardrail L1
auto-off for non-Bedrock). Remaining: independent test author, independent review,
live endpoint check.

## Phase 1 — Abstraction (safe refactor, valuable on its own)
- [x] Task 1: Define `LLMProvider` (Protocol) + `LLMResult` (text + usage + model)
      — `src/core/llm/types.py` (2026-11-15)
- [x] Task 2: `LLMService` (common layer): circuit breaker, semaphore, retry/backoff,
      guardrail calls, {model, agent_id, direction} metrics, token budget —
      `src/core/llm/service.py` (2026-11-15)
- [x] Task 3: `BedrockProvider` = `BedrockClient(LLMService)`, identical behavior;
      existing Bedrock suites pass with unchanged assertions (only the patch
      targets of the moved policy names retargeted to `src.core.llm.service`) (2026-11-15)
- [x] Task 4: Callers (GenericAgent, Classifier, synthesizer, RCA investigation,
      KB enricher/extractor, agentic loop + streaming loop, eval judge) import the
      provider-selected `src.core.llm.llm`; `LLM_PROVIDER` env selection (2026-11-15)
- [x] Task 5: Cost-attribution (spec 27) + AIP ARN preserved — model id passed
      through unchanged to the metric labels; covered by parity tests with mocks (2026-11-15)
- [x] Task 6: Tests (new `tests/test_llm_*.py`, suite ≥90%); ADR-0009 + ADR-001
      amendment note (2026-11-15)

## Phase 2 — Second provider
- [x] Task 7: Decide litellm vs hand-written — **hand-written httpx client**
      (owner decision 2026-11-15; design Decision 4)
- [x] Task 8: `OpenAICompatClient` — plain completions + agentic Converse tool loop
      translation, error/usage mapping, startup validation, L1 auto-off with
      WARNING + metric + `/ready` detail (2026-11-15)
- [x] Task 9: Parity tests — same contract, both providers (`tests/test_llm_parity.py`) (2026-11-15)

## Verification-independence gates
- [x] Task 10: Independent test author extends/re-derives tests against the contract
      (design Decisions 6, 8–11), not the implementation — 131 tests in
      `tests/test_llm_spec28_security_e2e.py` + `tests/test_llm_spec28_independent.py`
      (2026-11-15). Their one strict-xfail bug (API key echoed in a server error code)
      fixed the same day (`safe_error_code`), xfail removed.
- [x] Task 10b: Independent review (no blockers) — should-fix items applied 2026-11-15:
      usage-missing estimate fallback (`llm_usage_missing`), synthesized tool-call ids,
      L1 status reports the enforced `guardrail.enabled`, plain-http-to-remote-host
      refused at startup, Bedrock `error_code` restored in retry logs (+ nits:
      `Bearer ` strip, 3xx → `redirect`, query-string base URLs, `trust_env=False`,
      httpx client closed in the lifespan, explicit `allow_recovery`)
- [x] Task 11: Independent code review (no blockers; findings applied as Task 10b) —
      the "real server" refutation is folded into Task 12 below (2026-11-15)
- [x] Task 12: Live validation against a real OpenAI-compatible endpoint — done
      2026-11-15 on k8s01 (throwaway ns `aigent-squad-dev`, image
      `harbor.staffops.space/development/aigent-squad:0.5.0-dev-spec28`, chart 0.9.6,
      llama.cpp server CPU, Qwen2.5-1.5B-Instruct Q4_K_M, `--jinja`, 16k ctx). Proven:
      `invoke()` (classifier, aws/finops/devops, RCA synthesis + T13 ceiling) and
      `converse()` (kubernetes, incl. timeout→retry→success); 4xx (context overflow)
      classified `client_error`, not retried; `/query` 200 end-to-end; gateway
      `/v1/models` + `/v1/chat/completions` 200; `/ready` `checks.llm`
      {provider: openai, guardrail_l1: inactive}; startup `llm_guardrail_l1_inactive` +
      `llm_pricing_unset` warnings; `aigent_llm_guardrail_l1_active{provider="openai"} 0`;
      tokens labeled {agent_id, model, direction}; API key found in 0 log lines.
      **Not proven live**: a model-issued tool call executing through the allowlist (no
      MCP servers reachable from the test ns; covered by mocked tests only). Findings:
      llama.cpp puts the error type in `error.type` (`exceed_context_size_error`) but
      the log showed `error_code: None` → minor diagnosability follow-up (Task 16).
- [x] Task 16: Surface llama.cpp/OpenAI `error.type` as the sanitized `error_code` when
      `error.code` is numeric (diagnosability only; found in Task 12) — done 2026-11-16
      (`_error_code` prefers `type` when `code` is absent or numeric; symbolic codes still win).
      Root cause of the `None`: `OpenAICompatClient` never overrode the `LLMService._error_code`
      log hook, so the extracted code stayed on the exception and never reached the logs.

## Follow-ups (tracked)
- [x] Task 13: Helm chart support — `helm-charts` chart 0.9.6 (2026-11-15):
      `existingSecrets[]` (envFrom, no ESO needed) carries `OPENAI_API_KEY`; LLM env
      documented under `global.env`. Remaining: optional key-as-file mount
      (`OPENAI_API_KEY_FILE`) + egress NetworkPolicy to the endpoint (not needed for
      the live test; left for when networkPolicy is enabled with openai)
- [x] Task 14: Rename the caller-local alias `bedrock` → `llm` (+ ~190 test patch
      targets) — mechanical, separate change (BACKLOG B-31) — done 2026-11-16: 8 callers
      + `evals/runner.py` import `from src.core.llm import llm`; tests patch `"<caller>.llm"`
      (37 test files); no behavior change, full suite green
- [x] Task 15: `SecretStr` for secret settings repo-wide (`OPENAI_API_KEY`, tokens,
      Redis password) — BACKLOG B-32 — done 2026-11-16:
      7 fields → `SecretStr`; plaintext only via `config.reveal()` at the 7 use sites;
      `tests/test_b32_secret_settings.py` proves repr/dump masking

## Explicitly out of scope
- KB embeddings (Titan, Bedrock-only; KB disabled by default)
- Token streaming; per-role provider override; per-tier neutral model vars

## Notes
- **Clean-room**: implementation from scratch; no third-party code copied.
- **Not orchestration**: LLM transport only. LangGraph/Strands stay out (ADR-001).
